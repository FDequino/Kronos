import { $ } from '../shared/dom.js';
import { showStatus } from '../shared/status.js';
import { state } from './state.js';

const WINDOW_SIZE = 520;

let isDragging = false;
let activeHandle = null;

function windowPercentage() {
    const totalRows = state.slider ? state.slider.totalRows : 1000;
    return WINDOW_SIZE / totalRows;
}

function updateStartHandle(percentage) {
    const pct = Math.min(percentage, 1 - windowPercentage());
    $('start-handle').style.left = `${pct * 100}%`;
    $('slider-selection').style.left = `${pct * 100}%`;
    $('slider-selection').style.width = `${windowPercentage() * 100}%`;
    $('end-handle').style.left = `${(pct + windowPercentage()) * 100}%`;
}

function updateEndHandle(percentage) {
    const pct = Math.max(percentage, windowPercentage());
    $('end-handle').style.left = `${pct * 100}%`;
    $('slider-selection').style.left = `${(pct - windowPercentage()) * 100}%`;
    $('slider-selection').style.width = `${windowPercentage() * 100}%`;
    $('start-handle').style.left = `${(pct - windowPercentage()) * 100}%`;
}

function updateSliderFromHandles() {
    if (!state.slider) return;
    const startPct = parseFloat($('start-handle').style.left) / 100;
    const endPct = parseFloat($('end-handle').style.left) / 100;

    const totalTime = state.slider.endDate.getTime() - state.slider.startDate.getTime();
    const startDate = new Date(state.slider.startDate.getTime() + totalTime * startPct);
    const endDate = new Date(state.slider.startDate.getTime() + totalTime * endPct);

    $('window-start').textContent = `Inicio: ${startDate.toLocaleDateString()}`;
    $('window-end').textContent = `Fin: ${endDate.toLocaleDateString()}`;
    $('window-size').textContent = `Ventana: 400 + 120 = ${WINDOW_SIZE} puntos (fija)`;
    $('lookback').value = 400;
    $('pred-len').value = 120;
}

export function updateSliderFromInputs() {
    if (!state.slider) return;
    const totalRows = state.slider.totalRows;
    if (WINDOW_SIZE > totalRows) {
        showStatus('model-status', 'error', `Datos insuficientes: se necesitan al menos ${WINDOW_SIZE} puntos, hay ${totalRows}`);
        return;
    }

    const startPct = 0.1;
    updateStartHandle(startPct);
    updateEndHandle(startPct + windowPercentage());
    updateSliderFromHandles();
}

export function initializeTimeWindowSlider(dataInfo) {
    state.slider = {
        startDate: new Date(dataInfo.start_date),
        endDate: new Date(dataInfo.end_date),
        totalRows: dataInfo.rows,
        timeframe: dataInfo.timeframe,
    };
    $('min-label').textContent = dataInfo.start_date.split('T')[0];
    $('max-label').textContent = dataInfo.end_date.split('T')[0];
    updateSliderFromInputs();
}

export function getSelectedStartDate() {
    const startPct = parseFloat($('start-handle').style.left) / 100;
    const totalTime = state.slider.endDate.getTime() - state.slider.startDate.getTime();
    return new Date(state.slider.startDate.getTime() + totalTime * startPct);
}

export function initTimeSlider() {
    const track = document.querySelector('.slider-track');

    $('start-handle').addEventListener('mousedown', (e) => {
        isDragging = true;
        activeHandle = 'start';
        e.preventDefault();
    });

    $('end-handle').addEventListener('mousedown', (e) => {
        isDragging = true;
        activeHandle = 'end';
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const rect = track.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        if (activeHandle === 'start') updateStartHandle(pct);
        else updateEndHandle(pct);
        updateSliderFromHandles();
    });

    document.addEventListener('mouseup', () => {
        isDragging = false;
        activeHandle = null;
    });

    track.addEventListener('click', (e) => {
        const rect = track.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        const startRect = $('start-handle').getBoundingClientRect();
        const endRect = $('end-handle').getBoundingClientRect();
        const clickX = e.clientX - rect.left;
        if (Math.abs(clickX - (startRect.left - rect.left)) < Math.abs(clickX - (endRect.left - rect.left))) {
            updateStartHandle(pct);
        } else {
            updateEndHandle(pct);
        }
        updateSliderFromHandles();
    });

    $('lookback').addEventListener('input', updateSliderFromInputs);
    $('pred-len').addEventListener('input', updateSliderFromInputs);
}
