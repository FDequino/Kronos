# Kronos Portfolio — Contexto técnico para migración del motor predictivo

**Objetivo:** migrar el motor de inferencia actual (Kronos, autoregresivo con PyTorch) hacia Chronos-Bolt/Moirai servido vía FastAPI con soporte MPS.

## 1. Estructura de carpetas (resumida)

```
Kronos/
├── .env
├── requirements.txt
├── README.md
├── PRODUCT.md / DESIGN.md          # contexto de producto/diseño (Impeccable)
│
├── model/                          # ── Motor predictivo Kronos (a migrar) ──
│   ├── __init__.py
│   ├── kronos.py                   # KronosTokenizer, Kronos, KronosPredictor (inferencia)
│   ├── module.py                   # bloques Transformer/BSQuantizer internos (arquitectura, 570 líneas — no incluido abajo, es interno del modelo viejo)
│   └── runtime.py                  # selección de device (cuda/mps/cpu) + singleton del predictor cargado
│
├── portfolio/                      # ── Backend de la app (Flask) ──
│   ├── db.py                       # acceso SQLite (instrumentos, price_history, forecasts, etc.)
│   ├── schema.sql                  # schema completo de la base
│   ├── fetchers.py                 # OHLCV + cotizaciones en vivo (data912/BNA/dolarapi/binance/yfinance)
│   ├── forecasting.py              # pipeline: OHLCV -> input del modelo -> inferencia -> señal
│   ├── forecast_worker.py          # cola/worker en background que ejecuta forecasting.py
│   ├── signals.py                  # traduce la predicción a señal comprar/mantener/vender
│   ├── indicators.py                # indicadores técnicos clásicos (RSI/SMA/MACD), independiente de Kronos
│   ├── accuracy.py                  # MAPE/acierto direccional/calibración de pronósticos pasados
│   ├── routes.py                   # ~1500 líneas, todos los endpoints REST del portfolio
│   ├── quote_worker.py             # polling de cotizaciones en vivo en horario de mercado
│   ├── projection.py / risk.py / history.py / benchmarks.py / xirr.py / ...  # analítica de cartera (no tocan el modelo)
│   └── portfolio.db                # SQLite real (datos del usuario)
│
├── webui/                          # ── Flask app (UI + API) ──
│   ├── app.py                      # entrypoint Flask; además expone un demo standalone de predicción (/api/predict)
│   ├── nav.py
│   ├── templates/                  # Jinja (base.html, portfolio.html, index.html)
│   └── static/
│       ├── js/portfolio/           # frontend del portfolio (vanilla JS, ES modules)
│       ├── js/kronos/              # frontend del demo standalone
│       └── css/
│
├── finetune/ , finetune_csv/        # scripts de fine-tuning del tokenizer/predictor (no runtime)
├── examples/                        # scripts sueltos de ejemplo/backtest (no runtime)
├── tests/
│   ├── test_kronos_ars_bias.py     # reproduce el sesgo de normalización por ventana (ARS)
│   └── test_kronos_regression.py
│
├── data/                            # CSVs de ejemplo para el demo standalone
└── datos_broker/                    # excels/capturas de reconciliación manual (no código)
```

Omitidos: `.venv/`, `.git/`, `__pycache__/`, `webui/prediction_results/` (29 JSONs de corridas pasadas del demo), `examples/yuce/` (imágenes/reports de ejemplo), `datos_broker/*.png` — no aportan al motor predictivo.

## 2. Selección de device (CPU/CUDA/MPS) — `model/runtime.py`

Este archivo es el punto de entrada para cargar el modelo y elegir el device. Ya soporta MPS hoy.

```python
"""Flask-independent runtime state for the loaded Kronos model.

Lets portfolio/forecast_worker.py reuse whatever model is loaded without
importing anything from webui/, and without reloading weights per forecast.
"""
import torch

try:
    from model import Kronos, KronosTokenizer, KronosPredictor
    MODEL_AVAILABLE = True
except ImportError:
    MODEL_AVAILABLE = False

AVAILABLE_MODELS = {
    'kronos-mini': {
        'name': 'Kronos-mini',
        'model_id': 'NeoQuasar/Kronos-mini',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-2k',
        'context_length': 2048,
        'params': '4.1M',
        'description': 'Lightweight model, suitable for fast prediction',
    },
    'kronos-small': {
        'name': 'Kronos-small',
        'model_id': 'NeoQuasar/Kronos-small',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-base',
        'context_length': 512,
        'params': '24.7M',
        'description': 'Small model, balanced performance and speed',
    },
    'kronos-base': {
        'name': 'Kronos-base',
        'model_id': 'NeoQuasar/Kronos-base',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-base',
        'context_length': 512,
        'params': '102.3M',
        'description': 'Base model, provides better prediction quality',
    },
}

_predictor = None
_current_model_key = None


def pick_device():
    if torch.cuda.is_available():
        return "cuda:0"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(model_key, device=None):
    """Loads (or reloads) the given model and makes it the active one."""
    global _predictor, _current_model_key

    if not MODEL_AVAILABLE:
        raise RuntimeError("Kronos model library not available")
    if model_key not in AVAILABLE_MODELS:
        raise ValueError(f"Unsupported model: {model_key}")

    config = AVAILABLE_MODELS[model_key]
    device = device or pick_device()

    tokenizer = KronosTokenizer.from_pretrained(config["tokenizer_id"])
    model = Kronos.from_pretrained(config["model_id"])
    _predictor = KronosPredictor(model, tokenizer, device=device, max_context=config["context_length"])
    _current_model_key = model_key
    return config


def ensure_loaded(model_key="kronos-base", device=None):
    """Lazily loads model_key if nothing (or a different model) is loaded yet."""
    if _predictor is None or _current_model_key != model_key:
        load_model(model_key, device)
    return _predictor


def get_predictor():
    return _predictor


def status():
    if not MODEL_AVAILABLE:
        return {
            "available": False,
            "loaded": False,
            "message": "Kronos model library not available, please install related dependencies",
        }
    if _predictor is None:
        return {"available": True, "loaded": False, "message": "Kronos model available but not loaded"}
    return {
        "available": True,
        "loaded": True,
        "message": "Kronos model loaded and available",
        "current_model": {
            "name": _predictor.model.__class__.__name__,
            "device": str(next(_predictor.model.parameters()).device),
        },
    }
```

## 3. Motor de inferencia — `model/kronos.py` (completo)

Este es el wrapper que hay que reemplazar por el cliente de Chronos-Bolt/Moirai. `KronosTokenizer`/`Kronos` (clases `nn.Module`) son la arquitectura interna específica de Kronos — se descartan enteras en la migración. Lo que sí hay que replicar en la nueva implementación es el **contrato** de `KronosPredictor`: `predict()`, `predict_samples()`, `predict_batch()`, y cómo arma `x`/`x_stamp`/`y_stamp` a partir de un DataFrame OHLCV (`_prepare_inputs`).

