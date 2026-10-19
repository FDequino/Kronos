import { initInstrumentosView, loadInstrumentos } from './instrumentos.js';
import { initTransactionsView, populateInstrumentSelect, loadTransactionHistory, loadRentasDividendos } from './transactions.js';
import { initPlazosFijosView, loadPlazosFijos, loadCauciones } from './plazos-fijos.js';
import { initHoldingsView, loadHoldings } from './holdings.js';
import { initCoreSatelliteView } from './core-satellite.js';
import { initNewsView } from './news.js';
import { initPaperTradingView } from './paper-trading.js';
import { initKronosHistoryView } from './kronos-history.js';
import { initProposalsView, loadProposals } from './proposals.js';
import { initLoansView, loadLoans } from './loans.js';
import { initComparadorView, loadComparador } from './comparador.js';
import { initAjustesView, loadTelegramUsers } from './ajustes.js';
import { initBrokerConnectionsView, loadBrokerAccounts } from './broker-connections.js';
import { initQuoteStream } from './quote-stream.js';
import { initTabs } from './tabs.js';
import { initSubtabs } from '../shared/subtabs.js';
import { initStickyOffset } from '../shared/sticky-offset.js';

document.addEventListener('DOMContentLoaded', async () => {
    // Fase 15: initTabs() corre DESPUÉS de initSubtabs() y de cada
    // initXxxView() a propósito -- un link a un hash viejo fusionado (ej.
    // #comparar) se resuelve adentro de initTabs() con un click() real sobre
    // el botón de subtab correspondiente (ver shared/subtabs.js), y ese click
    // necesita que el listener que activa el panel (initSubtabs) Y el que
    // dispara la carga perezosa de esa subtab ya estén enganchados -- si
    // initTabs() corriera primero, ese primer click caería sin nadie
    // escuchando y la subtab quedaría sin cargar datos.
    initSubtabs();
    initQuoteStream();
    initInstrumentosView();
    initTransactionsView();
    initPlazosFijosView();
    initHoldingsView();
    initCoreSatelliteView();
    initNewsView();
    initPaperTradingView();
    initKronosHistoryView();
    initProposalsView();
    initLoansView();
    initComparadorView();
    initAjustesView();
    initBrokerConnectionsView();
    initTabs();
    initStickyOffset();

    await Promise.all([loadInstrumentos(), populateInstrumentSelect()]);
    await Promise.all([
        loadTransactionHistory(), loadHoldings(), loadPlazosFijos(),
        loadCauciones(), loadRentasDividendos(),
        loadProposals(), loadLoans(), loadComparador(),
        loadTelegramUsers(), loadBrokerAccounts(),
    ]);
});
