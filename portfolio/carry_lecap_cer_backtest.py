"""Backtest historico APROXIMADO del Carry LECAP/CER (ver conversacion,
auditoria 2026-10-20/21) -- portfolio/fixed_income.py::
compute_carry_trade_rotation_signals solo puede evaluar el spread de HOY:
las fuentes de LECAPs/CER vigentes (argentinadatos.com) son snapshots en
vivo, sin historial descargable (confirmado real, ver docstring de ese
modulo: variantes tipo /historico, /letras/<fecha> devuelven 404 -- "no hay
forma de reconstruir retroactivamente el spread LECAP/CER de meses pasados
sin inventar numeros").

Esto NO es una reconstruccion exacta de esa estrategia -- es una
aproximacion historica, con 2 simplificaciones metodologicas DECLARADAS:

1. La pata "LECAP" se proxea con la tasa BADLAR de bancos privados (BCRA,
   serie real y gratuita desde 1999, confirmada con una consulta real a
   api.bcra.gob.ar/estadisticas/v4.0/monetarias/7) -- BADLAR es una tasa de
   depositos mayoristas a 30 dias, NO el rendimiento de una Letra puntual:
   no tiene el riesgo de precio/duracion que si tiene una LECAP real
   (chico para un papel corto, pero no cero). Se usa como proxy de "tasa
   nominal de corto plazo disponible en pesos", declarado como tal.
2. La regla de rotacion real compara la TEM de LECAPs VIGENTES HOY contra la
   TIR real de bonos CER vigentes HOY (relative-value, hacia adelante) --
   sin curva LECAP historica, esta aproximacion rota por RETORNO REALIZADO
   (momentum: cada REBALANCE_DAYS ruedas, se sostiene la pata que mejor
   rindio en la ventana anterior) -- metodologicamente DISTINTO de la señal
   en vivo real, nunca presentado como equivalente.

La pata "CER" SI usa precio real de mercado (TX26, data912, historial
confirmado real desde 2021-09-16) -- esto captura el riesgo real (duracion,
repricing de tasa real, shocks de inflacion) de estar en CER, a diferencia
de BADLAR que es una tasa pura sin riesgo de precio.
"""
import pandas as pd
import requests
import urllib3

from portfolio import fetchers, macro_data

BADLAR_URL = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/7"
CER_BOND_SYMBOL = "TX26"  # unico bono CER con historial real confirmado en data912 (desde 2021)
REBALANCE_DAYS = 21  # ~1 mes de ruedas -- mismo horizonte "este mes" que compute_carry_trade_rotation_signals