```python
import numpy as np
import pandas as pd
import torch
from huggingface_hub import PyTorchModelHubMixin
import sys

from tqdm import trange

sys.path.append("../")
from model.module import *


class KronosTokenizer(nn.Module, PyTorchModelHubMixin):
    """
    KronosTokenizer module for tokenizing input data using a hybrid quantization approach.

    This tokenizer utilizes a combination of encoder and decoder Transformer blocks
    along with the Binary Spherical Quantization (BSQuantizer) to compress and decompress input data.

    Args:
           d_in (int): Input dimension.
           d_model (int): Model dimension.
           n_heads (int): Number of attention heads.
           ff_dim (int): Feed-forward dimension.
           n_enc_layers (int): Number of encoder layers.
           n_dec_layers (int): Number of decoder layers.
           ffn_dropout_p (float): Dropout probability for feed-forward networks.
           attn_dropout_p (float): Dropout probability for attention mechanisms.
           resid_dropout_p (float): Dropout probability for residual connections.
           s1_bits (int): Number of bits for the pre token in BSQuantizer.
           s2_bits (int): Number of bits for the post token in BSQuantizer.
           beta (float): Beta parameter for BSQuantizer.
           gamma0 (float): Gamma0 parameter for BSQuantizer.
           gamma (float): Gamma parameter for BSQuantizer.
           zeta (float): Zeta parameter for BSQuantizer.
           group_size (int): Group size parameter for BSQuantizer.

    """

    def __init__(self, d_in, d_model, n_heads, ff_dim, n_enc_layers, n_dec_layers, ffn_dropout_p, attn_dropout_p, resid_dropout_p, s1_bits, s2_bits, beta, gamma0, gamma, zeta, group_size):

        super().__init__()
        self.d_in = d_in
        self.d_model = d_model
        self.n_heads = n_heads
        self.ff_dim = ff_dim
        self.enc_layers = n_enc_layers
        self.dec_layers = n_dec_layers
        self.ffn_dropout_p = ffn_dropout_p
        self.attn_dropout_p = attn_dropout_p
        self.resid_dropout_p = resid_dropout_p

        self.s1_bits = s1_bits
        self.s2_bits = s2_bits
        self.codebook_dim = s1_bits + s2_bits # Total dimension of the codebook after quantization
        self.embed = nn.Linear(self.d_in, self.d_model)
        self.head = nn.Linear(self.d_model, self.d_in)

        # Encoder Transformer Blocks
        self.encoder = nn.ModuleList([
            TransformerBlock(self.d_model, self.n_heads, self.ff_dim, self.ffn_dropout_p, self.attn_dropout_p, self.resid_dropout_p)
            for _ in range(self.enc_layers - 1)
        ])
        # Decoder Transformer Blocks
        self.decoder = nn.ModuleList([
            TransformerBlock(self.d_model, self.n_heads, self.ff_dim, self.ffn_dropout_p, self.attn_dropout_p, self.resid_dropout_p)
            for _ in range(self.dec_layers - 1)
        ])
        self.quant_embed = nn.Linear(in_features=self.d_model, out_features=self.codebook_dim) # Linear layer before quantization
        self.post_quant_embed_pre = nn.Linear(in_features=self.s1_bits, out_features=self.d_model) # Linear layer after quantization (pre part - s1 bits)
        self.post_quant_embed = nn.Linear(in_features=self.codebook_dim, out_features=self.d_model) # Linear layer after quantization (full codebook)
        self.tokenizer = BSQuantizer(self.s1_bits, self.s2_bits, beta, gamma0, gamma, zeta, group_size) # BSQuantizer module

    def forward(self, x):
        """
        Forward pass of the KronosTokenizer.

        Args:
            x (torch.Tensor): Input tensor of shape (batch_size, seq_len, d_in).

        Returns:
            tuple: A tuple containing:
                - tuple: (z_pre, z) - Reconstructed outputs from decoder with s1_bits and full codebook respectively,
                         both of shape (batch_size, seq_len, d_in).
                - torch.Tensor: bsq_loss - Loss from the BSQuantizer.
                - torch.Tensor: quantized - Quantized representation from BSQuantizer.
                - torch.Tensor: z_indices - Indices from the BSQuantizer.
        """
        z = self.embed(x)

        for layer in self.encoder:
            z = layer(z)

        z = self.quant_embed(z) # (B, T, codebook)

        bsq_loss, quantized, z_indices = self.tokenizer(z)

        quantized_pre = quantized[:, :, :self.s1_bits] # Extract the first part of quantized representation (s1_bits)
        z_pre = self.post_quant_embed_pre(quantized_pre)

        z = self.post_quant_embed(quantized)

        # Decoder layers (for pre part - s1 bits)
        for layer in self.decoder:
            z_pre = layer(z_pre)
        z_pre = self.head(z_pre)

        # Decoder layers (for full codebook)
        for layer in self.decoder:
            z = layer(z)
        z = self.head(z)

        return (z_pre, z), bsq_loss, quantized, z_indices

    def indices_to_bits(self, x, half=False):
        """
        Converts indices to bit representations and scales them.

        Args:
            x (torch.Tensor): Indices tensor.
            half (bool, optional): Whether to process only half of the codebook dimension. Defaults to False.

        Returns:
            torch.Tensor: Bit representation tensor.
        """
        if half:
            x1 = x[0] # Assuming x is a tuple of indices if half is True
            x2 = x[1]
            mask = 2 ** torch.arange(self.codebook_dim//2, device=x1.device, dtype=torch.long) # Create a mask for bit extraction
            x1 = (x1.unsqueeze(-1) & mask) != 0 # Extract bits for the first half
            x2 = (x2.unsqueeze(-1) & mask) != 0 # Extract bits for the second half
            x = torch.cat([x1, x2], dim=-1) # Concatenate the bit representations
        else:
            mask = 2 ** torch.arange(self.codebook_dim, device=x.device, dtype=torch.long) # Create a mask for bit extraction
            x = (x.unsqueeze(-1) & mask) != 0 # Extract bits

        x = x.float() * 2 - 1 # Convert boolean to bipolar (-1, 1)
        q_scale = 1. / (self.codebook_dim ** 0.5) # Scaling factor
        x = x * q_scale
        return x

    def encode(self, x, half=False):
        """
        Encodes the input data into quantized indices.

        Args:
            x (torch.Tensor): Input tensor of shape (batch_size, seq_len, d_in).
            half (bool, optional): Whether to use half quantization in BSQuantizer. Defaults to False.

        Returns:
            torch.Tensor: Quantized indices from BSQuantizer.
        """
        z = self.embed(x)
        for layer in self.encoder:
            z = layer(z)
        z = self.quant_embed(z)

        bsq_loss, quantized, z_indices = self.tokenizer(z, half=half, collect_metrics=False)
        return z_indices

    def decode(self, x, half=False):
        """
        Decodes quantized indices back to the input data space.

        Args:
            x (torch.Tensor): Quantized indices tensor.
            half (bool, optional): Whether the indices were generated with half quantization. Defaults to False.

        Returns:
            torch.Tensor: Reconstructed output tensor of shape (batch_size, seq_len, d_in).
        """
        quantized = self.indices_to_bits(x, half)
        z = self.post_quant_embed(quantized)
        for layer in self.decoder:
            z = layer(z)
        z = self.head(z)
        return z


class Kronos(nn.Module, PyTorchModelHubMixin):
    """
    Kronos Model.

    Args:
        s1_bits (int): Number of bits for pre tokens.
        s2_bits (int): Number of bits for post tokens.
        n_layers (int): Number of Transformer blocks.
        d_model (int): Dimension of the model's embeddings and hidden states.
        n_heads (int): Number of attention heads in the MultiheadAttention layers.
        ff_dim (int): Dimension of the feedforward network in the Transformer blocks.
        ffn_dropout_p (float): Dropout probability for the feedforward network.
        attn_dropout_p (float): Dropout probability for the attention layers.
        resid_dropout_p (float): Dropout probability for residual connections.
        token_dropout_p (float): Dropout probability for token embeddings.
        learn_te (bool): Whether to use learnable temporal embeddings.
    """

    def __init__(self, s1_bits, s2_bits, n_layers, d_model, n_heads, ff_dim, ffn_dropout_p, attn_dropout_p, resid_dropout_p, token_dropout_p, learn_te):
        super().__init__()
        self.s1_bits = s1_bits
        self.s2_bits = s2_bits
        self.n_layers = n_layers
        self.d_model = d_model
        self.n_heads = n_heads
        self.learn_te = learn_te
        self.ff_dim = ff_dim
        self.ffn_dropout_p = ffn_dropout_p
        self.attn_dropout_p = attn_dropout_p
        self.resid_dropout_p = resid_dropout_p
        self.token_dropout_p = token_dropout_p

        self.s1_vocab_size = 2 ** self.s1_bits
        self.token_drop = nn.Dropout(self.token_dropout_p)
        self.embedding = HierarchicalEmbedding(self.s1_bits, self.s2_bits, self.d_model)
        self.time_emb = TemporalEmbedding(self.d_model, self.learn_te)
        self.transformer = nn.ModuleList([
            TransformerBlock(self.d_model, self.n_heads, self.ff_dim, self.ffn_dropout_p, self.attn_dropout_p, self.resid_dropout_p)
            for _ in range(self.n_layers)
        ])
        self.norm = RMSNorm(self.d_model)
        self.dep_layer = DependencyAwareLayer(self.d_model)
        self.head = DualHead(self.s1_bits, self.s2_bits, self.d_model)
        self.apply(self._init_weights)

    def _init_weights(self, module):

        if isinstance(module, nn.Linear):
            nn.init.xavier_normal_(module.weight)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0, std=self.embedding.d_model ** -0.5)
        elif isinstance(module, nn.LayerNorm):
            nn.init.ones_(module.weight)
            nn.init.zeros_(module.bias)
        elif isinstance(module, RMSNorm):
            nn.init.ones_(module.weight)

    def forward(self, s1_ids, s2_ids, stamp=None, padding_mask=None, use_teacher_forcing=False, s1_targets=None):
        """
        Args:
            s1_ids (torch.Tensor): Input tensor of s1 token IDs. Shape: [batch_size, seq_len]
            s2_ids (torch.Tensor): Input tensor of s2 token IDs. Shape: [batch_size, seq_len]
            stamp (torch.Tensor, optional): Temporal stamp tensor. Shape: [batch_size, seq_len]. Defaults to None.
            padding_mask (torch.Tensor, optional): Mask for padding tokens. Shape: [batch_size, seq_len]. Defaults to None.
            use_teacher_forcing (bool, optional): Whether to use teacher forcing for s1 decoding. Defaults to False.
            s1_targets (torch.Tensor, optional): Target s1 token IDs for teacher forcing. Shape: [batch_size, seq_len]. Defaults to None.

        Returns:
            Tuple[torch.Tensor, torch.Tensor]:
                - s1 logits: Logits for s1 token predictions. Shape: [batch_size, seq_len, s1_vocab_size]
                - s2_logits: Logits for s2 token predictions, conditioned on s1. Shape: [batch_size, seq_len, s2_vocab_size]
        """
        x = self.embedding([s1_ids, s2_ids])
        if stamp is not None:
            time_embedding = self.time_emb(stamp)
            x = x + time_embedding
        x = self.token_drop(x)

        for layer in self.transformer:
            x = layer(x, key_padding_mask=padding_mask)

        x = self.norm(x)

        s1_logits = self.head(x)

        if use_teacher_forcing:
            sibling_embed = self.embedding.emb_s1(s1_targets)
        else:
            s1_probs = F.softmax(s1_logits.detach(), dim=-1)
            sample_s1_ids = torch.multinomial(s1_probs.view(-1, self.s1_vocab_size), 1).view(s1_ids.shape)
            sibling_embed = self.embedding.emb_s1(sample_s1_ids)

        x2 = self.dep_layer(x, sibling_embed, key_padding_mask=padding_mask) # Dependency Aware Layer: Condition on s1 embeddings
        s2_logits = self.head.cond_forward(x2)
        return s1_logits, s2_logits

    def decode_s1(self, s1_ids, s2_ids, stamp=None, padding_mask=None):
        """
        Decodes only the s1 tokens.

        This method performs a forward pass to predict only s1 tokens. It returns the s1 logits
        and the context representation from the Transformer, which can be used for subsequent s2 decoding.

        Args:
            s1_ids (torch.Tensor): Input tensor of s1 token IDs. Shape: [batch_size, seq_len]
            s2_ids (torch.Tensor): Input tensor of s2 token IDs. Shape: [batch_size, seq_len]
            stamp (torch.Tensor, optional): Temporal stamp tensor. Shape: [batch_size, seq_len]. Defaults to None.
            padding_mask (torch.Tensor, optional): Mask for padding tokens. Shape: [batch_size, seq_len]. Defaults to None.

        Returns:
            Tuple[torch.Tensor, torch.Tensor]:
                - s1 logits: Logits for s1 token predictions. Shape: [batch_size, seq_len, s1_vocab_size]
                - context: Context representation from the Transformer. Shape: [batch_size, seq_len, d_model]
        """
        x = self.embedding([s1_ids, s2_ids])
        if stamp is not None:
            time_embedding = self.time_emb(stamp)
            x = x + time_embedding
        x = self.token_drop(x)

        for layer in self.transformer:
            x = layer(x, key_padding_mask=padding_mask)

        x = self.norm(x)

        s1_logits = self.head(x)
        return s1_logits, x

    def decode_s2(self, context, s1_ids, padding_mask=None):
        """
        Decodes the s2 tokens, conditioned on the context and s1 tokens.

        This method decodes s2 tokens based on a pre-computed context representation (typically from `decode_s1`)
        and the s1 token IDs. It uses the dependency-aware layer and the conditional s2 head to predict s2 tokens.

        Args:
            context (torch.Tensor): Context representation from the transformer (output of decode_s1).
                                     Shape: [batch_size, seq_len, d_model]
            s1_ids (torch.Tensor): Input tensor of s1 token IDs. Shape: [batch_size, seq_len]
            padding_mask (torch.Tensor, optional): Mask for padding tokens. Shape: [batch_size, seq_len]. Defaults to None.

        Returns:
            torch.Tensor: s2 logits. Shape: [batch_size, seq_len, s2_vocab_size]
        """
        sibling_embed = self.embedding.emb_s1(s1_ids)
        x2 = self.dep_layer(context, sibling_embed, key_padding_mask=padding_mask)
        return self.head.cond_forward(x2)


def top_k_top_p_filtering(
        logits,
        top_k: int = 0,
        top_p: float = 1.0,
        filter_value: float = -float("Inf"),
        min_tokens_to_keep: int = 1,
):
    """Filter a distribution of logits using top-k and/or nucleus (top-p) filtering
    Args:
        logits: logits distribution shape (batch size, vocabulary size)
        if top_k > 0: keep only top k tokens with highest probability (top-k filtering).
        if top_p < 1.0: keep the top tokens with cumulative probability >= top_p (nucleus filtering).
            Nucleus filtering is described in Holtzman et al. (http://arxiv.org/abs/1904.09751)
        Make sure we keep at least min_tokens_to_keep per batch example in the output
    From: https://gist.github.com/thomwolf/1a5a29f6962089e871b94cbd09daf317
    """
    if top_k > 0:
        top_k = min(max(top_k, min_tokens_to_keep), logits.size(-1))  # Safety check
        # Remove all tokens with a probability less than the last token of the top-k
        indices_to_remove = logits < torch.topk(logits, top_k)[0][..., -1, None]
        logits[indices_to_remove] = filter_value
        return logits

    if top_p < 1.0:
        sorted_logits, sorted_indices = torch.sort(logits, descending=True)
        cumulative_probs = torch.cumsum(F.softmax(sorted_logits, dim=-1), dim=-1)

        # Remove tokens with cumulative probability above the threshold (token with 0 are kept)
        sorted_indices_to_remove = cumulative_probs > top_p
        if min_tokens_to_keep > 1:
            # Keep at least min_tokens_to_keep (set to min_tokens_to_keep-1 because we add the first one below)
            sorted_indices_to_remove[..., :min_tokens_to_keep] = 0
        # Shift the indices to the right to keep also the first token above the threshold
        sorted_indices_to_remove[..., 1:] = sorted_indices_to_remove[..., :-1].clone()
        sorted_indices_to_remove[..., 0] = 0

        # scatter sorted tensors to original indexing
        indices_to_remove = sorted_indices_to_remove.scatter(1, sorted_indices, sorted_indices_to_remove)
        logits[indices_to_remove] = filter_value
        return logits


def sample_from_logits(logits, temperature=1.0, top_k=None, top_p=None, sample_logits=True):
    logits = logits / temperature
    if top_k is not None or top_p is not None:
        if top_k > 0 or top_p < 1.0:
            logits = top_k_top_p_filtering(logits, top_k=top_k, top_p=top_p)

    probs = F.softmax(logits, dim=-1)

    if not sample_logits:
        _, x = torch.topk(probs, k=1, dim=-1)
    else:
        x = torch.multinomial(probs, num_samples=1)

    return x


def auto_regressive_inference(tokenizer, model, x, x_stamp, y_stamp, max_context, pred_len, clip=5, T=1.0, top_k=0, top_p=0.99, sample_count=5, verbose=False, return_samples=False):
    with torch.no_grad():
        x = torch.clip(x, -clip, clip)

        device = x.device
        x = x.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(-1, x.size(1), x.size(2)).to(device)
        x_stamp = x_stamp.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(-1, x_stamp.size(1), x_stamp.size(2)).to(device)
        y_stamp = y_stamp.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(-1, y_stamp.size(1), y_stamp.size(2)).to(device)

        x_token = tokenizer.encode(x, half=True)
        
        initial_seq_len = x.size(1)
        batch_size = x_token[0].size(0)
        total_seq_len = initial_seq_len + pred_len
        full_stamp = torch.cat([x_stamp, y_stamp], dim=1)

        generated_pre = x_token[0].new_empty(batch_size, pred_len)
        generated_post = x_token[1].new_empty(batch_size, pred_len)

        pre_buffer = x_token[0].new_zeros(batch_size, max_context)
        post_buffer = x_token[1].new_zeros(batch_size, max_context)
        buffer_len = min(initial_seq_len, max_context)
        if buffer_len > 0:
            start_idx = max(0, initial_seq_len - max_context)
            pre_buffer[:, :buffer_len] = x_token[0][:, start_idx:start_idx + buffer_len]
            post_buffer[:, :buffer_len] = x_token[1][:, start_idx:start_idx + buffer_len]

        if verbose:
            ran = trange
        else:
            ran = range
        for i in ran(pred_len):
            current_seq_len = initial_seq_len + i
            window_len = min(current_seq_len, max_context)

            if current_seq_len <= max_context:
                input_tokens = [
                    pre_buffer[:, :window_len],
                    post_buffer[:, :window_len]
                ]
            else:
                input_tokens = [pre_buffer, post_buffer]

            context_end = current_seq_len
            context_start = max(0, context_end - max_context)
            current_stamp = full_stamp[:, context_start:context_end, :].contiguous()

            s1_logits, context = model.decode_s1(input_tokens[0], input_tokens[1], current_stamp)
            s1_logits = s1_logits[:, -1, :]
            sample_pre = sample_from_logits(s1_logits, temperature=T, top_k=top_k, top_p=top_p, sample_logits=True)

            s2_logits = model.decode_s2(context, sample_pre)
            s2_logits = s2_logits[:, -1, :]
            sample_post = sample_from_logits(s2_logits, temperature=T, top_k=top_k, top_p=top_p, sample_logits=True)

            generated_pre[:, i] = sample_pre.squeeze(-1)
            generated_post[:, i] = sample_post.squeeze(-1)

            if current_seq_len < max_context:
                pre_buffer[:, current_seq_len] = sample_pre.squeeze(-1)
                post_buffer[:, current_seq_len] = sample_post.squeeze(-1)
            else:
                pre_buffer.copy_(torch.roll(pre_buffer, shifts=-1, dims=1))
                post_buffer.copy_(torch.roll(post_buffer, shifts=-1, dims=1))
                pre_buffer[:, -1] = sample_pre.squeeze(-1)
                post_buffer[:, -1] = sample_post.squeeze(-1)

        full_pre = torch.cat([x_token[0], generated_pre], dim=1)
        full_post = torch.cat([x_token[1], generated_post], dim=1)

        context_start = max(0, total_seq_len - max_context)
        input_tokens = [
            full_pre[:, context_start:total_seq_len].contiguous(),
            full_post[:, context_start:total_seq_len].contiguous()
        ]
        z = tokenizer.decode(input_tokens, half=True)
        z = z.reshape(-1, sample_count, z.size(1), z.size(2))
        preds = z.cpu().numpy()
        if return_samples:
            return preds

        return np.mean(preds, axis=1)


def calc_time_stamps(x_timestamp):
    time_df = pd.DataFrame()
    time_df['minute'] = x_timestamp.dt.minute
    time_df['hour'] = x_timestamp.dt.hour
    time_df['weekday'] = x_timestamp.dt.weekday
    time_df['day'] = x_timestamp.dt.day
    time_df['month'] = x_timestamp.dt.month
    return time_df


class KronosPredictor:

    def __init__(self, model, tokenizer, device=None, max_context=512, clip=5):
        self.tokenizer = tokenizer
        self.model = model
        self.max_context = max_context
        self.clip = clip
        self.price_cols = ['open', 'high', 'low', 'close']
        self.vol_col = 'volume'
        self.amt_vol = 'amount'
        self.time_cols = ['minute', 'hour', 'weekday', 'day', 'month']
        
        # Auto-detect device if not specified
        if device is None:
            if torch.cuda.is_available():
                device = "cuda:0"
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        
        self.device = device

        self.tokenizer = self.tokenizer.to(self.device)
        self.model = self.model.to(self.device)

    def generate(self, x, x_stamp, y_stamp, pred_len, T, top_k, top_p, sample_count, verbose, return_samples=False):

        x_tensor = torch.from_numpy(np.array(x).astype(np.float32)).to(self.device)
        x_stamp_tensor = torch.from_numpy(np.array(x_stamp).astype(np.float32)).to(self.device)
        y_stamp_tensor = torch.from_numpy(np.array(y_stamp).astype(np.float32)).to(self.device)

        preds = auto_regressive_inference(self.tokenizer, self.model, x_tensor, x_stamp_tensor, y_stamp_tensor, self.max_context, pred_len,
                                          self.clip, T, top_k, top_p, sample_count, verbose, return_samples=return_samples)
        if return_samples:
            return preds[:, :, -pred_len:, :]
        return preds[:, -pred_len:, :]

    def _prepare_inputs(self, df, x_timestamp, y_timestamp):
        if not isinstance(df, pd.DataFrame):
            raise ValueError("Input must be a pandas DataFrame.")

        if not all(col in df.columns for col in self.price_cols):
            raise ValueError(f"Price columns {self.price_cols} not found in DataFrame.")

        df = df.copy()
        if self.vol_col not in df.columns:
            df[self.vol_col] = 0.0  # Fill missing volume with zeros
            df[self.amt_vol] = 0.0  # Fill missing amount with zeros
        if self.amt_vol not in df.columns and self.vol_col in df.columns:
            df[self.amt_vol] = df[self.vol_col] * df[self.price_cols].mean(axis=1)

        if df[self.price_cols + [self.vol_col, self.amt_vol]].isnull().values.any():
            raise ValueError("Input DataFrame contains NaN values in price or volume columns.")

        x_time_df = calc_time_stamps(x_timestamp)
        y_time_df = calc_time_stamps(y_timestamp)

        x = df[self.price_cols + [self.vol_col, self.amt_vol]].values.astype(np.float32)
        x_stamp = x_time_df.values.astype(np.float32)
        y_stamp = y_time_df.values.astype(np.float32)

        x_mean, x_std = np.mean(x, axis=0), np.std(x, axis=0)

        x = (x - x_mean) / (x_std + 1e-5)
        x = np.clip(x, -self.clip, self.clip)

        return x[np.newaxis, :], x_stamp[np.newaxis, :], y_stamp[np.newaxis, :], x_mean, x_std

    def predict(self, df, x_timestamp, y_timestamp, pred_len, T=1.0, top_k=0, top_p=0.9, sample_count=1, verbose=True):
        x, x_stamp, y_stamp, x_mean, x_std = self._prepare_inputs(df, x_timestamp, y_timestamp)

        preds = self.generate(x, x_stamp, y_stamp, pred_len, T, top_k, top_p, sample_count, verbose)

        preds = preds.squeeze(0)
        preds = preds * (x_std + 1e-5) + x_mean

        pred_df = pd.DataFrame(preds, columns=self.price_cols + [self.vol_col, self.amt_vol], index=y_timestamp)
        return pred_df

    def predict_samples(self, df, x_timestamp, y_timestamp, pred_len, T=1.0, top_k=0, top_p=0.9, sample_count=1, verbose=True):
        """Like predict(), but also returns each unaveraged sample trajectory.

        The spread between samples is Kronos's own uncertainty signal (it already pays for
        sample_count forward passes) — predict() discards it by averaging internally.
        """
        x, x_stamp, y_stamp, x_mean, x_std = self._prepare_inputs(df, x_timestamp, y_timestamp)

        samples = self.generate(x, x_stamp, y_stamp, pred_len, T, top_k, top_p, sample_count, verbose, return_samples=True)
        samples = samples[0] * (x_std + 1e-5) + x_mean  # drop batch dim -> (sample_count, pred_len, channels)

        columns = self.price_cols + [self.vol_col, self.amt_vol]
        sample_dfs = [pd.DataFrame(s, columns=columns, index=y_timestamp) for s in samples]
        mean_df = pd.DataFrame(samples.mean(axis=0), columns=columns, index=y_timestamp)
        return mean_df, sample_dfs


    def predict_batch(self, df_list, x_timestamp_list, y_timestamp_list, pred_len, T=1.0, top_k=0, top_p=0.9, sample_count=1, verbose=True):
        """
        Perform parallel (batch) prediction on multiple time series. All series must have the same historical length and prediction length (pred_len).

        Args:
            df_list (List[pd.DataFrame]): List of input DataFrames, each containing price columns and optional volume/amount columns.
            x_timestamp_list (List[pd.DatetimeIndex or Series]): List of timestamps corresponding to historical data, length should match the number of rows in each DataFrame.
            y_timestamp_list (List[pd.DatetimeIndex or Series]): List of future prediction timestamps, length should equal pred_len.
            pred_len (int): Number of prediction steps.
            T (float): Sampling temperature.
            top_k (int): Top-k filtering threshold.
            top_p (float): Top-p (nucleus sampling) threshold.
            sample_count (int): Number of parallel samples per series, automatically averaged internally.
            verbose (bool): Whether to display autoregressive progress.

        Returns:
            List[pd.DataFrame]: List of prediction results in the same order as input, each DataFrame contains
                                `open, high, low, close, volume, amount` columns, indexed by corresponding `y_timestamp`.
        """
        # Basic validation
        if not isinstance(df_list, (list, tuple)) or not isinstance(x_timestamp_list, (list, tuple)) or not isinstance(y_timestamp_list, (list, tuple)):
            raise ValueError("df_list, x_timestamp_list, y_timestamp_list must be list or tuple types.")
        if not (len(df_list) == len(x_timestamp_list) == len(y_timestamp_list)):
            raise ValueError("df_list, x_timestamp_list, y_timestamp_list must have consistent lengths.")

        num_series = len(df_list)

        x_list = []
        x_stamp_list = []
        y_stamp_list = []
        means = []
        stds = []
        seq_lens = []
        y_lens = []

        for i in range(num_series):
            df = df_list[i]
            if not isinstance(df, pd.DataFrame):
                raise ValueError(f"Input at index {i} is not a pandas DataFrame.")
            if not all(col in df.columns for col in self.price_cols):
                raise ValueError(f"DataFrame at index {i} is missing price columns {self.price_cols}.")

            df = df.copy()
            if self.vol_col not in df.columns:
                df[self.vol_col] = 0.0
                df[self.amt_vol] = 0.0
            if self.amt_vol not in df.columns and self.vol_col in df.columns:
                df[self.amt_vol] = df[self.vol_col] * df[self.price_cols].mean(axis=1)

            if df[self.price_cols + [self.vol_col, self.amt_vol]].isnull().values.any():
                raise ValueError(f"DataFrame at index {i} contains NaN values in price or volume columns.")

            x_timestamp = x_timestamp_list[i]
            y_timestamp = y_timestamp_list[i]

            x_time_df = calc_time_stamps(x_timestamp)
            y_time_df = calc_time_stamps(y_timestamp)

            x = df[self.price_cols + [self.vol_col, self.amt_vol]].values.astype(np.float32)
            x_stamp = x_time_df.values.astype(np.float32)
            y_stamp = y_time_df.values.astype(np.float32)

            if x.shape[0] != x_stamp.shape[0]:
                raise ValueError(f"Inconsistent lengths at index {i}: x has {x.shape[0]} vs x_stamp has {x_stamp.shape[0]}.")
            if y_stamp.shape[0] != pred_len:
                raise ValueError(f"y_timestamp length at index {i} should equal pred_len={pred_len}, got {y_stamp.shape[0]}.")

            x_mean, x_std = np.mean(x, axis=0), np.std(x, axis=0)
            x_norm = (x - x_mean) / (x_std + 1e-5)
            x_norm = np.clip(x_norm, -self.clip, self.clip)

            x_list.append(x_norm)
            x_stamp_list.append(x_stamp)
            y_stamp_list.append(y_stamp)
            means.append(x_mean)
            stds.append(x_std)

            seq_lens.append(x_norm.shape[0])
            y_lens.append(y_stamp.shape[0])

        # Require all series to have consistent historical and prediction lengths for batch processing
        if len(set(seq_lens)) != 1:
            raise ValueError(f"Parallel prediction requires all series to have consistent historical lengths, got: {seq_lens}")
        if len(set(y_lens)) != 1:
            raise ValueError(f"Parallel prediction requires all series to have consistent prediction lengths, got: {y_lens}")

        x_batch = np.stack(x_list, axis=0).astype(np.float32)           # (B, seq_len, feat)
        x_stamp_batch = np.stack(x_stamp_list, axis=0).astype(np.float32) # (B, seq_len, time_feat)
        y_stamp_batch = np.stack(y_stamp_list, axis=0).astype(np.float32) # (B, pred_len, time_feat)

        preds = self.generate(x_batch, x_stamp_batch, y_stamp_batch, pred_len, T, top_k, top_p, sample_count, verbose)
        # preds: (B, pred_len, feat)

        pred_dfs = []
        for i in range(num_series):
            preds_i = preds[i] * (stds[i] + 1e-5) + means[i]
            pred_df = pd.DataFrame(preds_i, columns=self.price_cols + [self.vol_col, self.amt_vol], index=y_timestamp_list[i])
            pred_dfs.append(pred_df)

        return pred_dfs
```

