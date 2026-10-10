const POLL_INTERVAL_MS = 2000;

export function pollForecast(url, { onProgress, onDone, onError }) {
    const tick = async () => {
        try {
            const { data } = await axios.get(url);
            if (!data) return onError(new Error('Forecast no encontrado'));

            if (data.status === 'error') return onError(new Error(data.error_message || 'El forecast falló'));
            if (data.status === 'done') return onDone(data);

            onProgress(data.progress_pct ?? 0);
            setTimeout(tick, POLL_INTERVAL_MS);
        } catch (error) {
            onError(error);
        }
    };
    tick();
}

/** Sondea un lote de forecasts (p.ej. todas las corridas de un backtest) hasta
 * que todos terminen (done o error), informando cuántos van quedando. */
export function pollBatch(forecastIds, { onProgress, onDone }) {
    if (!forecastIds.length) {
        onDone({ total: 0, done: 0, errors: 0 });
        return;
    }
    let settled = 0;
    let errors = 0;
    const total = forecastIds.length;
    onProgress({ total, done: 0, errors: 0 });
    forecastIds.forEach((id) => {
        pollForecast(`/api/portfolio/forecasts/${id}`, {
            onProgress: () => {},
            onDone: () => {
                settled += 1;
                onProgress({ total, done: settled, errors });
                if (settled === total) onDone({ total, done: settled, errors });
            },
            onError: () => {
                settled += 1;
                errors += 1;
                onProgress({ total, done: settled, errors });
                if (settled === total) onDone({ total, done: settled, errors });
            },
        });
    });
}
