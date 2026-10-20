import { $ } from '../shared/dom.js';
import { showStatus, setLoading } from '../shared/status.js';
import { api } from './api.js';
import { state } from './state.js';

export async function loadAvailableModels() {
    try {
        const { data } = await api.availableModels();
        if (data.model_available) {
            state.availableModels = data.models;
            populateModelSelect();
        } else {
            showStatus('model-status', 'warning', 'La librería de Kronos no está disponible, se usará una predicción simulada');
        }
    } catch {
        showStatus('model-status', 'error', 'No se pudieron cargar los modelos disponibles');
    }
}

function populateModelSelect() {
    const select = $('model-select');
    select.innerHTML = '<option value="">Elegí un modelo</option>';
    Object.entries(state.availableModels).forEach(([key, model]) => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = `${model.name} (${model.params}) - ${model.description}`;
        select.appendChild(option);
    });
}

// Ver conversación/auditoría, hallazgo 2.7: "no se ve el estado del modelo
// después de apretar 'Cargar modelo'" -- #model-status/#loading existen y SÍ
// se actualizan, pero en una página larga pueden quedar fuera de la vista
// actual del usuario sin este scroll.
function focusStatus() {
    $('model-status').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function loadModel() {
    focusStatus();
    const modelKey = $('model-select').value;
    const device = $('device-select').value;
    if (!modelKey) {
        showStatus('model-status', 'error', 'Elegí un modelo para cargar');
        return;
    }

    try {
        $('load-model-btn').disabled = true;
        setLoading('loading', true);
        const { data } = await api.loadModel({ model_key: modelKey, device });
        if (data.success) {
            state.modelLoaded = true;
            showStatus('model-status', 'success', data.message);
            updateModelStatus();
            if (state.dataFile) $('predict-btn').disabled = false;
        } else {
            showStatus('model-status', 'error', data.error);
        }
    } catch (error) {
        showStatus('model-status', 'error', `No se pudo cargar el modelo: ${error.response?.data?.error || error.message}`);
    } finally {
        setLoading('loading', false);
        $('load-model-btn').disabled = false;
    }
}

async function updateModelStatus() {
    try {
        const { data: status } = await api.modelStatus();
        if (status.loaded) {
            showStatus('model-status', 'success', `Modelo cargado: ${status.current_model.name} en ${status.current_model.device}`);
        } else if (status.available) {
            showStatus('model-status', 'info', 'Modelo disponible pero no cargado');
        } else {
            showStatus('model-status', 'warning', 'La librería del modelo no está disponible');
        }
    } catch {
        // status is best-effort; the load-model flow already surfaced any real error
    }
}

export function initModelsView() {
    $('load-model-btn').addEventListener('click', loadModel);
}
