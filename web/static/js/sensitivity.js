const sensitivityRequests = {};
const sensitivityResults = {};
const sensitivityColours = ['#1d70b8', '#00703c', '#d4351c', '#b87808', '#087e78', '#a23451'];
const historyFilterState = {};

function historyFilters(scope) {
    const age = document.getElementById(scope + '-sensitivity-age').value;
    if (scope === 'dashboard') return {age};
    const prefix = scope === 'trend' ? 'trend' : 'st';
    const filters = {age};
    for (const key of ['fuel', 'range', 'granularity', 'start', 'end']) filters[key] = document.getElementById(prefix + '-' + key).value;
    filters.fuel = getFuelSelection(prefix + '-fuel');
    if (filters.range === 'all') filters.start = '';
    if (scope === 'trend') {
        for (const key of ['country', 'region', 'rural-urban']) filters[key] = getMultiSelectValues('trend-' + key + '-ms');
    } else {
        filters.selection = {mode: stationTrendState.mode, nodeId: stationTrendState.nodeId,
            nodeIds: stationTrendState.nodeIds, searchFilters: stationTrendState.searchFilters};
    }
    return JSON.parse(JSON.stringify(filters));
}

function historyFiltersDiffer(draft, applied) {
    return !applied || JSON.stringify(draft) !== JSON.stringify(applied);
}

function historyFilterMessage(draft, state = {}) {
    const dirty = historyFiltersDiffer(draft, state.applied);
    if (state.loading) return historyFiltersDiffer(draft, state.requested) ? 'Applying earlier filters; unapplied changes' : 'Applying filters...';
    return dirty ? 'Unapplied changes' : '';
}

function updateHistoryFilterState(scope) {
    if (!reconstructedHistoryEnabled) return;
    const state = historyFilterState[scope] || {};
    const draft = historyFilters(scope);
    const dirty = historyFiltersDiffer(draft, state.applied);
    const status = document.getElementById(scope + '-filter-status');
    if (status) status.textContent = historyFilterMessage(draft, state);
    const apply = document.getElementById(scope + '-apply-filters');
    if (apply) apply.disabled = Boolean(state.loading && !historyFiltersDiffer(draft, state.requested));
    if (scope === 'dashboard') return;
    const results = sensitivityResults[scope] || [];
    for (const format of ['csv', 'json']) {
        const button = document.getElementById((scope === 'trend' ? 'trend-dl-' : 'st-dl-') + format);
        button.disabled = Boolean(state.loading || dirty || !state.applied || _userRole === 'readonly' || !results.some(result => result.response.data.some(row => row.avg_price != null)));
        button.title = dirty ? 'Apply filters before downloading' : 'Raw observations for the applied filters';
    }
    document.getElementById(scope + '-sensitivity-compare').disabled = Boolean(state.loading || !results.length || results[0].response.age_limit === 'none');
}

function redrawHistoryComparison(scope) {
    const chart = charts[scope === 'trend' ? 'chart-trend' : 'chart-station-trend'];
    const results = sensitivityResults[scope];
    if (!chart || !results) return;
    chart.data.datasets = historyDatasets(results, document.getElementById(scope + '-sensitivity-compare').checked);
    chart.options.plugins.legend.display = chart.data.datasets.length > 1;
    chart.update('none');
    if (scope === 'station') rememberStationTrend();
}

async function loadHistoryBreakdown(scope) {
    const state = historyFilterState[scope];
    const results = sensitivityResults[scope];
    if (!state || !results || state.loading || state.groupsLoaded || state.groupsLoading) return;
    const target = document.getElementById(scope + '-sensitivity-groups');
    state.groupsLoading = true;
    target.textContent = 'Loading breakdown...';
    const controller = state.controller;
    try {
        const detailed = await Promise.all(results.map(async result => ({...result,
            response: await apiFetch(result.url + '&include_sensitivity=true', {signal: controller.signal})})));
        if (controller.signal.aborted || sensitivityResults[scope] !== results) return;
        target.innerHTML = sensitivityGroupTable(detailed);
        state.groupsLoaded = true;
    } catch (error) {
        if (!controller.signal.aborted) target.innerHTML = '<p>Breakdown unavailable.</p><button type="button" data-breakdown-retry="' + scope + '">Retry breakdown</button>';
    } finally {
        if (state.controller === controller) state.groupsLoading = false;
    }
}

