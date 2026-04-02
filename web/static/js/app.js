// ---------------------------------------------------------------------------
// App bootstrap — ties everything together
// ---------------------------------------------------------------------------
async function startApp() {
    initTomSelects();
    await fetchUserRole();
    await loadFuelTypes();
    await loadRegions();
    loadDistricts();
    loadConstituencies();
    await loadDashboard();
    applyInitialHash();
}

(async () => {
    const authed = await initAuth();
    if (authed) await startApp();
})();

document.addEventListener('DOMContentLoaded', () => {
    initServerSortedTables();
    initSortableTables();
});

document.addEventListener('DOMContentLoaded', () => {
    initTomSelects();
});
