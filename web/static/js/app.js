// ---------------------------------------------------------------------------
// App bootstrap — ties everything together
// ---------------------------------------------------------------------------
async function startApp() {
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

document.addEventListener('DOMContentLoaded', initSortableTables);

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.multi-select').forEach(ms => {
        ms.querySelectorAll('input[type=checkbox]').forEach(cb => {
            cb.addEventListener('change', () => updateMultiSelectDisplay(ms.id));
        });
    });
});
