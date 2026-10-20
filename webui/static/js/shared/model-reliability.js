// Banner compartido entre Comparador y Detalle de instrumento (ver
// conversación/auditoría: "la baja confiabilidad del modelo... visible en
// TODAS las pantallas de señales, no solo internamente") -- un solo texto,
// alimentado por GET /api/portfolio/model-reliability
// (portfolio/accuracy.py::compute_model_reliability_summary). Deliberadamente
// NO se agrega a Oportunidades: esa pantalla muestra señales del motor
// Core/Satélite (reglas cuantitativas con backtest), que no usan el modelo
// predictivo Kronos -- mostrar ahí "el modelo acierta poco" sería atribuirle
// a esas señales un problema que no es el suyo.
export function modelReliabilityBannerHtml(summary) {
    if (!summary || !summary.evaluated_count || !summary.low_accuracy_count) return '';
    return `
        <p class="hint cell-warning" style="margin-bottom:12px;">
            ⚠ ${summary.low_accuracy_count} de ${summary.evaluated_count} instrumentos con historial evaluado tienen hoy
            un acierto direccional por debajo del 50% (peor que cara-o-ceca). Tratá cualquier pronóstico "Sin validar"
            con cautela.
        </p>
    `;
}