function historyDatasets(results, compare) {
    const ageNames = {none: 'No age limit', '30': 'Last 30 days', '14': 'Last 14 days', '7': 'Last 7 days', today: 'Recorded that day'};
    return results.flatMap(({name, response, colour}) => {
        const age = response.age_limit;
        const datasets = [{label: name + (age === 'none' ? '' : ' / ' + ageNames[age]),
            borderColor: colour, backgroundColor: colour, borderWidth: 2.5, order: 1,
            data: response.data.map(row => ({x: row.bucket, y: row.age_price, unsmoothed: row.unsmoothed_age_price,
                changed: row.hampel_age_price_changed, stations: row.included_stations})), spanGaps: false}];
        if (compare && age !== 'none') datasets.push({label: name + ' / no age limit',
            borderColor: colour + '80', backgroundColor: colour + '80', borderDash: [8, 5], borderWidth: 1.5,
            pointRadius: 0, pointHoverRadius: 4, order: 2,
            data: response.data.map(row => ({x: row.bucket, y: row.avg_price, unsmoothed: row.unsmoothed_avg_price,
                changed: row.hampel_avg_price_changed, stations: row.stations})), spanGaps: false});
        return datasets;
    });
}

function historyLegendLabels(chart) {
    return chart.data.datasets.map((dataset, index) => ({
        text: dataset.label, datasetIndex: index, hidden: !chart.isDatasetVisible(index),
        pointStyle: 'line', strokeStyle: dataset.borderColor, fillStyle: dataset.backgroundColor,
        lineWidth: dataset.borderWidth, lineDash: dataset.borderDash || [],
    }));
}

function initialiseSensitivity() {
    document.body.classList.toggle('reconstructed-history', reconstructedHistoryEnabled);
    if (reconstructedHistoryEnabled) {
        for (const id of ['trend-granularity', 'st-granularity']) {
            document.querySelector('#' + id + ' option[value="hourly"]').textContent = 'Hourly';
        }
        for (const scope of ['trend', 'station']) {
            const controls = document.querySelector(`[data-history-controls="${scope}"]`);
            controls.hidden = false;
            if (!controls.children.length) controls.innerHTML = `
                <div class="control"><label for="${scope}-sensitivity-age" title="Age of the latest stored price-change report, not its last confirmation">Price-record age</label>
                    <select id="${scope}-sensitivity-age" data-sensitivity-age="${scope}">
                        <option value="none" selected>No age limit</option><option value="30">Last 30 days</option>
                        <option value="14">Last 14 days</option><option value="7">Last 7 days</option><option value="today">Recorded that day</option>
                    </select>
                </div>
                <label class="history-compare"><input type="checkbox" id="${scope}-sensitivity-compare" data-sensitivity-compare="${scope}" disabled> Compare with no age limit</label>`;
        }
        setTrendRange(document.getElementById('trend-range').value, false);
    }
    document.querySelectorAll('[data-sensitivity]').forEach(details => {
        details.hidden = !reconstructedHistoryEnabled;
        if (!reconstructedHistoryEnabled || details.dataset.initialised) return;
        const scope = details.dataset.sensitivity;
        if (scope !== 'dashboard') {
            details.querySelector('[data-sensitivity-body]').innerHTML = `
                <div id="${scope}-sensitivity-summary" role="status" aria-live="polite"></div>
                <details class="sensitivity-breakdown" data-history-breakdown="${scope}"><summary>Exclusion breakdown</summary>
                    <p class="sensitivity-caveat">Snapshot regions and forecourt types. Fully excluded means no eligible hourly price remains in a bucket.</p>
                    <div id="${scope}-sensitivity-groups"></div>
                </details>
                <p class="sensitivity-caveat">Record age is not confirmation age. Historical days use UTC. <a href="/docs/about#outlier-methodology">Methodology</a></p>`;
            details.dataset.initialised = 'true';
            return;
        }
        const today = scope === 'dashboard' ? 'Recorded today' : 'Recorded that day';
        details.querySelector('[data-sensitivity-body]').innerHTML = `
            <div class="sensitivity-controls control">
                <label for="${scope}-sensitivity-age">Change-record age</label>
                <select id="${scope}-sensitivity-age" data-sensitivity-age="${scope}">
                    <option value="none">No age limit</option><option value="30" selected>Last 30 days</option>
                    <option value="14">Last 14 days</option><option value="7">Last 7 days</option><option value="today">${today}</option>
                </select>
                <button type="button" id="${scope}-apply-filters" data-sensitivity-retry="${scope}">Apply filters</button>
                <span class="filter-pending" id="${scope}-filter-status" role="status" aria-live="polite"></span>
            </div>
            <p class="sensitivity-caveat">Age refers to the stored price-change record, not its last confirmation. ${scope === 'dashboard' ? 'Existing IQR and anomaly exclusions are retained.' : 'Both series use the same Hampel settings. Recorded that day starts at each historical UTC midnight.'}</p>
            <div id="${scope}-sensitivity-summary" role="status" aria-live="polite"></div>
            <details class="sensitivity-breakdown"><summary>Exclusion breakdown</summary>
                <p class="sensitivity-caveat">Snapshot regions and forecourt types, not historical membership. Fully excluded means no eligible price remains in a bucket.</p>
                <div id="${scope}-sensitivity-groups"></div>
            </details>`;
        details.dataset.initialised = 'true';
    });
}

