"""Universo buscable de instrumentos BYMA, cacheado en memoria para no pegarle
a data912 en cada tecla del buscador del Explorador.
"""
import time

from portfolio import fetchers

_TTL_SECONDS = 300
_ENDPOINT_BY_TYPE = {
    "arg_stocks": "accion",
    "arg_cedears": "cedear",
    "arg_bonds": "bono",
}

_cache = {"rows": None, "fetched_at": 0.0}


def _load():
    if _cache["rows"] is not None and time.time() - _cache["fetched_at"] < _TTL_SECONDS:
        return _cache["rows"]

    rows = []
    for endpoint, asset_type in _ENDPOINT_BY_TYPE.items():
        for item in fetchers.fetch_data912_live(endpoint):
            rows.append({
                "symbol": item["symbol"],
                "type": asset_type,
                "market": "byma",
                "price": item["c"],
                "pct_change": item.get("pct_change"),
            })

    _cache["rows"] = rows
    _cache["fetched_at"] = time.time()
    return rows


def search(query, type_filter=None, limit=20):
    query = (query or "").strip().upper()
    rows = _load()
    if type_filter:
        rows = [r for r in rows if r["type"] == type_filter]
    if query:
        rows = [r for r in rows if query in r["symbol"]]
    rows.sort(key=lambda r: (not r["symbol"].startswith(query), r["symbol"]))
    return rows[:limit]
