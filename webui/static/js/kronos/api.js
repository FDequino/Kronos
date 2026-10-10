const BASE = '/api';

export const api = {
    availableModels: () => axios.get(`${BASE}/available-models`),
    loadModel: (payload) => axios.post(`${BASE}/load-model`, payload),
    modelStatus: () => axios.get(`${BASE}/model-status`),
    fetchLiveData: (payload) => axios.post(`${BASE}/fetch-live-data`, payload),
    dataFiles: () => axios.get(`${BASE}/data-files`),
    loadData: (payload) => axios.post(`${BASE}/load-data`, payload),
    predict: (payload) => axios.post(`${BASE}/predict`, payload),
};