def fetch_badlar_history(desde, hasta):
    """Serie diaria REAL de BADLAR bancos privados (TNA %), BCRA, paginada
    (limit=1000/request, ver auditoria). Sin autenticacion. Devuelve
    DataFrame date/tna_pct ordenado, vacio si la fuente no responde (nunca
    rompe el caller)."""
    rows = []
    offset = 0
    while True:
        try:
            # verify=False: el certificado TLS de api.bcra.gob.ar es invalido
            # (confirmado real -- curl -k hizo falta la primera vez que se
            # encontro esta fuente). Es una API publica de solo lectura
            # (tasas de interes, sin datos sensibles ni credenciales en la
            # request), riesgo de MITM bajo -- igual se declara explicito en
            # vez de dejarlo en silencio, y se apaga el warning de urllib3
            # para no spamear logs si esto corre por cron.
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = requests.get(
                BADLAR_URL, params={"desde": desde, "hasta": hasta, "offset": offset},
                timeout=20, verify=False,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            break
        detalle = data.get("results", [{}])[0].get("detalle", [])
        if not detalle:
            break
        rows.extend(detalle)
        meta = data.get("metadata", {}).get("resultset", {})
        offset += meta.get("limit", len(detalle))
        if offset >= meta.get("count", 0):
            break

    if not rows:
        return pd.DataFrame(columns=["date", "tna_pct"])
    df = pd.DataFrame(rows).rename(columns={"fecha": "date", "valor": "tna_pct"})
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def _badlar_daily_return_pct(tna_pct_series):
    """Retorno diario SIMPLE prorateado de la TNA, en las mismas unidades
    "percent" que espera daily_returns_pct en todo el proyecto (ver
    portfolio/backtest.py::_compute_engine_metrics, que divide por 100 para
    recuperar la fraccion) -- TNA/365, NUNCA TNA/100/365 (esa escala, usada
    hoy en portfolio/yield_engine.py::daily_caucion_return_pct, da un
    retorno compuesto ~100x mas chico que el real cuando se la trata como
    daily_returns_pct -- confirmado empiricamente en esta auditoria, ver
    reporte; queda fuera del alcance de este archivo arreglarlo)."""
    return tna_pct_series / 365.0


def simulate_carry_lecap_cer_engine(conn, cer_symbol=CER_BOND_SYMBOL, rebalance_days=REBALANCE_DAYS,
                                     commission_pct=0.0):
    """Simulador historico APROXIMADO (ver docstring del modulo) -- mismo
    shape de salida que el resto de los motores del proyecto:
    {"engine": "carry_lecap_cer", "daily_returns_pct": pd.Series, "idle_weight":
    pd.Series, "trades": [...]}. Devuelve series vacias si falta cualquiera
    de las 2 fuentes reales (nunca inventa datos).

    commission_pct (ver conversacion 2026-10-21, "investigacion en paralelo"):
    costo de rotacion real -- CADA rotacion implica vender una pata y comprar
    la otra de verdad (sea CER->efectivo/corto-plazo o al reves), nunca es
    gratis. Sin tarifa especifica de "rotacion LECAP/CER" confirmada en
    ningun tarifario (a diferencia de Canje, que SI tiene una linea explicita
    de "Canjes de Titulos Valores") -- se usa como piso declarado
    backtest.ROUND_TRIP_COMMISSION_PCT (2.5%, la comision real estandar de
    bonos/acciones de IOL/PPI, misma que ya usa Factor Investing para sus
    rotaciones), nunca inventado aparte. Se cobra UNA vez el dia de cada
    rotacion (mismo criterio contable que Canje/Carry cripto/Factor)."""
    cer_df = fetchers.fetch_data912_historical(cer_symbol, "bono")
    if cer_df is None or cer_df.empty:
        empty = pd.Series(dtype=float)
        return {"engine": "carry_lecap_cer", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}
    cer_df = cer_df.sort_values("timestamps").reset_index(drop=True)
    cer_df["date"] = cer_df["timestamps"].dt.normalize()

    desde = cer_df["date"].min().strftime("%Y-%m-%d")
    hasta = cer_df["date"].max().strftime("%Y-%m-%d")
    badlar_df = fetch_badlar_history(desde, hasta)
    if badlar_df.empty:
        empty = pd.Series(dtype=float)
        return {"engine": "carry_lecap_cer", "daily_returns_pct": empty, "idle_weight": empty, "trades": []}

    # Alineacion por interseccion de fechas donde AMBAS patas tienen dato real
    # (nunca forward-fill entre patas -- mismo criterio que portfolio/
    # pairs_trading.py::_align_pair).
    cer_close = cer_df.set_index("date")["close"]
    badlar_tna = badlar_df.set_index("date")["tna_pct"]
    common_dates = cer_close.index.intersection(badlar_tna.index)
    cer_close = cer_close.loc[common_dates].sort_index()
    badlar_tna = badlar_tna.loc[common_dates].sort_index()

    cer_daily_ret_pct = cer_close.pct_change() * 100
    badlar_daily_ret_pct = _badlar_daily_return_pct(badlar_tna)

    # Rotacion por retorno REALIZADO (momentum, ver docstring del modulo):
    # cada rebalance_days ruedas, se sostiene durante el proximo tramo la
    # pata que mejor rindio compuesta en el tramo anterior. Primer tramo
    # (sin historial previo) arranca en BADLAR (la pata de menor riesgo,
    # nunca se asume CER por default).
    n = len(common_dates)
    position = []  # "BADLAR" o "CER" por rueda
    current = "BADLAR"
    current_since = 0  # indice donde arranco el tramo actual -- para armar return_pct al rotar
    trades = []
    for i in range(n):
        if i > 0 and i % rebalance_days == 0:
            window_start = max(0, i - rebalance_days)
            cer_window_ret = (1 + cer_daily_ret_pct.iloc[window_start:i].fillna(0) / 100).prod() - 1
            badlar_window_ret = (1 + badlar_daily_ret_pct.iloc[window_start:i].fillna(0) / 100).prod() - 1
            new_position = "CER" if cer_window_ret > badlar_window_ret else "BADLAR"
            if new_position != current:
                # return_pct del tramo que se cierra (ver conversacion: sin
                # este campo, backtest.py::run_strategy_cpcv_validation
                # filtra "trades" por `"return_pct" in t` y reporta n_trades=0
                # aunque SI hubo rotaciones reales -- mismo criterio de
                # reporte que Canje/pairs_trading, nunca un numero inventado).
                held_ret_pct = cer_daily_ret_pct if current == "CER" else badlar_daily_ret_pct
                segment_return_pct = (1 + held_ret_pct.iloc[current_since:i].fillna(0) / 100).prod() * 100 - 100
                trades.append({
                    "engine": "carry_lecap_cer", "date": common_dates[i],
                    "from": current, "to": new_position,
                    "entry_date": common_dates[current_since].date().isoformat(),
                    "exit_date": common_dates[i].date().isoformat(),
                    "return_pct": round(float(segment_return_pct), 3),
                })
                current_since = i
            current = new_position
        position.append(current)

    position_sr = pd.Series(position, index=common_dates)
    daily_returns_pct = cer_daily_ret_pct.where(position_sr == "CER", badlar_daily_ret_pct)
    daily_returns_pct = daily_returns_pct.fillna(0.0)

    # Comision de rotacion, restada UNA vez el dia de cada cambio de pata
    # (ver docstring de arriba) -- trade["date"] es exactamente el dia `i`
    # donde new_position != current, el mismo indice que common_dates[i].
    if commission_pct and trades:
        trade_dates = {t["date"] for t in trades}
        for date in trade_dates:
            daily_returns_pct.loc[date] -= commission_pct

    return {
        "engine": "carry_lecap_cer",
        "daily_returns_pct": daily_returns_pct,
        "idle_weight": pd.Series(0.0, index=common_dates),  # siempre posicionado en una de las 2 patas, nunca ocioso
        "trades": trades,
    }


def simulate_carry_lecap_cer_engine_usd(conn, cer_symbol=CER_BOND_SYMBOL, rebalance_days=REBALANCE_DAYS,
                                         commission_pct=0.0):
    """Igual que simulate_carry_lecap_cer_engine pero convertido a USD via
    CCL historico real (portfolio/macro_data.py::get_ccl_series) -- la vara
    de OBJETIVO_MARCO.md es rendimiento en USD, nunca ARS nominal."""
    result = simulate_carry_lecap_cer_engine(conn, cer_symbol, rebalance_days, commission_pct=commission_pct)
    returns_ars = result["daily_returns_pct"]
    if not len(returns_ars):
        return result

    ccl = macro_data.get_ccl_series(conn)
    ccl_aligned = ccl.reindex(returns_ars.index).ffill()
    valid = ccl_aligned.notna() & (ccl_aligned > 0)

    equity_ars = (1 + returns_ars[valid].to_numpy() / 100).cumprod()
    equity_usd = equity_ars / ccl_aligned[valid].to_numpy()
    equity_usd = equity_usd / equity_usd[0]  # normaliza a 1.0 el primer dia valido
    usd_returns_pct = pd.Series(equity_usd, index=returns_ars[valid].index).pct_change().fillna(0.0) * 100

    return {
        "engine": "carry_lecap_cer",
        "daily_returns_pct": usd_returns_pct,
        "idle_weight": result["idle_weight"].reindex(usd_returns_pct.index).fillna(0.0),
        "trades": result["trades"],
    }