function sensitivityFormat(value, digits = 2) {
    return value == null || !Number.isFinite(Number(value)) ? 'n/a' : Number(value).toLocaleString('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function sensitivityMean(values) {
    const present = values.filter(value => value != null);
    return present.length ? present.reduce((sum, value) => sum + Number(value), 0) / present.length : null;
}

function sensitivityGroupTable(results, snapshot = false) {
    return results.map(({name, response}) => {
        const groups = new Map();
        for (const row of response.groups) {
            const key = row.dimension + ':' + row.label;
            if (!groups.has(key)) groups.set(key, {...row, stations: 0, included_stations: 0, excluded_stations: 0});
            const group = groups.get(key);
            group.stations += row.stations;
            group.included_stations += row.included_stations;
            group.excluded_stations += row.excluded_stations;
        }
        const buckets = snapshot ? 1 : response.data.length;
        const rows = [...groups.values()].sort((first, second) => first.dimension.localeCompare(second.dimension) || first.label.localeCompare(second.label));
        const units = snapshot ? 'stations' : response.granularity === 'daily' ? 'station-days' : 'station-hours';
        return `<h4>${escHtml(name)}</h4><div class="sensitivity-table" tabindex="0" role="region" aria-label="${escHtml(name)} exclusion breakdown"><table>
            <thead><tr><th>Group</th><th>Type</th><th>Reference ${units}</th><th>Included ${units}</th><th>Fully excluded</th><th>Excluded</th></tr></thead><tbody>
            ${rows.map(row => `<tr><th scope="row">${escHtml(row.label)}</th><td>${row.dimension === 'region' ? 'Region' : 'Forecourt'}</td><td>${sensitivityFormat(row.stations, 0)}</td><td>${sensitivityFormat(row.included_stations, 0)}</td><td>${sensitivityFormat(row.excluded_stations, 0)}</td><td>${row.stations ? sensitivityFormat(100 * row.excluded_stations / row.stations) + '%' : 'n/a'}</td></tr>`).join('')}
            </tbody></table></div>${!rows.length || !buckets ? '<p>No eligible stations.</p>' : ''}`;
    }).join('');
}

async function loadWeightedTrend(scope, fuel, buildUrl, single = false) {
    const settings = scope === 'trend'
        ? {chart: 'chart-trend', heading: 'trend-heading', note: 'trend-granularity-note', downloads: 'trend-dl-'}
        : {chart: 'chart-station-trend', heading: 'st-trend-heading', note: 'st-granularity-note', downloads: 'st-dl-'};
    const requested = historyFilters(scope);
    const state = historyFilterState[scope] ||= {};
    state.requested = requested;
    state.loading = true;
    state.groupsLoaded = false;
    state.groupsLoading = false;
    const age = requested.age;
    const comparison = document.getElementById(scope + '-sensitivity-compare');
    comparison.disabled = true;
    const ageName = document.getElementById(scope + '-sensitivity-age').selectedOptions[0].textContent;
    sensitivityRequests[scope]?.abort();
    const controller = new AbortController();
    sensitivityRequests[scope] = controller;
    state.controller = controller;
    sensitivityResults[scope] = null;
    if (charts[settings.chart]) { charts[settings.chart].destroy(); delete charts[settings.chart]; }
    document.getElementById(settings.heading).textContent = 'Loading last-reported prices...';
    document.getElementById(settings.note).textContent = '';
    document.getElementById(scope + '-sensitivity-summary').textContent = 'Loading coverage...';
    document.getElementById(scope + '-sensitivity-groups').textContent = '';
    for (const format of ['csv', 'json']) document.getElementById(settings.downloads + format).disabled = true;
    updateHistoryFilterState(scope);
    try {
        const fuels = selectedFuelTypes(fuel);
        const results = await Promise.all(fuels.map(async (type, index) => {
            let url = buildUrl(type.fuel_type_code);
            url += '&age_limit=' + encodeURIComponent(age);
            const response = await apiFetch(url, {signal: controller.signal});
            if (response.method !== 'last_reported_station_weighted') throw new Error('Reconstruction is not enabled');
            return {name: type.fuel_name || type.fuel_type_code, response, url, colour: fuelColour(type.fuel_type_code)};
        }));
        if (controller.signal.aborted || sensitivityRequests[scope] !== controller) return;
        sensitivityResults[scope] = results;
        const datasets = historyDatasets(results, comparison.checked);
        const hourly = results[0]?.response.granularity === 'hourly';
        if (single && results[0]?.response.station) {
            document.getElementById('station-trend-title').textContent = results[0].response.station.trading_name || stationTrendState.title;
        }
        const labels = [...new Set(results.flatMap(result => result.response.data.map(row => row.bucket)))].sort();
        charts[settings.chart] = new Chart(document.getElementById(settings.chart), {
            type: 'line', data: {labels, datasets}, options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: {mode: 'index', intersect: false},
                elements: {line: {tension: 0, borderWidth: 2}, point: {radius: hourly ? 0 : 2, hoverRadius: 4}},
                scales: {x: {type: 'category', ticks: {maxTicksLimit: 8, maxRotation: 0,
                    callback(value) { return new Date(this.getLabelForValue(value)).toLocaleString('en-GB', {timeZone: 'UTC', day: 'numeric', month: 'short', ...(hourly ? {hour: '2-digit', minute: '2-digit'} : {})}); }}},
                    y: {title: {display: true, text: 'Pence per litre'}}},
                plugins: {legend: {display: datasets.length > 1, position: 'bottom',
                    labels: {usePointStyle: true, pointStyle: 'line', pointStyleWidth: 36, boxWidth: 36, padding: 18, generateLabels: historyLegendLabels}},
                    tooltip: {callbacks: {
                        title: items => new Date(items[0].raw.x).toISOString().replace('T', ' ').slice(0, 16) + ' UTC',
                        label: item => item.dataset.label + ': ' + sensitivityFormat(item.parsed.y) + 'p/l; ' + item.raw.stations + ' station(s)' + (item.raw.changed ? '; before Hampel ' + sensitivityFormat(item.raw.unsmoothed) + 'p/l' : ''),
                    }}}},
        });
        document.getElementById(settings.heading).textContent = (hourly ? 'Hourly ' : 'Daily ') + (single ? 'last-reported price' : 'average last-reported price');
        const capped = results.some(result => result.response.range_capped);
        const range = scope === 'trend' ? 'trend' : 'st';
        const starts = results.map(result => result.response.range_start).filter(Boolean).sort();
        const allData = requested.range === 'all';
        if (allData && starts.length && !historyFiltersDiffer(historyFilters(scope), requested)) document.getElementById(range + '-start').value = starts[0].slice(0, 10);
        const hasPrices = results.some(result => result.response.data.some(row => row.age_price != null));
        document.getElementById(settings.note).textContent = (!hasPrices ? 'No prices match these filters. ' : '') +
            'Price-record age: ' + (age === 'none' ? 'no age limit.' : ageName.toLowerCase() + '.') + ' UTC. ' +
            (single ? 'Individual station; no Hampel smoothing.' : 'Hampel filtering retained.') +
            (capped ? ' History is limited by your access tier.' : '');
        if (scope === 'station') document.getElementById('st-hampel-note').textContent = '';
        if (scope === 'trend') lastTrendData = results[0]?.response.data || [];
        else lastStationTrendData = results[0]?.response.data || [];
        {
            document.getElementById(scope + '-sensitivity-summary').innerHTML = results.map(({name, response}) => {
                const valid = response.data.filter(row => row.avg_price != null && row.age_price != null);
                const difference = sensitivityMean(valid.map(row => row.age_price - row.avg_price));
                const reference = response.data.reduce((sum, row) => sum + row.stations, 0);
                const excluded = response.data.reduce((sum, row) => sum + row.excluded_stations, 0);
                const referenceHours = response.data.reduce((sum, row) => sum + row.reference_hours, 0);
                const includedHours = response.data.reduce((sum, row) => sum + row.included_hours, 0);
                const changed = response.data.filter(row => row.hampel_avg_price_changed || row.hampel_age_price_changed).length;
                const units = response.granularity === 'daily' ? 'station-days' : 'station-hours';
                const included = sensitivityMean(response.data.map(row => row.included_stations));
                const coverage = sensitivityFormat(included, single ? 2 : 0) + (single ? ' station' : ' stations') + ' per ' + (response.granularity === 'daily' ? 'day' : 'hour');
                const change = age === 'none' ? '' : ` ${difference == null ? 'No paired prices' : (difference > 0 ? '+' : '') + sensitivityFormat(difference) + 'p/l versus no age limit'}; ${reference ? sensitivityFormat(100 * excluded / reference) + '% fewer ' + units : 'no eligible stations'}; ${referenceHours ? sensitivityFormat(100 * (referenceHours - includedHours) / referenceHours) + '% fewer sampled hours' : 'no sampled hours'}.`;
                return `<p><strong>${escHtml(name)}</strong>: ${coverage}.${change}${changed ? ' ' + changed + ' bucket(s) adjusted by Hampel.' : ''}</p>`;
            }).join('');
        }
        state.applied = requested;
        if (scope === 'station') rememberStationTrend();
    } catch (error) {
        if (controller.signal.aborted) return;
        document.getElementById(settings.heading).textContent = 'Price history unavailable';
        document.getElementById(settings.note).textContent = error.message;
        document.getElementById(scope + '-sensitivity-summary').textContent = 'Comparison unavailable. Apply filters to retry.';
    } finally {
        if (sensitivityRequests[scope] === controller) {
            state.loading = false;
            updateHistoryFilterState(scope);
            if (document.querySelector(`[data-history-breakdown="${scope}"]`)?.open) loadHistoryBreakdown(scope);
        }
    }
}

