import { $ } from '../shared/dom.js';
import { showStatus, setLoading } from '../shared/status.js';
import { api } from './api.js';
import { state } from './state.js';
import { initializeTimeWindowSlider } from './time-slider.js';

export async function loadDataFiles() {
    try {
        const { data: files } = await api.dataFiles();
        const select = $('data-file-select');
        select.innerHTML = '<option value="">Elegí un archivo de datos</option>';
        files.forEach((file) => {
            const option = document.createElement('option');
            option.value = file.path;
            option.textContent = `${file.name} (${file.size})`;
            select.appendChild(option);
        });
    } catch {
        showStatus('model-status', 'error', 'No se pudo cargar la lista de archivos');
    }
}

function showDataInfo(dataInfo) {
    $('data-info').style.display = 'block';
    $('data-rows').textContent = dataInfo.rows;
    $('data-cols').textContent = dataInfo.columns.length;
    $('data-time-range').textContent = `${dataInfo.start_date} → ${dataInfo.end_date}`;
    $('data-price-range').textContent = `${dataInfo.price_range.min.toFixed(4)} - ${dataInfo.price_range.max.toFixed(4)}`;
    $('data-timeframe').textContent = dataInfo.timeframe;
    $('data-prediction-cols').textContent = dataInfo.prediction_columns.join(', ');
    initializeTimeWindowSlider(dataInfo);
}

async function loadData() {
    const filePath = $('data-file-select').value;
    if (!filePath) {
        showStatus('model-status', 'error', 'Elegí un archivo de datos para cargar');
        return;
    }

    try {
        $('load-data-btn').disabled = true;
        setLoading('loading', true);
        const { data } = await api.loadData({ file_path: filePath });
        if (data.success) {
            state.dataFile = filePath;
            state.dataInfo = data.data_info;
            showDataInfo(data.data_info);
            showStatus('model-status', 'success', data.message);
            if (state.modelLoaded) $('predict-btn').disabled = false;
        } else {
            showStatus('model-status', 'error', data.error);
        }
    } catch (error) {
        showStatus('model-status', 'error', `No se pudieron cargar los datos: ${error.response?.data?.error || error.message}`);
    } finally {
        setLoading('loading', false);
        $('load-data-btn').disabled = false;
    }
}

export function initDataFilesView() {
    $('load-data-btn').addEventListener('click', loadData);
}