> **Nota para la migración:** `_prepare_inputs` hace z-score normalization sobre toda la ventana de lookback (`x_mean`/`x_std` calculados ahí mismo) y desnormaliza con esos mismos valores — es la causa raíz de un sesgo documentado en `tests/test_kronos_ars_bias.py` para series con tendencia fuerte (ARS). `portfolio/forecasting.py::_apply_continuity_correction` lo parchea post-hoc. Chronos-Bolt/Moirai no deberían necesitar este parche si manejan la normalización distinto — vale la pena revisarlo explícitamente al migrar.

## 4. Carga de OHLCV + orquestación del pipeline — `portfolio/fetchers.py` (completo)

Acá se trae el histórico OHLCV real (data912 para BYMA; CCL histórico para convertir ARS→USD antes de alimentar al modelo).

```python
"""
Fetchers de precios para los instrumentos cargados en el portfolio.

Cada instrumento sabe de donde traer su precio via price_source +
price_source_params (ver schema.sql). Nada de tickers ni endpoints
hardcodeados en codigo: todo sale de esos campos.

Fuentes:
  - data912       -> acciones/CEDEARs/bonos/ON de BYMA (gratis, sin auth)
  - dolarapi      -> cotizaciones de USD en Argentina (gratis, sin auth)
  - binance       -> precio spot de criptomonedas (publico, sin auth)
  - argentinadatos -> serie historica diaria de dolar (CCL/MEP/etc, gratis, sin auth)
"""
import json
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

from portfolio import db

DATA912_BASE = "https://data912.com"
DOLARAPI_BASE = "https://dolarapi.com"
BINANCE_BASE = "https://api.binance.com"
BNA_URL = "https://www.bna.com.ar/Personas"
ARGENTINADATOS_BASE = "https://api.argentinadatos.com"

_BNA_CACHE_TTL = 300
_bna_cache = {"rates": None, "fetched_at": 0.0}

# "casa" de CCL en argentinadatos, usada para convertir series ARS a USD antes
# de alimentarlas a Kronos (ver portfolio/forecasting.py).
CCL_CASA = "contadoconliqui"
CCL_FRESHNESS_HOURS = 12

# tipo de instrumento -> endpoint live de data912
DATA912_LIVE_ENDPOINT_BY_TYPE = {
    "accion": "arg_stocks",
    "cedear": "arg_cedears",
    "bono": "arg_bonds",
    "on": "arg_corp",
}

# tipo de instrumento -> endpoint historico de data912 (ON no tiene historico)
DATA912_HIST_ENDPOINT_BY_TYPE = {
    "accion": "stocks",
    "cedear": "cedears",
    "bono": "bonds",
}


def _params(instrument):
    raw = instrument.get("price_source_params")
    return json.loads(raw) if raw else {}


# --- data912 -----------------------------------------------------------------

def fetch_data912_live(endpoint):
    resp = requests.get(f"{DATA912_BASE}/live/{endpoint}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def _bono_on_divisor(instrument):
    """Bonos/ON cotizan en el mercado 'por cada 100 nominales'; en todo el resto
    del sistema 'precio' significa 'por 1 unidad de cantidad' (como una accion).
    Normalizamos aca, en el borde con data912, para que nadie mas tenga que
    acordarse de este detalle (si se olvida una sola vez, el valor de la
    tenencia sale 100x mas grande de lo real)."""
    return 100 if instrument["type"] in ("bono", "on") else 1


def fetch_data912_quote(instrument):
    endpoint = _params(instrument).get("endpoint") or DATA912_LIVE_ENDPOINT_BY_TYPE.get(instrument["type"])
    if not endpoint:
        raise ValueError(f"No se pudo determinar el endpoint data912 para {instrument['symbol']}")

    rows = fetch_data912_live(endpoint)
    match = next((r for r in rows if r["symbol"] == instrument["symbol"]), None)
    if not match:
        raise ValueError(f"{instrument['symbol']} no encontrado en data912 /live/{endpoint}")

    divisor = _bono_on_divisor(instrument)
    bid = match.get("px_bid")
    ask = match.get("px_ask")
    return {
        "price": match["c"] / divisor,
        "bid": bid / divisor if bid is not None else None,
        "ask": ask / divisor if ask is not None else None,
    }


def fetch_data912_historical(symbol, asset_type):
    endpoint = DATA912_HIST_ENDPOINT_BY_TYPE.get(asset_type)
    if not endpoint:
        raise ValueError(f"data912 no tiene historico para el tipo '{asset_type}'")

    resp = requests.get(f"{DATA912_BASE}/historical/{endpoint}/{symbol}", timeout=20)
    resp.raise_for_status()
    raw = resp.json()
    if not raw or isinstance(raw, dict):
        raise ValueError(
            f"No hay historial de precios para {symbol} en data912 — "
            "esta fuente no cubre este instrumento en particular."
        )

    df = pd.DataFrame(raw)
    df["timestamps"] = pd.to_datetime(df["date"])
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    if asset_type in ("bono", "on"):
        df[["open", "high", "low", "close"]] /= 100
    # Monto operado real (no un cero fantasma): aproximado como volumen * precio
    # promedio del dia -- mismo fallback que usa KronosPredictor._prepare_inputs
    # cuando no recibe esta columna, calculado aca (con el precio ya normalizado
    # "por 1 unidad") para que quede disponible tambien para indicadores/chart.
    df["amount"] = df["volume"] * df[["open", "high", "low", "close"]].mean(axis=1)
    return df[["timestamps", "open", "high", "low", "close", "volume", "amount"]]


# --- Banco Nacion (BNA) ------------------------------------------------------------

def _bna_usd_row(soup, tab_id):
    tab = soup.find(id=tab_id)
    if not tab:
        raise ValueError(f"No se encontro el bloque '{tab_id}' en la pagina de BNA")
    for row in tab.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if cells and cells[0].startswith("Dolar"):
            return float(cells[1].replace(",", ".")), float(cells[2].replace(",", "."))
    raise ValueError(f"No se encontro la fila de Dolar U.S.A. en '{tab_id}'")


def fetch_bna_rates():
    """Cotizacion real de BNA, scrapeada de su propia web (no publican API):
    billete (efectivo) y divisa (transferencia) tienen tasas distintas."""
    if _bna_cache["rates"] is not None and time.time() - _bna_cache["fetched_at"] < _BNA_CACHE_TTL:
        return _bna_cache["rates"]

    resp = requests.get(BNA_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    billete_compra, billete_venta = _bna_usd_row(soup, "billetes")
    divisa_compra, divisa_venta = _bna_usd_row(soup, "divisas")

    rates = {
        "billete_compra": billete_compra, "billete_venta": billete_venta,
        "divisa_compra": divisa_compra, "divisa_venta": divisa_venta,
    }
    _bna_cache["rates"] = rates
    _bna_cache["fetched_at"] = time.time()
    return rates


# --- argentinadatos (historico de dolar) ---------------------------------------

def fetch_fx_historical(casa=CCL_CASA):
    """Serie diaria completa (no admite rango de fechas, la API devuelve todo
    el historico junto -- ~5000 puntos desde 2013, una sola request)."""
    resp = requests.get(f"{ARGENTINADATOS_BASE}/v1/cotizaciones/dolares/{casa}", timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    return [{"fecha": r["fecha"], "compra": r.get("compra"), "venta": r.get("venta")} for r in raw]


def ensure_ccl_history(conn, casa=CCL_CASA):
    """Refresca la serie historica de CCL si la ultima fecha guardada no es de
    hoy (mismo criterio de frescura que quotes_latest/fundamentals_latest: no
    pegarle a la fuente externa en cada carga)."""
    latest = db.get_fx_rate_latest_fetched_date(conn, casa)
    today = datetime.now(timezone.utc).date().isoformat()
    if latest == today:
        return
    try:
        rows = fetch_fx_historical(casa)
    except Exception:
        return  # sin conexion o la fuente esta caida -- se usa lo que ya hay guardado
    if rows:
        db.upsert_fx_rates(conn, casa, rows)


INFLATION_URL = f"{ARGENTINADATOS_BASE}/v1/finanzas/indices/inflacion"


def fetch_inflation_history():
    """IPC mensual (% variacion mes a mes), serie completa en una sola request."""
    resp = requests.get(INFLATION_URL, timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    return [{"fecha": r["fecha"], "valor_mensual_pct": r["valor"]} for r in raw]


def ensure_inflation_history(conn):
    latest = db.get_inflation_latest_fetched_date(conn)
    today = datetime.now(timezone.utc).date().isoformat()
    if latest == today:
        return
    try:
        rows = fetch_inflation_history()
    except Exception:
        return
    if rows:
        db.upsert_inflation_rates(conn, rows)


# --- yfinance (benchmarks, ej. SPY) ---------------------------------------------

def fetch_benchmark_history(symbol):
    """Historico diario de cierre de un simbolo via yfinance -- usado para
    comparar la cartera contra un indice en USD (SPY)."""
    hist = yf.Ticker(symbol).history(period="max")
    if hist.empty:
        raise ValueError(f"yfinance no devolvio historico para {symbol}")
    hist = hist.reset_index()
    return [
        {"fecha": pd.Timestamp(row.Date).strftime("%Y-%m-%d"), "close": float(row.Close)}
        for row in hist.itertuples()
        if pd.notna(row.Close)  # yfinance a veces devuelve el dia en curso sin cierre todavia (NaN) -- se saltea, nunca se inserta un cierre inventado
    ]


def ensure_benchmark_history(conn, symbol="SPY"):
    latest = db.get_benchmark_latest_fetched_date(conn, symbol)
    today = datetime.now(timezone.utc).date().isoformat()
    if latest == today:
        return
    try:
        rows = fetch_benchmark_history(symbol)
    except Exception:
        return
    if rows:
        db.upsert_benchmark_prices(conn, symbol, rows)


# --- dolarapi ------------------------------------------------------------------

_DOLARAPI_CACHE_TTL = 60
_dolarapi_cache = {}


def fetch_dolarapi_casa(casa):
    """Cacheado (Fase 10): dashboard-bundle llama esto hasta 4 veces en una
    sola request (currency-exposure, risk, ganancia, usd_mep_rate) -- sin
    cache, cada llamada es un HTTP real (~5s con esta API en particular),
    volviendo el bundle completo lentísimo (~20s, reportado por el usuario)."""
    cached = _dolarapi_cache.get(casa)
    if cached and time.time() - cached["fetched_at"] < _DOLARAPI_CACHE_TTL:
        return cached["data"]
    resp = requests.get(f"{DOLARAPI_BASE}/v1/dolares/{casa}", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    _dolarapi_cache[casa] = {"data": data, "fetched_at": time.time()}
    return data


def fetch_dolarapi_quote(instrument):
    casa = _params(instrument).get("casa", "oficial")
    data = fetch_dolarapi_casa(casa)
    return {"price": data["venta"], "bid": data["compra"], "ask": data["venta"]}


# "casa" de dolarapi por fuente de USD elegida en Preferencias (ver app_settings.usd_rate_source).
_DOLARAPI_CASA_BY_SOURCE = {"mep": "bolsa", "ccl": "contadoconliqui"}


def resolve_usd_rate(settings):
    """Centraliza de donde sale "la" cotizacion USD/ARS usada para convertir
    la cartera -- antes estaba hardcodeada a BNA por separado en routes.py e
    history.py; ahora ambos la piden aca segun app_settings.usd_rate_source."""
    source = settings.get("usd_rate_source") or "bna"
    if source in _DOLARAPI_CASA_BY_SOURCE:
        return fetch_dolarapi_casa(_DOLARAPI_CASA_BY_SOURCE[source])["venta"]
    return fetch_bna_rates()[settings["bna_rate_variant"]]


# --- binance ---------------------------------------------------------------------

def fetch_binance_quote(instrument):
    pair = _params(instrument).get("pair", instrument["symbol"])
    resp = requests.get(f"{BINANCE_BASE}/api/v3/ticker/price", params={"symbol": pair}, timeout=15)
    resp.raise_for_status()
    price = float(resp.json()["price"])
    return {"price": price, "bid": None, "ask": None}


# --- orquestacion ------------------------------------------------------------------

_QUOTE_FETCHERS = {
    "data912": fetch_data912_quote,
    "dolarapi": fetch_dolarapi_quote,
    "binance": fetch_binance_quote,
}


def update_quote(conn, instrument):
    """Trae y guarda la cotizacion actual de un instrumento. No hace nada para price_source='manual'."""
    fetcher = _QUOTE_FETCHERS.get(instrument["price_source"])
    if fetcher is None:
        return None

    quote = fetcher(instrument)
    db.upsert_quote(
        conn, instrument["id"], quote["price"],
        source=instrument["price_source"], bid=quote.get("bid"), ask=quote.get("ask"),
    )
    return quote


def update_all_quotes(conn):
    """Actualiza la cotizacion de todos los instrumentos. Devuelve {symbol: error} de los que fallaron."""
    errors = {}
    for instrument in db.list_instruments(conn):
        try:
            update_quote(conn, instrument)
        except Exception as e:
            errors[instrument["symbol"]] = str(e)
    return errors


def backfill_history(conn, instrument):
    """Descarga historico OHLC de data912 y lo guarda en price_history. Solo accion/cedear/bono."""
    if instrument["price_source"] != "data912":
        raise ValueError("El historico automatico solo esta soportado para instrumentos data912")

    df = fetch_data912_historical(instrument["symbol"], instrument["type"])
    rows = [
        (instrument["id"], row.timestamps.isoformat(), row.open, row.high, row.low,
         row.close, row.volume, "data912")
        for row in df.itertuples()
    ]
    conn.executemany(
        """INSERT INTO price_history (instrument_id, timestamp, open, high, low, close, volume, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(instrument_id, timestamp) DO UPDATE SET
               open=excluded.open, high=excluded.high, low=excluded.low,
               close=excluded.close, volume=excluded.volume, source=excluded.source""",
        rows,
    )
    conn.commit()
    return len(rows)
```