async function loadSnapshotSensitivity() {
    const details = document.querySelector('[data-sensitivity="dashboard"]');
    if (!reconstructedHistoryEnabled || !details.open) return;
    sensitivityRequests.dashboard?.abort();
    const controller = new AbortController();
    sensitivityRequests.dashboard = controller;
    const state = historyFilterState.dashboard ||= {};
    const requested = historyFilters('dashboard');
    state.requested = requested;
    state.loading = true;
    updateHistoryFilterState('dashboard');
    const summary = document.getElementById('dashboard-sensitivity-summary');
    const groups = document.getElementById('dashboard-sensitivity-groups');
    summary.textContent = 'Calculating current-snapshot comparison...';
    groups.textContent = '';
    try {
        const age = requested.age;
        const results = await Promise.all(fuelTypes.map(async type => ({name: type.fuel_name,
            response: await apiFetch('/prices/current/sensitivity?' + new URLSearchParams({fuel_type: type.fuel_type_code, age_limit: age}), {signal: controller.signal})})));
        if (controller.signal.aborted) return;
        summary.innerHTML = `<div class="sensitivity-table" tabindex="0" role="region" aria-label="Current price sensitivity"><table><thead><tr><th>Fuel</th><th>No age limit</th><th>Age-limited</th><th>Difference</th><th>Included / reference</th><th>Fully excluded</th></tr></thead><tbody>${results.map(({name, response}) => {
            const row = response.data;
            const difference = row.age_price == null || row.avg_price == null ? null : row.age_price - row.avg_price;
            return `<tr><th scope="row">${escHtml(name)}</th><td>${sensitivityFormat(row.avg_price)}p/l</td><td>${sensitivityFormat(row.age_price)}p/l</td><td>${difference > 0 ? '+' : ''}${sensitivityFormat(difference)}p/l</td><td>${row.included_stations} / ${row.stations}</td><td>${row.excluded_stations}</td></tr>`;
        }).join('')}</tbody></table></div><p class="sensitivity-caveat">Current snapshot; age evaluated at ${escHtml(results[0]?.response.as_of || '')}. No historical averaging or Hampel filtering is used here. IQR exclusions stay fixed while age limits change.</p>`;
        groups.innerHTML = sensitivityGroupTable(results, true);
        state.applied = requested;
    } catch (error) {
        if (!controller.signal.aborted) summary.textContent = 'Snapshot comparison unavailable. Apply filters to retry.';
    } finally {
        if (sensitivityRequests.dashboard === controller) {
            state.loading = false;
            updateHistoryFilterState('dashboard');
        }
    }
}

