"""Script de investigacion EXPLORATORIO (no runtime, no production) -- ver
OBJETIVO_MARCO.md, "Como hacer que el resto funcione": el Core (Chronos-Bolt
point-forecast) no muestra DSR significativo. La literatura externa citada
en el relevamiento (Pretrained Time-Series Foundation Models for Financial
Return Forecasting, arxiv 2606.27100) encontro a TimesFM-2.5/Moirai-2.0
ganandole a Chronos en varios tickers de EEUU -- esto compara los 3 (TimesFM
2.5 200M, Chronos-Bolt-small, naive/sin-cambio) sobre datos REALES del
universo del proyecto, al mismo horizonte (15 ruedas) que usa Core.

Instalacion probada real (2026-10-21): `pip install "timesfm[torch]"` --
Apache-2.0, ~200M params, carga en ~40s y forecastea en <1s en este hardware
(Mac M4). Instalacion limpia, sin conflicto de dependencias con el venv
principal (numpy/huggingface_hub/safetensors/torch ya presentes).

Resultado real obtenido (4 tickers, error absoluto % al paso 15 contra el
precio real, NO es un backtest CPCV/DSR completo -- una corrida sola por
ticker, no hay walk-forward ni significancia estadistica medida):

  Ticker | TimesFM | Chronos | Naive (sin cambio)
  GGAL   |  3.77%  |  2.45%  |  4.16%
  AAPL   |  0.39%  |  3.37%  |  0.87%
  SPY    |  0.81%  |  2.25%  |  0.96%
  YPFD   |  8.16%  |  8.31%  |  1.03%

Conclusion de esta corrida: NO hay una ventaja clara y consistente de
TimesFM sobre Chronos-Bolt en este universo puntual -- gana en 2/4, pierde
en 1/4, empata en 1/4. El baseline naive (sin cambio) le gana a AMBOS
modelos en YPFD por mucho margen, y es competitivo en AAPL/SPY -- consistente
con el hallazgo ya documentado (DSR=0.0073 del Core): a este horizonte, el
forecasting de precio puntual sigue pareciendose bastante a un paseo
aleatorio, sea cual sea el modelo. Para una conclusion con peso estadistico
real haria falta correr esto mismo dentro de run_strategy_cpcv_validation
(CPCV + Deflated Sharpe Ratio) sobre TODO el universo, no 4 tickers sueltos
-- no se hizo aca por alcance/tiempo de esta investigacion puntual.
"""
import time

import timesfm

from model.local_client import LocalChronosClient
from portfolio import backtest, db
from portfolio.forecasting import _future_timestamps

LOOKBACK = 400
HORIZON = 15
TICKERS = [("GGAL", "accion"), ("AAPL", "cedear"), ("SPY", "cedear"), ("YPFD", "accion")]


def _pct_error(predicted, actual):
    return abs(predicted - actual) / actual * 100


def main():
    print("Cargando TimesFM-2.5 (200M, google/timesfm-2.5-200m-pytorch)...")
    t0 = time.time()
    timesfm_model = timesfm.TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
    timesfm_model.compile(timesfm.ForecastConfig(
        max_context=1024, max_horizon=256, normalize_inputs=True, use_continuous_quantile_head=True,
    ))
    print(f"-- listo ({time.time() - t0:.1f}s)")

    print("Cargando Chronos-Bolt-small (en proceso, local)...")
    t0 = time.time()
    chronos_client = LocalChronosClient()
    print(f"-- listo ({time.time() - t0:.1f}s)")

    conn = db.get_connection()
    print(f"\n{'Ticker':8s} | {'TimesFM':>8s} | {'Chronos':>8s} | {'Naive':>8s}")
    for symbol, asset_type in TICKERS:
        df = backtest.fetch_ticker_history(symbol, asset_type, conn=conn)
        closes = df["close"].to_numpy(dtype=float)
        ctx_df = df.iloc[-(LOOKBACK + HORIZON):-HORIZON].reset_index(drop=True)
        context = ctx_df["close"].to_numpy(dtype=float)
        actual_future = closes[-HORIZON:]
        last_close = context[-1]

        point_forecast, _ = timesfm_model.forecast(horizon=HORIZON, inputs=[context])
        err_timesfm = _pct_error(point_forecast[0][-1], actual_future[-1])

        x_timestamp = ctx_df["timestamps"]
        y_timestamp = _future_timestamps(x_timestamp, HORIZON)
        mean_df, _ = chronos_client.predict_samples(
            ctx_df[["open", "high", "low", "close", "volume"]], x_timestamp, y_timestamp, HORIZON, sample_count=10,
        )
        err_chronos = _pct_error(mean_df["close"].to_numpy()[-1], actual_future[-1])

        err_naive = _pct_error(last_close, actual_future[-1])
        print(f"{symbol:8s} | {err_timesfm:7.2f}% | {err_chronos:7.2f}% | {err_naive:7.2f}%")


if __name__ == "__main__":
    main()