## 5. Procesamiento de activos + llamada al modelo — `portfolio/forecasting.py` (completo)

Este es el archivo **más importante para la migración**: arma el DataFrame OHLCV de entrada, lo convierte a USD cuando corresponde, invoca al predictor y post-procesa la salida.

```python
"""Orquesta un forecast de punta a punta: datos -> modelo -> indicadores -> senial.

Corre dentro del worker en background (ver forecast_worker.py), nunca en el
hilo que atiende el request HTTP que lo dispara.
"""
import bisect
import json

import pandas as pd

from model import runtime as model_runtime
from portfolio import db, fetchers, indicators, signals

OHLCV_COLS = ["open", "high", "low", "close", "volume", "amount"]

# accion/cedear cotizan en ARS en data912. Convertir a USD via CCL historico
# no elimina por si solo el sesgo de normalizacion por ventana de Kronos (se
# probo empiricamente contra el precio USD real de 8 instrumentos: el gap es
# practicamente identico en ambas monedas -- es una propiedad de la
# normalizacion frente a CUALQUIER serie con tendencia, no un artefacto de
# moneda), pero aisla el componente de tendencia que es 100% estructural y
# garantizado en ARS (inflacion/devaluacion) del que es propio del activo, asi
# que se mantiene junto con la correccion de continuidad (_apply_continuity_
# correction), que es la que realmente ataca el salto del primer paso. bono/on
# no tienen contrapartida USD clara via CCL (son renta fija local), quedan ARS.
USD_MODEL_ASSET_TYPES = ("accion", "cedear")
PRICE_COLS = ("open", "high", "low", "close")


def build_lookback_df(conn, *, instrument_id, symbol, market, asset_type, lookback, as_of_date=None):
    """Holdings reusan price_history (completando con backfill si falta);
    el Explorador pide historico fresco directo, sin necesitar alta en instruments.

    as_of_date acota el lookback a velas <= esa fecha -- es lo que hace que un
    backtest sea honesto (el modelo nunca ve datos que todavia no existian en
    ese momento del pasado)."""
    if instrument_id is not None:
        if as_of_date:
            rows = db.get_price_history_as_of(conn, instrument_id, as_of_date, limit=lookback)
        else:
            rows = db.get_price_history(conn, instrument_id, limit=lookback)
            if len(rows) < lookback:
                instrument = db.get_instrument(conn, instrument_id)
                fetchers.backfill_history(conn, instrument)
                rows = db.get_price_history(conn, instrument_id, limit=lookback)
        df = pd.DataFrame(rows)
        df["timestamps"] = pd.to_datetime(df["timestamp"])
        # Monto operado real (no un cero fantasma), misma aproximacion que
        # fetchers.fetch_data912_historical para cuando esta columna se arma
        # directo del Explorador.
        df["amount"] = df["volume"].fillna(0) * df[["open", "high", "low", "close"]].mean(axis=1)
    else:
        df = fetchers.fetch_data912_historical(symbol, asset_type)

    df = df.sort_values("timestamps").tail(lookback).reset_index(drop=True)
    return df[["timestamps"] + OHLCV_COLS]


def build_chart_payload(conn, *, instrument_id, symbol, market, asset_type, lookback):
    """La mitad "rapida" de run_forecast_job: historico + indicadores, sin tocar
    el modelo. Esto es lo que deja ver el grafico y las medias/RSI/MACD al
    instante, sin esperar una corrida de Kronos (que es lo lento)."""
    lookback_df = build_lookback_df(
        conn, instrument_id=instrument_id, symbol=symbol, market=market,
        asset_type=asset_type, lookback=lookback, as_of_date=None,
    )
    return {
        "symbol": symbol,
        "historical": _candles_json(lookback_df, lookback_df["timestamps"]),
        "indicators": indicators.compute_all(lookback_df),
    }


def _ccl_rate_for_dates(conn, date_strs):
    """Serie de cotizacion CCL-venta alineada 1 a 1 con date_strs (forward-fill
    desde la ultima fecha conocida <= cada fecha -- mismo criterio ya usado en
    portfolio/history.py para precios). Devuelve None si falta cobertura para
    alguna fecha de la ventana (no se arriesga a mezclar ARS/USD en la misma
    serie que ve el modelo)."""
    fetchers.ensure_ccl_history(conn)
    rate_map = db.get_fx_rate_history(conn, fetchers.CCL_CASA)
    if not rate_map:
        return None

    known_dates = sorted(rate_map)
    rates = []
    for date_str in date_strs:
        i = bisect.bisect_right(known_dates, date_str) - 1
        if i < 0:
            return None
        rate = rate_map[known_dates[i]]
        if rate is None:
            return None
        rates.append(rate)
    return pd.Series(rates)


def build_model_input(conn, lookback_df, asset_type):
    """Convierte el lookback a USD (via CCL historico) antes de alimentarlo a
    Kronos, para accion/cedear -- la serie en ARS cruda arrastra la tendencia
    estructural que sesga la normalizacion por ventana del modelo. El grafico y
    los indicadores siguen mostrandose en ARS (lookback_df no se toca); esta
    conversion es solo para lo que *ve* el modelo.

    Devuelve (df_para_el_modelo, ultimo_ccl_conocido_o_None, "USD"|"ARS"). Si
    falta cobertura de CCL para alguna fecha de la ventana, degrada a ARS sin
    romper (igual que cualquier otra fuente de datos faltante en este proyecto)."""
    if asset_type not in USD_MODEL_ASSET_TYPES:
        return lookback_df, None, "ARS"

    date_strs = lookback_df["timestamps"].dt.strftime("%Y-%m-%d")
    rates = _ccl_rate_for_dates(conn, date_strs)
    if rates is None:
        return lookback_df, None, "ARS"

    df_usd = lookback_df.copy()
    for col in PRICE_COLS:
        df_usd[col] = df_usd[col] / rates.values
    return df_usd, float(rates.iloc[-1]), "USD"


def _apply_continuity_correction(pred_mean, sample_dfs, last_close):
    """Ancla cada trayectoria predicha (la media y cada muestra individual) para
    que su primer paso coincida exactamente con el ultimo cierre real conocido.

    Por que hace falta: el autoregresivo de Kronos no tiene ninguna restriccion
    que ate el primer paso generado al contexto real -- se desnormaliza con el
    x_mean/x_std de toda la ventana (ver model/kronos.py::_prepare_inputs), asi
    que en una ventana con tendencia el primer paso ya arranca lejos del ultimo
    cierre (confirmado con datos reales en tests/test_kronos_ars_bias.py, y
    reproducido en USD tambien -- no es un artefacto de moneda, es una
    propiedad de la normalizacion por ventana frente a CUALQUIER serie con
    tendencia). Sin esta correccion, la mayor parte de la "señal" termina
    siendo ese salto artificial del primer paso, no la forma que el modelo
    realmente predice a partir de ahi.

    Se corrige con un offset CONSTANTE por trayectoria (se preserva la
    forma/pendiente predicha, solo se desplaza el nivel), aplicado por igual a
    las 4 columnas de precio."""
    def _shift(df):
        offset = last_close - df["close"].iloc[0]
        df = df.copy()
        for col in PRICE_COLS:
            df[col] = df[col] + offset
        return df

    pred_mean = _shift(pred_mean)
    sample_dfs = [_shift(s) for s in sample_dfs] if sample_dfs else sample_dfs
    return pred_mean, sample_dfs


def _future_timestamps(timestamps, pred_len):
    """Extiende la frecuencia detectada en el historico hacia adelante.
    Simplificacion aceptada: no excluye fines de semana/feriados en datos diarios."""
    deltas = timestamps.diff().dropna()
    step = deltas.mode().iloc[0] if not deltas.empty else pd.Timedelta(days=1)
    last = timestamps.iloc[-1]
    return pd.Series([last + step * (i + 1) for i in range(pred_len)])


def _candles_json(df, timestamps):
    return [
        {
            "time": int(pd.Timestamp(ts).timestamp()),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        }
        for ts, row in zip(timestamps, df.itertuples(index=False))
    ]


def run_forecast_job(conn, forecast_id):
    row = db.get_forecast(conn, forecast_id)
    config = db.get_signal_config(conn)

    lookback_df = build_lookback_df(
        conn, instrument_id=row["instrument_id"], symbol=row["symbol"],
        market=row["market"], asset_type=row["asset_type"], lookback=row["lookback"],
        as_of_date=row["as_of_date"],
    )
    model_input_df, last_ccl, model_currency = build_model_input(conn, lookback_df, row["asset_type"])
    x_df = model_input_df[OHLCV_COLS]
    x_timestamp = model_input_df["timestamps"]
    y_timestamp = _future_timestamps(x_timestamp, row["pred_len"])

    model_runtime.ensure_loaded(row["model_key"])
    predictor = model_runtime.get_predictor()

    if row["sample_count"] > 1:
        pred_mean, sample_dfs = predictor.predict_samples(
            df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
            pred_len=row["pred_len"], T=row["temperature"], top_p=row["top_p"],
            sample_count=row["sample_count"], verbose=False,
        )
    else:
        pred_mean = predictor.predict(
            df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
            pred_len=row["pred_len"], T=row["temperature"], top_p=row["top_p"],
            sample_count=1, verbose=False,
        )
        sample_dfs = None

    last_close = float(x_df["close"].iloc[-1])  # en USD si model_currency == "USD"
    pred_mean, sample_dfs = _apply_continuity_correction(pred_mean, sample_dfs, last_close)
    signal, pred_change_pct, uncertainty_pct, distribution = signals.compute_signal(
        last_close, pred_mean, sample_dfs, config
    )
    explanation = signals.explain(
        row["symbol"], pred_change_pct, uncertainty_pct, signal, row["pred_len"], config,
        distribution=distribution, model_currency=model_currency,
    )

    # El grafico siempre se muestra en ARS (lo que el usuario conoce) -- si el
    # modelo corrio en USD, reconvertimos la prediccion con el ultimo CCL
    # conocido (simplificacion declarada: se mantiene fijo durante todo el
    # horizonte predicho, igual que ya acepta portfolio/history.py para el
    # historico reconstruido).
    pred_display = pred_mean.copy()
    if model_currency == "USD" and last_ccl:
        for col in PRICE_COLS:
            pred_display[col] = pred_display[col] * last_ccl

    result = {
        "symbol": row["symbol"],
        "historical": _candles_json(lookback_df, lookback_df["timestamps"]),
        "predicted": _candles_json(pred_display, pred_display.index),
        "indicators": indicators.compute_all(lookback_df),
        "signal": signal,
        "pred_change_pct": pred_change_pct,
        "uncertainty_pct": uncertainty_pct,
        "distribution": distribution,
        "model_currency": model_currency,
        "explanation": explanation,
    }

    db.mark_forecast_done(
        conn, forecast_id,
        result_json=json.dumps(result),
        signal=signal, pred_change_pct=pred_change_pct, uncertainty_pct=uncertainty_pct,
    )
```

