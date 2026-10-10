const REDUCE_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function animateValue(el, { to, from = 0, duration = 600, formatFn = (v) => String(Math.round(v)) }) {
    if (REDUCE_MOTION || to === null || to === undefined || Number.isNaN(to)) {
        el.textContent = formatFn(to ?? 0);
        return;
    }
    const start = performance.now();
    function tick(now) {
        const progress = Math.min((now - start) / duration, 1);
        const eased = 1 - (1 - progress) ** 3;
        el.textContent = formatFn(from + (to - from) * eased);
        if (progress < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
}
