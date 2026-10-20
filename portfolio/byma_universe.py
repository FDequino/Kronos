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


def augment_with_catalog_name_matches(conn, results, query, type_filter=None, limit=20):
    """Suma al resultado de `search` instrumentos del catalogo DEL USUARIO
    (instruments.name) cuyo NOMBRE matchea la busqueda (ver conversacion/
    auditoria 19/10/2026, hallazgo 2.5: "galicia" no encontraba GGAL) --
    data912/esta API solo expone simbolos, nunca nombres de empresa, asi
    que esto cubre UNICAMENTE instrumentos ya en el catalogo (cargados a
    mano o por import de reporte de broker); el resto de BYMA sigue sin
    poder buscarse por nombre, no hay una fuente gratuita de esos nombres
    para todo el universo (pendiente de diseno, no se inventa una).
    Instrumentos que ya vinieron por simbolo en `results` no se duplican.
    price/pct_change quedan None para los que se suman aca (no se inventa
    una cotizacion que `search` no trajo)."""
    query_norm = (query or "").strip()
    if not query_norm or len(query_norm) < 2:
        return results
    rows = conn.execute(
        "SELECT symbol, type, market FROM instruments WHERE name LIKE ? COLLATE NOCASE",
        (f"%{query_norm}%",),
    ).fetchall()
    existing_symbols = {r["symbol"] for r in results}
    augmented = list(results)
    for row in rows:
        if type_filter and row["type"] != type_filter:
            continue
        if row["symbol"] in existing_symbols:
            continue
        augmented.append({
            "symbol": row["symbol"], "type": row["type"], "market": row["market"],
            "price": None, "pct_change": None,
        })
        existing_symbols.add(row["symbol"])
    return augmented[:limit]
