import { $ } from './dom.js';

export function initDateRangeFilter({ fromId, toId, tbodyIds }) {
    const fromInput = $(fromId);
    const toInput = $(toId);

    const apply = () => {
        const from = fromInput.value || null;
        const to = toInput.value || null;
        tbodyIds.forEach((tbodyId) => {
            $(tbodyId).querySelectorAll('tr').forEach((row) => {
                const date = row.querySelector('td')?.textContent?.trim();
                const inRange = !date || ((!from || date >= from) && (!to || date <= to));
                row.classList.toggle('date-filter-hidden', !inRange);
            });
        });
    };

    fromInput.addEventListener('change', apply);
    toInput.addEventListener('change', apply);
}
