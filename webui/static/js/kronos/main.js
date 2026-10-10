import { initModelsView, loadAvailableModels } from './models.js';
import { initLiveDataView } from './live-data.js';
import { initDataFilesView, loadDataFiles } from './data-files.js';
import { initTimeSlider } from './time-slider.js';
import { initPredictView } from './predict.js';

document.addEventListener('DOMContentLoaded', async () => {
    initModelsView();
    initLiveDataView();
    initDataFilesView();
    initTimeSlider();
    initPredictView();

    await Promise.all([loadAvailableModels(), loadDataFiles()]);
});
