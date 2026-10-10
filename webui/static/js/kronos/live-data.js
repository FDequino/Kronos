import { $ } from '../shared/dom.js';
import { showStatus, setLoading } from '../shared/status.js';
import { api } from './api.js';
import { loadDataFiles } from './data-files.js';

function buildPayload() {
    const source = $('live-source-select').value;
    if (source === 'binance') {
        return {
            source,
            symbol: $('live-symbol').value.trim(),
            interval: $('live-interval-binance').value,
            limit: 1000,
        };
    }
    return {
        source,
        ticker: $('live-ticker').value.trim(),
        period: $('live-period').value,
        interval: '1d',
    };
}

async function fetchLiveData() {
    const btn = $('fetch-live-btn');
    try {
        btn.disabled = true;
        setLoading('loading', true);
        const { data } = await api.fetchLiveData(buildPayload());
        if (data.success) {
            showStatus('model-status', 'success', data.message);
            await loadDataFiles();
            $('data-file-select').value = data.file_path;
        } else {
            showStatus('model-status', 'error', data.error);
        }
    } catch (error) {
        showStatus('model-status', 'error', `No se pudieron traer los datos en vivo: ${error.response?.data?.error || error.message}`);
    } finally {
        setLoading('loading', false);
        btn.disabled = false;
    }
}

export function initLiveDataView() {
    $('fetch-live-btn').addEventListener('click', fetchLiveData);
    $('live-source-select').addEventListener('change', function () {
        const isBinance = this.value === 'binance';
        $('binance-fields').style.display = isBinance ? 'block' : 'none';
        $('yfinance-fields').style.display = isBinance ? 'none' : 'block';
    });
}