## 6. Orquestación/cola de jobs — `portfolio/forecast_worker.py` (completo)

Thread único + `queue.Queue`, sin Celery/Redis (justificado porque es una app local de un solo usuario). **Para FastAPI con MPS, este es el componente que más atención necesita**: un solo device sirviendo requests concurrentes necesita o bien este mismo patrón de cola single-worker, o un semáforo que serialice el acceso al modelo.

```python
"""Un unico worker en background para correr forecasts de Kronos sin bloquear Flask.

Nada de Celery/Redis: el predictor es una sola instancia de modelo sobre un
device -- dos inferencias en paralelo no serian mas rapidas, y esto es una app
local de un solo usuario. Un thread + una cola alcanza.
"""
import logging
import queue
import threading
from datetime import datetime, timedelta

from portfolio import db, forecasting
from portfolio.tz import today_ar

logger = logging.getLogger(__name__)

_queue = queue.Queue()

# Para que un instrumento llegue a "validado" (accuracy.py exige >=30
# pronosticos NO SUPERPUESTOS, signal_config.min_non_overlapping_forecasts)
# hacen falta al menos 30*BACKTEST_PRED_LEN dias de ventana -- con
# BACKTEST_PRED_LEN=35 eso es ~1050 dias. 180 dias (el valor viejo) era un
# techo duro que nunca iba a poder llegar a 30 sin importar cuantas veces se
# corriera el backtest, aunque el price_history disponible fuera de anios
# (GGAL/YPFD desde 2001, KO desde 2012, etc.) -- confirmado reproduciendo el
# backtest real antes de este cambio.
BACKTEST_ANCHOR_SPACING_DAYS = 7
BACKTEST_PRED_LEN = 35
BACKTEST_DEFAULT_WINDOW_DAYS = 1500


def _cap_pred_len_to_maturity(conn, instrument_id, pred_len, as_of_date):
    """Un bono/ON no puede predecirse mas alla de su propio vencimiento --
    acota el horizonte en vez de pronosticar precio sobre un instrumento que
    ya dejo de existir (bug real reportado: Kronos corria 15-120 dias sobre
    TO26 cuando venia venciendo en menos de eso, prediciendo valores sin
    sentido despues del pago a la par)."""
    if instrument_id is None:
        return pred_len
    instrument = db.get_instrument(conn, instrument_id)
    if not instrument or instrument["type"] not in ("bono", "on") or not instrument.get("maturity_date"):
        return pred_len
    reference = datetime.fromisoformat(as_of_date).date() if as_of_date else today_ar()
    maturity = datetime.fromisoformat(instrument["maturity_date"]).date()
    dias_restantes = (maturity - reference).days
    if dias_restantes > 0:
        return min(pred_len, dias_restantes)
    return pred_len  # ya vencido o vence hoy -- caso raro, se deja el default en vez de bloquear la corrida


def submit_forecast(*, instrument_id=None, symbol, market, asset_type, sample_count=None,
                     pred_len=None, as_of_date=None):
    conn = db.get_connection()
    try:
        config = db.get_signal_config(conn)
        sample_count = sample_count or config["default_sample_count"]
        pred_len = pred_len or config["default_pred_len"]
        pred_len = _cap_pred_len_to_maturity(conn, instrument_id, pred_len, as_of_date)
        expected = config["benchmark_seconds_per_step"] * pred_len * sample_count
        forecast_id = db.insert_forecast_job(
            conn, instrument_id=instrument_id, symbol=symbol, market=market, asset_type=asset_type,
            model_key=config["default_model_key"], lookback=config["default_lookback"],
            pred_len=pred_len, temperature=config["default_temperature"], top_p=config["default_top_p"],
            sample_count=sample_count, expected_duration_seconds=expected, as_of_date=as_of_date,
        )
    finally:
        conn.close()
    _queue.put(forecast_id)
    return forecast_id


def _is_fresh(conn, instrument_id, freshness_hours):
    latest = db.get_latest_forecast(conn, instrument_id=instrument_id)
    if not latest or latest["status"] != "done" or not latest["finished_at"]:
        return False
    age_hours = (datetime.utcnow() - datetime.fromisoformat(latest["finished_at"])).total_seconds() / 3600
    return age_hours < freshness_hours


def enqueue_all_holdings(force=False):
    conn = db.get_connection()
    try:
        config = db.get_signal_config(conn)
        holdings = [h for h in db.get_holdings(conn) if h["instrument"]["type"] not in ("fondo", "cripto")]
        targets = [
            h["instrument"] for h in holdings
            if force or not _is_fresh(conn, h["instrument"]["id"], config["freshness_hours"])
        ]
    finally:
        conn.close()

    return [
        submit_forecast(instrument_id=i["id"], symbol=i["symbol"], market=i["market"], asset_type=i["type"])
        for i in targets
    ]


def _backtest_anchors(conn, instrument, config, start_date=None):
    """Fechas pasadas donde todavia no corrimos un backtest para este instrumento,
    espaciadas BACKTEST_ANCHOR_SPACING_DAYS dias, acotadas a:
    - no antes de que exista suficiente historico previo (config['default_lookback'] dias antes)
    - no despues de hoy menos BACKTEST_PRED_LEN (para que el tramo predicho entero ya sea pasado)
    - start_date (si se pasa) nunca se ignora: es desde cuando importa la cartera."""
    price_rows = db.get_price_history(conn, instrument["id"])
    if len(price_rows) < config["default_lookback"]:
        return []

    earliest_with_lookback = (
        datetime.fromisoformat(price_rows[config["default_lookback"] - 1]["timestamp"].split("T")[0]).date()
    )
    today = today_ar()
    window_start = today - timedelta(days=BACKTEST_DEFAULT_WINDOW_DAYS)
    start = max(earliest_with_lookback, window_start)
    if start_date:
        start = max(start, datetime.fromisoformat(start_date).date())
        start = max(start, earliest_with_lookback)
    end = today - timedelta(days=BACKTEST_PRED_LEN)

    already = db.list_backtest_anchors(conn, instrument["id"])
    anchors = []
    cursor = start
    while cursor <= end:
        iso = cursor.isoformat()
        if iso not in already:
            anchors.append(iso)
        cursor += timedelta(days=BACKTEST_ANCHOR_SPACING_DAYS)
    return anchors


def enqueue_backtest(instrument_id, start_date=None):
    """Encola un backtest retroactivo para un instrumento: una corrida de Kronos por
    cada fecha ancla, usando solo datos disponibles hasta esa fecha. Devuelve la
    lista de forecast_ids encolados (puede ser vacia si ya estaban todos cubiertos)."""
    conn = db.get_connection()
    try:
        instrument = db.get_instrument(conn, instrument_id)
        if not instrument:
            return []
        config = db.get_signal_config(conn)
        anchors = _backtest_anchors(conn, instrument, config, start_date=start_date)
    finally:
        conn.close()

    return [
        submit_forecast(
            instrument_id=instrument["id"], symbol=instrument["symbol"], market=instrument["market"],
            asset_type=instrument["type"], sample_count=1, pred_len=BACKTEST_PRED_LEN, as_of_date=anchor,
        )
        for anchor in anchors
    ]


def enqueue_portfolio_backtest():
    """Backtest retroactivo de todas las tenencias actuales, cada una desde su
    primera transaccion -- no tiene sentido retroceder mas alla de cuando se
    empezo a tener la posicion, ya que la cartera reconstruida no la usa antes."""
    conn = db.get_connection()
    try:
        holdings = [h for h in db.get_holdings(conn) if h["instrument"]["type"] not in ("fondo", "cripto")]
        starts = {}
        for h in holdings:
            txs = db.list_transactions(conn, h["instrument"]["id"])
            if txs:
                starts[h["instrument"]["id"]] = min(t["fecha"] for t in txs)
        instrument_ids = [h["instrument"]["id"] for h in holdings]
    finally:
        conn.close()

    forecast_ids = []
    for instrument_id in instrument_ids:
        forecast_ids.extend(enqueue_backtest(instrument_id, start_date=starts.get(instrument_id)))
    return forecast_ids


def _worker_loop():
    while True:
        forecast_id = _queue.get()
        conn = db.get_connection()
        try:
            db.mark_forecast_running(conn, forecast_id)
            forecasting.run_forecast_job(conn, forecast_id)
        except Exception as exc:
            logger.exception("Forecast %s fallo", forecast_id)
            db.mark_forecast_error(conn, forecast_id, str(exc))
        finally:
            conn.close()
            _queue.task_done()


def _recover_orphaned_jobs():
    """Jobs en queued/running al importar este modulo quedaron huerfanos de una
    corrida anterior (p.ej. el reload de debug=True mato al worker a mitad de camino)."""
    conn = db.get_connection()
    try:
        for job in db.list_pending_forecasts(conn):
            db.mark_forecast_error(conn, job["id"], "Interrumpido por reinicio del servidor")
    finally:
        conn.close()


_recover_orphaned_jobs()
threading.Thread(target=_worker_loop, daemon=True, name="kronos-forecast-worker").start()
```

