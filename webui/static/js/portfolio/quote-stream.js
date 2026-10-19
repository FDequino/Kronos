// Fase 7: una sola conexion SSE para toda la pagina -- holdings.js y
// resumen-v2.js se suscriben con onQuoteUpdate(cb) en vez de que cada uno
// abra su propio EventSource (el browser limita las conexiones HTTP
// concurrentes por origen; duplicar esto las agotaria rapido con otras
// pestañas/requests abiertas). Ver portfolio/quote_worker.py del lado server.
let source = null;
let lastEventAt = null;
const listeners = new Set();

export function initQuoteStream() {
    if (source) return;
    source = new EventSource('/api/portfolio/stream');
    source.addEventListener('quote', (e) => {
        lastEventAt = Date.now();
        let data;
        try {
            data = JSON.parse(e.data);
        } catch {
            return;
        }
        listeners.forEach((cb) => cb(data));
    });
    // EventSource reconecta solo ante un corte -- no hace falta logica propia
    // de retry aca, es nativo del protocolo SSE.
}

export function onQuoteUpdate(cb) {
    listeners.add(cb);
    return () => listeners.delete(cb);
}

export function lastQuoteEventAt() {
    return lastEventAt;
}