function refreshSensitivity(scope) {
    if (!reconstructedHistoryEnabled) return;
    if (scope === 'trend') loadTrends();
    else if (scope === 'station') loadStationTrend();
    else loadSnapshotSensitivity();
}

document.addEventListener('toggle', event => {
    if (event.target.matches('[data-sensitivity="dashboard"]') && event.target.open && !historyFilterState.dashboard?.applied && !historyFilterState.dashboard?.loading) refreshSensitivity('dashboard');
    if (event.target.matches('[data-history-breakdown]') && event.target.open) loadHistoryBreakdown(event.target.dataset.historyBreakdown);
}, true);
for (const eventName of ['input', 'change']) document.addEventListener(eventName, event => {
    if (!reconstructedHistoryEnabled || !event.target.matches('input, select')) return;
    if (event.target.matches('[data-sensitivity-compare]')) {
        if (eventName === 'change') redrawHistoryComparison(event.target.dataset.sensitivityCompare);
        return;
    }
    if (event.target.closest('[data-sensitivity="dashboard"]')) updateHistoryFilterState('dashboard');
    else if (event.target.closest('#panel-trends')) updateHistoryFilterState('trend');
    else if (event.target.closest('#panel-station-trend')) updateHistoryFilterState('station');
});
document.addEventListener('click', event => {
    const button = event.target.closest('[data-sensitivity-retry]');
    if (button) refreshSensitivity(button.dataset.sensitivityRetry);
    const retry = event.target.closest('[data-breakdown-retry]');
    if (retry) loadHistoryBreakdown(retry.dataset.breakdownRetry);
});