## 7. Persistencia (SQLite) — excerpt relevante de `portfolio/schema.sql`

```sql
CREATE TABLE IF NOT EXISTS price_history (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    timestamp TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    source TEXT,
    PRIMARY KEY (instrument_id, timestamp)
);

-- Config unica de senales + defaults de forecast. Nada hardcodeado en Python:
-- se edita via GET|PUT /api/portfolio/signal-config.
CREATE TABLE IF NOT EXISTS signal_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    buy_threshold_pct REAL NOT NULL DEFAULT 5.0,
    sell_threshold_pct REAL NOT NULL DEFAULT -5.0,
    freshness_hours REAL NOT NULL DEFAULT 12.0,
    default_model_key TEXT NOT NULL DEFAULT 'kronos-base',
    default_lookback INTEGER NOT NULL DEFAULT 400,
    -- Horizonte corto + muchas muestras en vez de un horizonte largo con una
    -- sola corrida: un pronostico a 120 dias con un unico punto no es confiable
    -- (ver tests/test_kronos_ars_bias.py) y la distribucion de sample_count
    -- muestras (ver portfolio/signals.py) es mas honesta que un % puntual.
    default_pred_len INTEGER NOT NULL DEFAULT 15,
    default_sample_count INTEGER NOT NULL DEFAULT 60,
    default_temperature REAL NOT NULL DEFAULT 1.0,
    default_top_p REAL NOT NULL DEFAULT 0.9,
    -- Recalibrado para sample_count>1 (las muestras se corren en batch: el
    -- costo no escala linealmente con sample_count, ver benchmark real en el
    -- plan de Fase 1 -- pred_len=15/sample_count=60 midio ~68s en MPS).
    benchmark_seconds_per_step REAL NOT NULL DEFAULT 0.076,
    ...
);

-- Cache de forecasts + estado del job en background. instrument_id es NULL para
-- simbolos del Explorador que todavia no estan en la cartera (no ensucia el
-- catalogo instruments con tickers meramente consultados); symbol/market siempre
-- estan para poder buscar "ultimo forecast" en ambos casos.
CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER REFERENCES instruments(id),
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'error')),
    model_key TEXT NOT NULL,
    lookback INTEGER NOT NULL,
    pred_len INTEGER NOT NULL,
    temperature REAL NOT NULL,
    top_p REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    expected_duration_seconds REAL,
    result_json TEXT,
    signal TEXT CHECK (signal IN ('comprar', 'mantener', 'vender')),
    pred_change_pct REAL,
    uncertainty_pct REAL,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,
    as_of_date TEXT
);
```

