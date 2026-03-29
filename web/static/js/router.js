// ---------------------------------------------------------------------------
// Hash-based router — enables browser back/forward navigation
// ---------------------------------------------------------------------------
const tabs = document.querySelectorAll('.tab');
const panels = document.querySelectorAll('.panel');
const lazyLoaded = {};

function getHashParts() {
    const parts = location.hash.replace('#', '').split('/');
    return {
        panel: parts[0] || 'dashboard',
        section: parts[1] || null,
    };
}

function applyPanelSection(panel, section) {
    if (!panel || !document.getElementById('panel-' + panel)) {
        panel = 'dashboard';
    }

    switchTab(panel, false);

    if (!section) return;

    if (panel === 'anomalies') {
        const select = document.getElementById('anomaly-section');
        if (select && Array.from(select.options).some(o => o.value === section)) {
            select.value = section;
            switchAnomalySection(true);
        }
    }

    if (panel === 'data') {
        const select = document.getElementById('data-section');
        if (select && Array.from(select.options).some(o => o.value === section)) {
            select.value = section;
            switchDataSection();
        }
    }
}

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
        if (panelName === 'data') switchDataSection();
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
        applyPanelSection(e.state.panel, e.state.section || null);
    } else {
        const hash = getHashParts();
        applyPanelSection(hash.panel, hash.section);
    }
});

// Read initial hash on page load (called from app.js after auth)
function applyInitialHash() {
    const hash = getHashParts();
    if (hash.panel && document.getElementById('panel-' + hash.panel)) {
        applyPanelSection(hash.panel, hash.section);
        // Replace state so back button works from the initial tab
        history.replaceState({ panel: hash.panel, section: hash.section || null }, '', location.hash || ('#' + hash.panel));
    } else {
        history.replaceState({ panel: 'dashboard' }, '', '#dashboard');
    }
}
