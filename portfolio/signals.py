"""Traduce un forecast de Kronos en una senial simple + su explicacion.

Es una heuristica de umbral sobre el % que pronostica el modelo, nunca
asesoramiento financiero real -- eso se deja explicito en explain().
"""
import numpy as np


def compute_signal(last_close, pred_mean_df, sample_dfs, config):
    pred_close = pred_mean_df["close"].iloc[-1]
    pred_change_pct = (pred_close - last_close) / last_close * 100

    uncertainty_pct = None
    if sample_dfs and len(sample_dfs) > 1:
        sample_changes = [
            (s["close"].iloc[-1] - last_close) / last_close * 100 for s in sample_dfs
        ]
        uncertainty_pct = float(np.std(sample_changes))

    if pred_change_pct >= config["buy_threshold_pct"]:
        signal = "comprar"
    elif pred_change_pct <= config["sell_threshold_pct"]:
        signal = "vender"
    else:
        signal = "mantener"

    return signal, float(pred_change_pct), uncertainty_pct


def explain(symbol, pred_change_pct, uncertainty_pct, signal, pred_len, config):
    direccion = "suba" if pred_change_pct >= 0 else "baja"
    base = (
        f"Kronos pronostica una {direccion} del {abs(pred_change_pct):.1f}% en {symbol} "
        f"a {pred_len} pasos. Umbral configurado: compra desde +{config['buy_threshold_pct']:.1f}%, "
        f"venta desde {config['sell_threshold_pct']:.1f}% -> senial: {signal}."
    )
    if uncertainty_pct is not None:
        base += (
            f" Las muestras del modelo difieren entre si en torno a {uncertainty_pct:.1f} puntos "
            f"porcentuales -- cuanto mas alto, menos de fiar es el numero puntual."
        )
    base += " Esto es una heuristica sobre un pronostico estadistico, no es asesoramiento financiero."
    return base
