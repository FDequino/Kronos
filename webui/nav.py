"""Single source of truth for sidebar navigation, shared by every page template."""

NAV_ITEMS = [
    {"id": "resumen", "label": "Resumen", "href": "/portfolio#resumen", "icon": "overview"},
    {"id": "decisiones", "label": "Decisiones", "href": "/portfolio#decisiones", "icon": "target"},
    {"id": "instrumentos", "label": "Instrumentos", "href": "/portfolio#instrumentos", "icon": "layers"},
    {"id": "transacciones", "label": "Transacciones", "href": "/portfolio#transacciones", "icon": "swap"},
    {"id": "plazos-fijos", "label": "Plazos Fijos", "href": "/portfolio#plazos-fijos", "icon": "vault"},
    {"id": "prestamos", "label": "Préstamos", "href": "/portfolio#prestamos", "icon": "handshake"},
    {"id": "ajustes", "label": "Ajustes", "href": "/portfolio#ajustes", "icon": "settings"},
    # Herramienta separada (jugar con el modelo "libre", CSV/Binance propio),
    # no forma parte del flujo de Cartera -- al final de la lista a proposito,
    # no es un paso mas del workflow principal (ver conversacion, Fase 1).
    {"id": "predictor", "label": "Predictor", "href": "/", "icon": "predictor"},
]

# Fase 15: el menú pasó de 10 a 6 ítems (Señales/Kronos/Propuestas se
# fusionaron en "Decisiones"; Explorador/Comparar se fusionaron en
# "Instrumentos"). Un hash viejo guardado en favoritos antes de la fusión
# sigue funcionando: tabs.js lo redirige a la vista+subtab correctos en vez
# de caer en el default silenciosamente.
# Fase 18: Propuestas y Señales volvieron a ser subtabs propios de Decisiones
# (habían convivido en "Bandeja" como tabla + sección colapsable -- eran dos
# cosas distintas, mezclarlas confundía más de lo que ahorraba).
# Nota: este dict es solo documentación -- la redirección real vive en
# webui/static/js/portfolio/tabs.js, no se lee desde Python/Jinja.
LEGACY_HASH_REDIRECTS = {
    "senales": ("decisiones", "senales"),
    "kronos": ("decisiones", "kronos"),
    "propuestas": ("decisiones", "propuestas"),
    "bandeja": ("decisiones", "propuestas"),
    "explorador": ("instrumentos", "explorador"),
    "comparar": ("instrumentos", "comparar"),
}
