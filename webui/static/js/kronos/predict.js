import { $ } from '../shared/dom.js';
import { showStatus, setLoading } from '../shared/status.js';
import { fmt } from '../shared/format.js';
import { renderPredictorChart } from '../charts/predictor-forecast-chart.js';
import { api } from './api.js';
import { state } from './state.js';
import { getSelectedStartDate } from './time-slider.js';

// Reemplazada en cada prediccion (nunca actualizada in-place vía setData) --
// mismo patron que instrument-detail.js::paintChartAndWireAlerts: se limpia
// con chart.remove() antes de pintar la siguiente, para no acumular
// ResizeObservers/listeners viejos sobre el mismo <div id="chart">.
let forecastChart = null;

function predictionQuality(predictions, actuals) {
    const n = Math.min(predictions.length, actuals.length);
    if (!n) return { mae: 0, rmse: 0, mape: 0 };

    let mae = 0, sqErr = 0, mape = 0;
    for (let i = 0; i < n; i += 1) {
        const error = Math.abs(predictions[i].close - actuals[i].close);
        mae += error;
        sqErr += error * error;
        mape += (error / actuals[i].close) * 100;
    }
    return { mae: mae / n, rmse: Math.sqrt(sqErr / n), mape: mape / n };
}

function fillComparisonTable(predictions, actuals) {
    const n = Math.min(predictions.length, actuals.length);
    const rows = [];
    for (let i = 0; i < n; i += 1) {
        const p = predictions[i];
        const a = actuals[i];
        rows.push(`<tr>
            <td>${new Date(p.timestamp).toLocaleString()}</td>
            <td class="num">${fmt(a.open, 4)}</td><td class="num">${fmt(p.open, 4)}</td>
            <td class="num">${fmt(a.high, 4)}</td><td class="num">${fmt(p.high, 4)}</td>
            <td class="num">${fmt(a.low, 4)}</td><td class="num">${fmt(p.low, 4)}</td>
            <td class="num">${fmt(a.close, 4)}</td><td class="num">${fmt(p.close, 4)}</td>
        </tr>`);
    }
    $('comparison-tbody').innerHTML = rows.join('');
}

function displayComparisonAnalysis(result) {
    $('comparison-section').style.display = 'block';
    $('prediction-type').textContent = result.prediction_type;
    $('comparison-data').textContent = `${result.actual_data.length} actual data points`;

    const quality = predictionQuality(result.prediction_results, result.actual_data);
    $('mae').textContent = quality.mae.toFixed(4);
    $('rmse').textContent = quality.rmse.toFixed(4);
    $('mape').textContent = quality.mape.toFixed(2);

    fillComparisonTable(result.prediction_results, result.actual_data);
}

function displayPredictionResult(result) {
    if (forecastChart) forecastChart.remove();
    forecastChart = renderPredictorChart($('chart'), result.chart);

    if (result.has_comparison) displayComparisonAnalysis(result);
    else $('comparison-section').style.display = 'none';
}

async function startPrediction() {
    if (!state.dataFile) return showStatus('model-status', 'error', 'Cargá un archivo de datos primero');
    if (!state.modelLoaded) return showStatus('model-status', 'error', 'Cargá un modelo primero');
    if (!state.slider) return showStatus('model-status', 'error', 'La ventana temporal todavía no se inicializó');

    try {
        setLoading('loading', true);
        $('predict-btn').disabled = true;

        const payload = {
            file_path: state.dataFile,
            lookback: parseInt($('lookback').value, 10),
            pred_len: parseInt($('pred-len').value, 10),
            start_date: getSelectedStartDate().toISOString().slice(0, 16),
            temperature: parseFloat($('temperature').value),
            top_p: parseFloat($('top-p').value),
            sample_count: parseInt($('sample-count').value, 10),
        };

        const { data } = await api.predict(payload);
        if (data.success) {
            displayPredictionResult(data);
            showStatus('model-status', 'success', data.message);
        } else {
            showStatus('model-status', 'error', data.error);
        }
    } catch (error) {
        showStatus('model-status', 'error', `La predicción falló: ${error.response?.data?.error || error.message}`);
    } finally {
        setLoading('loading', false);
        $('predict-btn').disabled = false;
    }
}

export function initPredictView() {
    $('predict-btn').addEventListener('click', startPrediction);
    $('temperature').addEventListener('input', function () {
        $('temperature-value').textContent = this.value;
    });
    $('top-p').addEventListener('input', function () {
        $('top-p-value').textContent = this.value;
    });
}
