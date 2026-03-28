// ---------------------------------------------------------------------------
// Hash-based router — enables browser back/forward navigation
// ---------------------------------------------------------------------------
const tabs = document.querySelectorAll('.tab');
const panels = document.querySelectorAll('.panel');
const lazyLoaded = {};

function switchTab(panelName, pushState) {
    if (pushState === undefined) pushState = true;
    tabs.forEach(x => x.classList.remove('active'));
    panels.forEach(x => x.classList.remove('active'));
    const tab = document.querySelector(`.tab[data-panel="${panelName}"]`);
    if (tab) tab.classList.add('active');
    document.getElementById('panel-' + panelName).classList.add('active');

    if (!lazyLoaded[panelName]) {
        lazyLoaded[panelName] = true;
        if (panelName === 'map') initMap();
        if (panelName === 'trends') loadTrends();
        if (panelName === 'anomalies') loadAnomalies();
        if (panelName === 'logs') { loadScrapeHistory(); loadCorrectionsLog(); }
        if (panelName === 'data') loadReport();
        if (panelName === 'users') loadUsers();
    }

    if (pushState) {
        history.pushState({ panel: panelName }, '', '#' + panelName);
    }
}

// Wire up tab clicks
tabs.forEach(t => t.addEventListener('click', () => {
    switchTab(t.dataset.panel);
}));

// Handle browser back/forward
window.addEventListener('popstate', (e) => {
    if (e.state && e.state.panel) {
        switchTab(e.state.panel, false);
    } else {
        const hash = location.hash.replace('#', '').split('/')[0] || 'dashboard';
        switchTab(hash, false);
    }
});

// Read initial hash on page load (called from app.js after auth)
function applyInitialHash() {
    const hash = location.hash.replace('#', '').split('/')[0];
    if (hash && document.getElementById('panel-' + hash)) {
        switchTab(hash, false);
        // Replace state so back button works from the initial tab
        history.replaceState({ panel: hash }, '', '#' + hash);
    } else {
        history.replaceState({ panel: 'dashboard' }, '', '#dashboard');
    }
}