Funciones de acceso relevantes en `portfolio/db.py` (archivo completo tiene 1030 líneas, mezcla esto con CRUD de todo lo demás de la cartera — no incluido entero):

```python
def get_price_history(conn, instrument_id, limit=None):
    query = "SELECT * FROM price_history WHERE instrument_id = ? ORDER BY timestamp"
    if limit:
        query = f"SELECT * FROM ({query} DESC LIMIT {int(limit)}) ORDER BY timestamp"
    rows = conn.execute(query, (instrument_id,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_price_history_as_of(conn, instrument_id, as_of_date, limit=None):
    """Igual que get_price_history pero solo con velas hasta as_of_date (inclusive) --
    para que un backtest nunca vea datos que todavia no existian en esa fecha."""
    query = "SELECT * FROM price_history WHERE instrument_id = ? AND timestamp <= ? ORDER BY timestamp"
    if limit:
        query = f"SELECT * FROM ({query} DESC LIMIT {int(limit)}) ORDER BY timestamp"
    rows = conn.execute(query, (instrument_id, f"{as_of_date}T23:59:59")).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_signal_config(conn):
    return _row_to_dict(conn.execute("SELECT * FROM signal_config WHERE id = 1").fetchone())


def insert_forecast_job(conn, *, instrument_id, symbol, market, asset_type, model_key,
                         lookback, pred_len, temperature, top_p, sample_count,
                         expected_duration_seconds, as_of_date=None):
    cur = conn.execute(
        """INSERT INTO forecasts (instrument_id, symbol, market, asset_type, model_key,
               lookback, pred_len, temperature, top_p, sample_count, expected_duration_seconds,
               as_of_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (instrument_id, symbol, market, asset_type, model_key, lookback, pred_len,
         temperature, top_p, sample_count, expected_duration_seconds, as_of_date),
    )
    conn.commit()
    return cur.lastrowid


def mark_forecast_running(conn, forecast_id):
    conn.execute(
        "UPDATE forecasts SET status = 'running', started_at = datetime('now') WHERE id = ?",
        (forecast_id,),
    )
    conn.commit()


def mark_forecast_done(conn, forecast_id, *, result_json, signal, pred_change_pct, uncertainty_pct):
    conn.execute(
        """UPDATE forecasts SET status = 'done', finished_at = datetime('now'),
               result_json = ?, signal = ?, pred_change_pct = ?, uncertainty_pct = ?
           WHERE id = ?""",
        (result_json, signal, pred_change_pct, uncertainty_pct, forecast_id),
    )
    conn.commit()


def mark_forecast_error(conn, forecast_id, error_message):
    conn.execute(
        "UPDATE forecasts SET status = 'error', finished_at = datetime('now'), error_message = ? WHERE id = ?",
        (error_message, forecast_id),
    )
    conn.commit()


def get_latest_forecast(conn, instrument_id=None, symbol=None, market=None):
    """Solo pronosticos 'en vivo' (as_of_date NULL) -- uno de backtest nunca debe
    aparecer como si fuera la senial actual."""
    if instrument_id is not None:
        row = conn.execute(
            """SELECT * FROM forecasts WHERE instrument_id = ? AND as_of_date IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (instrument_id,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM forecasts WHERE symbol = ? AND market = ? AND instrument_id IS NULL
               AND as_of_date IS NULL ORDER BY created_at DESC LIMIT 1""",
            (symbol, market),
        ).fetchone()
    return _row_to_dict(row)


def list_forecasts_for_instrument(conn, instrument_id):
    rows = conn.execute(
        "SELECT * FROM forecasts WHERE instrument_id = ? AND status = 'done' ORDER BY created_at",
        (instrument_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_pending_forecasts(conn):
    """queued/running al reiniciar el proceso son huerfanos (el thread que los corria murio)."""
    rows = conn.execute("SELECT * FROM forecasts WHERE status IN ('queued', 'running')").fetchall()
    return [_row_to_dict(r) for r in rows]
```

