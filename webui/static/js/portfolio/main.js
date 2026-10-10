import { initInstrumentsView, loadInstruments } from './instruments.js';
import { initTransactionsView, populateInstrumentSelect, loadTransactionHistory, loadRentasDividendos } from './transactions.js';
import { initPlazosFijosView, loadPlazosFijos, loadCauciones } from './plazos-fijos.js';
import { initHoldingsView, loadHoldings } from './holdings.js';
import { initSignalsView, loadSignals } from './signals.js';
import { initExplorerView } from './explorer.js';
import { initTabs } from './tabs.js';
import { initDateRangeFilter } from '../shared/date-range-filter.js';
import { initSubtabs } from '../shared/subtabs.js';

document.addEventListener('DOMContentLoaded', async () => {
    initTabs();
    initSubtabs();
    initInstrumentsView();
    initTransactionsView();
    initPlazosFijosView();
    initHoldingsView();
    initSignalsView();
    initExplorerView();
    initDateRangeFilter({
        fromId: 'tx-filter-from', toId: 'tx-filter-to',
        tbodyIds: ['transactions-tbody', 'rentas-dividendos-tbody'],
    });

    await Promise.all([loadInstruments(), populateInstrumentSelect()]);
    await Promise.all([
        loadTransactionHistory(), loadHoldings(), loadPlazosFijos(),
        loadSignals(), loadCauciones(), loadRentasDividendos(),
    ]);
});