## 8. Entry points HTTP actuales (Flask) — excerpt de `webui/app.py`

Hoy hay **dos** caminos de inferencia en el repo:

- **El de la cartera real** (`portfolio/routes.py` → `forecast_worker.submit_forecast` → `forecasting.run_forecast_job`, ya mostrado arriba) — asíncrono, vía cola.
- **Un demo standalone** (`/api/predict` en `webui/app.py`), síncrono, que carga un CSV de `data/` y llama al predictor directo. Probablemente no migra 1:1 (es un demo educativo), pero muestra el contrato mínimo de uso de `KronosPredictor` sin toda la orquestación de cartera.

```python
# --- carga de OHLCV desde CSV/feather (demo standalone) ---
def load_data_file(file_path):
    """Load data file"""
    try:
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        elif file_path.endswith('.feather'):
            df = pd.read_feather(file_path)
        else:
            return None, "Unsupported file format"

        required_cols = ['open', 'high', 'low', 'close']
        if not all(col in df.columns for col in required_cols):
            return None, f"Missing required columns: {required_cols}"

        if 'timestamps' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamps'])
        elif 'timestamp' in df.columns:
            df['timestamps'] = pd.to_datetime(df['timestamp'])
        elif 'date' in df.columns:
            df['timestamps'] = pd.to_datetime(df['date'])
        else:
            df['timestamps'] = pd.date_range(start='2024-01-01', periods=len(df), freq='1H')
        ...


# --- carga/gestión del modelo ---
@app.route('/api/load-model', methods=['POST'])
def load_model_endpoint():
    data = request.get_json()
    model_key = data.get('model_key', 'kronos-base')
    device = data.get('device')  # None -> autodetect (cuda/mps/cpu)
    model_config = model_runtime.load_model(model_key, device)
    ...

@app.route('/api/available-models')
def available_models():
    return jsonify({'models': model_runtime.AVAILABLE_MODELS})

@app.route('/api/model-status')
def model_status():
    return jsonify(model_runtime.status())


# --- inferencia síncrona (demo) ---
@app.route('/api/predict', methods=['POST'])
def predict():
    """Perform prediction"""
    data = request.get_json()
    file_path = data.get('file_path')
    lookback = int(data.get('lookback', 400))
    pred_len = int(data.get('pred_len', 120))
    temperature = float(data.get('temperature', 1.0))
    top_p = float(data.get('top_p', 0.9))
    sample_count = int(data.get('sample_count', 1))

    df, error = load_data_file(file_path)
    ...
    predictor = model_runtime.get_predictor()
    pred_df = predictor.predict(
        df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
        pred_len=pred_len, T=temperature, top_p=top_p, sample_count=sample_count,
    )
    ...
```

---

## Puntos clave para la migración (resumen para el arquitecto)

1. **El contrato a reemplazar es `KronosPredictor`** (`model/kronos.py`): `predict(df, x_timestamp, y_timestamp, pred_len, T, top_k, top_p, sample_count)` → DataFrame con columnas `open/high/low/close/volume/amount` indexado por `y_timestamp`, y `predict_samples(...)` → `(mean_df, [sample_df, ...])` (la dispersión entre muestras es la señal de incertidumbre que usa `portfolio/signals.py`).
2. **`model/runtime.py` ya selecciona MPS automáticamente** (`torch.backends.mps.is_available()`) — el patrón de selección de device se puede portar casi igual.
3. **El punto de integración real es `portfolio/forecasting.py::run_forecast_job`**: arma el input (`build_lookback_df` + `build_model_input`, con la conversión ARS→USD vía CCL histórico), llama al predictor, y aplica `_apply_continuity_correction` — un parche puntual al sesgo de normalización-por-ventana de Kronos (documentado y reproducido en `tests/test_kronos_ars_bias.py`) que probablemente no haga falta con Chronos-Bolt/Moirai, pero hay que decidirlo explícitamente, no asumir.
4. **`portfolio/forecast_worker.py`** serializa toda inferencia en un único thread/cola porque hoy es una sola instancia de modelo en un solo device — al mover esto a FastAPI hay que decidir si se mantiene ese serializado (más simple, un solo worker) o se permite concurrencia real (requeriría que el nuevo modelo soporte eso en MPS sin pisarse).
5. **`signal_config`** (tabla SQLite) trae todos los hiperparámetros de inferencia (`default_lookback=400`, `default_pred_len=15`, `default_sample_count=60`, `default_temperature=1.0`, `default_top_p=0.9`, `benchmark_seconds_per_step=0.076` medido en MPS) — son el punto de partida realista para re-benchmarkear con el modelo nuevo.
