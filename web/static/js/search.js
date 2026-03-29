// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------
let searchState = { offset: 0 };
let searchSort = { col: 'price', dir: 'asc' };

// Column index → API sort key (index 0 is checkbox, no sort)
const SEARCH_SORT_KEYS = [null,'station','brand',null,'city','postcode','district','rural_urban','price',null,'observed_at'];

async function doSearch(offset = 0) {
    const data = await apiFetch(buildSearchUrl(50, offset));
    searchState = { offset, total: data.total };

    const body = document.getElementById('search-body');
    body.innerHTML = data.results.map(r => `
        <tr>
            <td><input type="checkbox" class="search-row-cb" value="${escHtml(r.node_id)}" data-name="${escHtml(r.trading_name)}" data-brand="${escHtml(r.brand_name || '')}" data-city="${escHtml(r.city || '')}" data-postcode="${escHtml(r.postcode || '')}" onchange="updateTrendButton()"></td>
            <td><a href="#" class="station-link" data-node="${escHtml(r.node_id)}" data-name="${escHtml(r.trading_name)}" data-brand="${escHtml(r.brand_name || '')}" data-city="${escHtml(r.city || '')}" data-postcode="${escHtml(r.postcode || '')}" style="color:var(--accent);text-decoration:none;">${escHtml(r.trading_name)}</a></td>
            <td>${r.brand_name || '—'}</td>
            <td>${categoryTag(r.forecourt_type)}</td>
            <td>${r.city || '—'}</td>
            <td>${r.postcode || '—'}</td>
            <td>${r.admin_district || '—'}</td>
            <td>${r.rural_urban ? r.rural_urban.replace(/:.*/,'') : '—'}</td>
            <td><strong>${ppl(r.price)}</strong></td>
            <td>${r.fuel_name || r.fuel_type}</td>
            <td>${new Date(r.observed_at).toLocaleDateString()}</td>
        </tr>
    `).join('');
    document.getElementById('search-select-all').checked = false;
    updateTrendButton();

    // Enable/disable the "all results" trend button
    const allBtn = document.getElementById('search-view-all-trend-btn');
    allBtn.disabled = !data.total;
    allBtn.textContent = data.total
        ? `📈 View trend for all ${data.total.toLocaleString()} results`
        : '📈 View trend for all results';

    const pag = document.getElementById('search-pagination');
    const page = Math.floor(offset / 50) + 1;
    const pages = Math.ceil(data.total / 50);
    pag.innerHTML = `
        <button ${offset === 0 ? 'disabled' : ''} onclick="doSearch(${offset - 50})">← Prev</button>
        <span class="info">Page ${page} of ${pages} (${data.total.toLocaleString()} results)</span>
        <button ${offset + 50 >= data.total ? 'disabled' : ''} onclick="doSearch(${offset + 50})">Next →</button>
    `;

    document.getElementById('search-dl-csv').disabled = !data.total;
    document.getElementById('search-dl-json').disabled = !data.total;
    document.getElementById('search-hist-dl-csv').disabled = !data.total;
    document.getElementById('search-hist-dl-json').disabled = !data.total;
    _syncSortIndicators('search-body', searchSort, SEARCH_SORT_KEYS);
}

function buildSearchUrl(limit, offset) {
    const fuel = document.getElementById('search-fuel').value;
    const postcode = document.getElementById('search-postcode').value;
    const station = document.getElementById('search-station').value;
    const brand = document.getElementById('search-brand').value;
    const city = document.getElementById('search-city').value;
    const minP = document.getElementById('search-min').value;
    const maxP = document.getElementById('search-max').value;
    const supermarket = document.getElementById('search-supermarket').checked;
    const motorway = document.getElementById('search-motorway').checked;
    const excludeOutliers = document.getElementById('search-exclude-outliers').checked;
    const category = getSelectedCategories();
    const district = document.getElementById('search-district').value;
    const constituency = document.getElementById('search-constituency').value;
    const ruralUrban = getMultiSelectValues('search-rural-urban-ms');
    const region = getMultiSelectValues('search-region-ms');
    const country = getMultiSelectValues('search-country-ms');

    let url = `/prices/search?fuel_type=${fuel}&limit=${limit}&offset=${offset}`;
    if (searchSort.col) url += `&sort=${searchSort.col}&order=${searchSort.dir}`;
    if (postcode) url += `&postcode=${encodeURIComponent(postcode)}`;
    if (station) url += `&station=${encodeURIComponent(station)}`;
    if (brand) url += `&brand=${encodeURIComponent(brand)}`;
    if (city) url += `&city=${encodeURIComponent(city)}`;
    if (minP) url += `&min_price=${minP}`;
    if (maxP) url += `&max_price=${maxP}`;
    if (supermarket) url += '&supermarket_only=true';
    if (motorway) url += '&motorway_only=true';
    if (excludeOutliers) url += '&exclude_outliers=true';
    if (category) url += `&category=${encodeURIComponent(category)}`;
    if (district) url += `&district=${encodeURIComponent(district)}`;
    if (constituency) url += `&constituency=${encodeURIComponent(constituency)}`;
    if (ruralUrban) url += `&rural_urban=${encodeURIComponent(ruralUrban)}`;
    if (region) url += `&region=${encodeURIComponent(region)}`;
    if (country) url += `&country=${encodeURIComponent(country)}`;
    return url;
}

async function downloadSearchData(fmt) {
    const btn = document.getElementById('search-dl-' + fmt);
    btn.disabled = true;
    btn.textContent = '⏳ ' + fmt.toUpperCase();
    try {
        const data = await apiFetch(buildSearchUrl(10000, 0));
        downloadFile(data.results, 'fuel-search-results', fmt);
    } finally {
        btn.disabled = false;
        btn.textContent = '⬇ ' + fmt.toUpperCase();
    }
}

function downloadSearchHistoryData(fmt) {
    let url = buildSearchUrl(10000, 0)
        .replace('/prices/search?', '/prices/search/export?')
        .replace(/&limit=\d+/, '').replace(/&offset=\d+/, '');
    url += '&format=' + fmt;
    fetchExport(API + url, ['fuel-search-all-history'], fmt, document.getElementById('search-hist-dl-' + fmt));
}

// ---------------------------------------------------------------------------
// Search → Station / Selection Trend
// ---------------------------------------------------------------------------
let stationTrendState = { mode: null, nodeId: null, nodeIds: null, title: '' };
let lastStationTrendData = [];

function toggleSearchSelectAll(master) {
    document.querySelectorAll('.search-row-cb').forEach(cb => cb.checked = master.checked);
    updateTrendButton();
}

function updateTrendButton() {
    const count = document.querySelectorAll('.search-row-cb:checked').length;
    const btn = document.getElementById('search-view-trend-btn');
    if (count === 0) {
        btn.disabled = true;
        btn.textContent = '📈 View trend for selected';
    } else if (count === 1) {
        const cb = document.querySelector('.search-row-cb:checked');
        btn.disabled = false;
        btn.textContent = `📈 View trend for ${cb.dataset.name}`;
    } else {
        btn.disabled = false;
        btn.textContent = `📈 View trend for ${count} stations`;
    }
}

function showStationTrendPanel() {
    tabs.forEach(x => x.classList.remove('active'));
    panels.forEach(x => x.classList.remove('active'));
    document.getElementById('panel-station-trend').classList.add('active');
    history.pushState({ panel: 'station-trend' }, '', '#station-trend');
}

function closeStationTrend() {
    history.back();
}

function initStationTrendFuels() {
    const sel = document.getElementById('st-fuel');
    if (!sel.options.length) {
        fuelTypes.forEach(ft => {
            const o = document.createElement('option');
            o.value = ft.fuel_type_code;
            o.textContent = ft.fuel_name;
            sel.appendChild(o);
        });
    }
    // Sync fuel type from search panel
    const searchFuel = document.getElementById('search-fuel').value;
    if (searchFuel) sel.value = searchFuel;
    else sel.value = 'E10';
}

function openStationTrend(nodeId, name, brand, city, postcode) {
    stationTrendState = { mode: 'single', nodeId, nodeIds: null, title: name };
    initStationTrendFuels();
    // Reset range to 30 days
    document.getElementById('st-range').value = '30';
    document.getElementById('st-granularity').value = 'auto';
    document.getElementById('station-trend-title').textContent = name;
    const parts = [brand, city, postcode].filter(Boolean);
    document.getElementById('station-trend-subtitle').textContent = parts.join(' · ');
    showStationTrendPanel();
    setStationTrendRange('30');
}

function viewSelectedTrend() {
    const checked = document.querySelectorAll('.search-row-cb:checked');
    if (!checked.length) return;
    const ids = Array.from(checked).map(cb => cb.value);
    const names = Array.from(checked).map(cb => cb.dataset.name);

    if (ids.length === 1) {
        const cb = checked[0];
        openStationTrend(cb.value, cb.dataset.name, cb.dataset.brand, cb.dataset.city, cb.dataset.postcode);
        return;
    }

    stationTrendState = { mode: 'multi', nodeId: null, nodeIds: ids, title: `${ids.length} stations` };
    initStationTrendFuels();
    // Reset range to 30 days
    document.getElementById('st-range').value = '30';
    document.getElementById('st-granularity').value = 'auto';
    document.getElementById('station-trend-title').textContent = `Average trend for ${ids.length} stations`;
    const preview = names.length <= 5 ? names.join(', ') : names.slice(0, 5).join(', ') + ` + ${names.length - 5} more`;
    document.getElementById('station-trend-subtitle').textContent = preview;
    showStationTrendPanel();
    setStationTrendRange('30');
}

async function viewAllResultsTrend() {
    // Capture search filters to pass directly to the history endpoint
    const searchFilters = {};
    const station = document.getElementById('search-station').value;
    const brand = document.getElementById('search-brand').value;
    const city = document.getElementById('search-city').value;
    const postcode = document.getElementById('search-postcode').value;
    const category = getSelectedCategories();
    const district = document.getElementById('search-district').value;
    const constituency = document.getElementById('search-constituency').value;
    const ruralUrban = getMultiSelectValues('search-rural-urban-ms');
    const region = getMultiSelectValues('search-region-ms');
    const country = getMultiSelectValues('search-country-ms');
    const supermarket = document.getElementById('search-supermarket').checked;
    const motorway = document.getElementById('search-motorway').checked;
    const excludeOutliers = document.getElementById('search-exclude-outliers').checked;

    if (station) searchFilters.station = station;
    if (brand) searchFilters.brand = brand;
    if (city) searchFilters.city = city;
    if (postcode) searchFilters.postcode = postcode;
    if (category) searchFilters.category = category;
    if (district) searchFilters.district = district;
    if (constituency) searchFilters.constituency = constituency;
    if (ruralUrban) searchFilters.rural_urban = ruralUrban;
    if (region) searchFilters.region = region;
    if (country) searchFilters.country = country;
    if (supermarket) searchFilters.supermarket_only = true;
    if (motorway) searchFilters.motorway_only = true;
    if (excludeOutliers) searchFilters.exclude_outliers = true;

    const total = searchState.total || 0;
    stationTrendState = { mode: 'search', nodeId: null, nodeIds: null, searchFilters, title: `${total} stations` };
    initStationTrendFuels();
    document.getElementById('st-range').value = '30';
    document.getElementById('st-granularity').value = 'auto';
    document.getElementById('station-trend-title').textContent = `Average trend for all ${total.toLocaleString()} search results`;
    document.getElementById('station-trend-subtitle').textContent = 'Based on current search filters';
    showStationTrendPanel();
    setStationTrendRange('30');
}

function setStationTrendRange(value) {
    const customFields = value === 'custom';
    document.getElementById('st-start-ctl').style.display = customFields ? '' : 'none';
    document.getElementById('st-end-ctl').style.display = customFields ? '' : 'none';
    if (!customFields) {
        const end = new Date();
        document.getElementById('st-end').value = end.toISOString().slice(0, 10);
        if (value === 'all') {
            document.getElementById('st-start').value = '';
        } else {
            const start = new Date();
            start.setDate(start.getDate() - parseInt(value));
            document.getElementById('st-start').value = start.toISOString().slice(0, 10);
        }
        loadStationTrend();
    }
}

async function loadStationTrend() {
    const fuel = document.getElementById('st-fuel').value;
    const startDate = document.getElementById('st-start').value;
    const endDate = document.getElementById('st-end').value;
    const gran = document.getElementById('st-granularity').value;

    // Show loading state
    document.getElementById('st-trend-heading').textContent = 'Loading…';
    document.getElementById('st-granularity-note').textContent = '';
    if (charts['chart-station-trend']) { charts['chart-station-trend'].destroy(); delete charts['chart-station-trend']; }

    let url;
    if (stationTrendState.mode === 'single') {
        url = `/prices/station/${encodeURIComponent(stationTrendState.nodeId)}/history?fuel_type=${fuel}`;
    } else if (stationTrendState.mode === 'search') {
        // Pass search filters directly — much faster than sending thousands of node_ids
        url = `/prices/history?fuel_type=${fuel}`;
        const sf = stationTrendState.searchFilters;
        if (sf.brand) url += `&brand=${encodeURIComponent(sf.brand)}`;
        if (sf.city) url += `&city=${encodeURIComponent(sf.city)}`;
        if (sf.postcode) url += `&postcode=${encodeURIComponent(sf.postcode)}`;
        if (sf.category) url += `&category=${encodeURIComponent(sf.category)}`;
        if (sf.district) url += `&district=${encodeURIComponent(sf.district)}`;
        if (sf.constituency) url += `&constituency=${encodeURIComponent(sf.constituency)}`;
        if (sf.rural_urban) url += `&rural_urban=${encodeURIComponent(sf.rural_urban)}`;
        if (sf.region) url += `&region=${encodeURIComponent(sf.region)}`;
        if (sf.country) url += `&country=${encodeURIComponent(sf.country)}`;
        if (sf.supermarket_only) url += '&supermarket_only=true';
        if (sf.motorway_only) url += '&motorway_only=true';
        if (sf.exclude_outliers) url += '&exclude_outliers=true';
    } else {
        const ids = stationTrendState.nodeIds.join(',');
        url = `/prices/history?fuel_type=${fuel}&node_ids=${encodeURIComponent(ids)}`;
    }
    if (startDate) url += `&start_date=${startDate}`;
    if (endDate) url += `&end_date=${endDate}`;
    if (!startDate && !endDate) url += '&days=30';
    if (gran !== 'auto') url += `&granularity=${gran}`;

    const resp = await apiFetch(url);
    const data = resp.data;
    lastStationTrendData = data;

    document.getElementById('st-dl-csv').disabled = !data.length;
    document.getElementById('st-dl-json').disabled = !data.length;

    // Show table view button only for single-station views
    document.getElementById('st-edit-btn').style.display =
        stationTrendState.mode === 'single' ? '' : 'none';

    const hourly = resp.granularity === 'hourly';
    const isSingle = stationTrendState.mode === 'single';
    document.getElementById('st-trend-heading').textContent =
        isSingle ? (hourly ? 'Hourly price' : 'Daily price')
                 : (hourly ? 'Hourly average price' : 'Daily average price');
    document.getElementById('st-granularity-note').textContent = hourly
        ? 'Showing per scrape window (data is fetched every 30 minutes).'
        : 'Showing daily ' + (isSingle ? 'prices.' : 'averages.');

    const labels = data.map(d => {
        const dt = new Date(d.bucket);
        if (hourly) {
            return dt.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
                 + ' ' + dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        }
        return dt.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
    });

    if (charts['chart-station-trend']) charts['chart-station-trend'].destroy();
    const ctx = document.getElementById('chart-station-trend').getContext('2d');
    charts['chart-station-trend'] = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: isSingle ? 'Pence/litre' : 'Avg pence/litre',
                data: data.map(d => d.avg_price),
                borderColor: '#1d70b8',
                backgroundColor: '#1d70b833',
                fill: true, tension: 0.3,
                pointRadius: hourly ? 1.5 : 4,
                pointHoverRadius: hourly ? 4 : 7,
                pointBackgroundColor: '#1d70b8',
                borderWidth: hourly ? 1.5 : 2,
            }]
        },
        options: {
            responsive: true,
            scales: {
                x: { ticks: { maxTicksAutoSkip: true, maxRotation: 45 } },
                y: { beginAtZero: false }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => items[0].label,
                        label: (item) => {
                            const d = data[item.dataIndex];
                            const suffix = d.stations ? ` · averaged from ${d.stations} stations` : '';
                            return `${item.parsed.y}p${suffix}`;
                        }
                    }
                }
            }
        }
    });
}

function downloadStationTrendData(fmt) {
    const fuel = document.getElementById('st-fuel').value;
    const startDate = document.getElementById('st-start').value;
    const endDate = document.getElementById('st-end').value;

    let url = `/api/prices/history/export?fuel_type=${encodeURIComponent(fuel)}&format=${fmt}`;
    if (startDate) url += `&start_date=${startDate}`;
    if (endDate) url += `&end_date=${endDate}`;
    if (!startDate && !endDate) url += '&days=30';

    if (stationTrendState.mode === 'single') {
        url += `&node_ids=${encodeURIComponent(stationTrendState.nodeId)}`;
    } else if (stationTrendState.mode === 'search') {
        const sf = stationTrendState.searchFilters;
        if (sf.brand) url += `&brand=${encodeURIComponent(sf.brand)}`;
        if (sf.city) url += `&city=${encodeURIComponent(sf.city)}`;
        if (sf.postcode) url += `&postcode=${encodeURIComponent(sf.postcode)}`;
        if (sf.category) url += `&category=${encodeURIComponent(sf.category)}`;
        if (sf.district) url += `&district=${encodeURIComponent(sf.district)}`;
        if (sf.constituency) url += `&constituency=${encodeURIComponent(sf.constituency)}`;
        if (sf.rural_urban) url += `&rural_urban=${encodeURIComponent(sf.rural_urban)}`;
        if (sf.region) url += `&region=${encodeURIComponent(sf.region)}`;
        if (sf.country) url += `&country=${encodeURIComponent(sf.country)}`;
        if (sf.supermarket_only) url += '&supermarket_only=true';
        if (sf.motorway_only) url += '&motorway_only=true';
        if (sf.exclude_outliers) url += '&exclude_outliers=true';
    } else {
        url += `&node_ids=${encodeURIComponent(stationTrendState.nodeIds.join(','))}`;
    }

    const parts = [stationTrendState.mode === 'single'
        ? 'station-' + stationTrendState.title.replace(/\s+/g, '-').toLowerCase()
        : 'station-trend'];
    if (fuel) parts.push(fuel.replace(/\s+/g, '-'));
    if (startDate) parts.push('from-' + startDate);
    if (endDate) parts.push('to-' + endDate);

    fetchExport(url, parts, fmt, document.getElementById('st-dl-' + fmt));
}

// ---------------------------------------------------------------------------
// Price Editor
// ---------------------------------------------------------------------------
let priceEditorState = { nodeId: null, backTo: 'anomalies' };
const pendingCorrections = new Map(); // fuel_price_id → corrected_price

function openPriceEditor(nodeId, stationName, backTo) {
    priceEditorState = { nodeId, backTo: backTo || 'anomalies' };
    pendingCorrections.clear();
    updatePeSaveBar();
    // Hide all panels, show editor
    panels.forEach(x => x.classList.remove('active'));
    tabs.forEach(x => x.classList.remove('active'));
    document.getElementById('panel-price-editor').classList.add('active');
    document.getElementById('price-editor-title').textContent = stationName;
    document.getElementById('price-editor-subtitle').textContent = 'Edit individual price records for this station';
    history.pushState({ panel: 'price-editor' }, '', '#price-editor');
    // Populate fuel select
    const sel = document.getElementById('pe-fuel');
    if (!sel.options.length) {
        const allOpt = document.createElement('option');
        allOpt.value = ''; allOpt.textContent = 'All fuel types';
        sel.appendChild(allOpt);
        fuelTypes.forEach(ft => {
            const o = document.createElement('option');
            o.value = ft.fuel_type_code;
            o.textContent = ft.fuel_name || ft.fuel_type_code;
            sel.appendChild(o);
        });
    }
    loadPriceEditorRecords();
}

function closePriceEditor() {
    history.back();
}

function suggestCorrection(price) {
    // Common misreporting: pounds instead of pence
    if (price < 10) return (price * 100).toFixed(1);
    if (price > 500) return (price / 10).toFixed(1);
    return null;
}

async function loadPriceEditorRecords() {
    const nodeId = priceEditorState.nodeId;
    const fuel = document.getElementById('pe-fuel').value;
    let url = `/prices/station/${encodeURIComponent(nodeId)}/records?limit=500`;
    if (fuel) url += `&fuel_type=${encodeURIComponent(fuel)}`;
    const resp = await apiFetch(url);
    const records = resp.records;
    const station = resp.station;
    if (station) {
        const parts = [station.brand_name, station.city, station.postcode].filter(Boolean);
        document.getElementById('price-editor-subtitle').textContent = parts.join(' · ');
    }
    const body = document.getElementById('price-editor-body');
    if (!records.length) {
        body.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:2rem;color:var(--muted)">No records found</td></tr>';
        return;
    }
    const fmtDate = ts => ts ? new Date(ts).toLocaleString() : '—';
    body.innerHTML = records.map(r => {
        const hasCorrection = r.corrected_price != null;
        const effFlags = r.effective_flags || [];
        const hasEffFlags = effFlags.length > 0;
        const rowClass = hasCorrection
            ? (hasEffFlags ? 'anomaly-row' : 'corrected')
            : (hasEffFlags ? 'anomaly-row' : '');

        // Status: current effective anomaly state
        const statusHtml = hasEffFlags
            ? effFlags.map(f => '<span class="tag">' + escHtml(f) + '</span>').join(' ')
            : (hasCorrection ? '<span class="tag" style="background:#d4edda;color:#155724;">OK</span>' : '—');

        // Overrides: suggestion for unfixed anomalies, or correction details
        let overrideHtml = '—';
        if (hasCorrection) {
            const parts = [`${ppl(r.original_price)} → ${ppl(r.corrected_price)}`];
            if (r.corrected_by) parts.push(`by ${escHtml(r.corrected_by)}`);
            overrideHtml = `<span style="font-size:0.78rem;color:var(--muted);">${parts.join(' ')}</span>`;
        } else if (hasEffFlags) {
            const suggestion = suggestCorrection(r.original_price);
            if (suggestion && canEdit()) {
                overrideHtml = `<a class="suggest-link" onclick="document.getElementById('corr-${r.fuel_price_id}').value='${suggestion}';markPending(${r.fuel_price_id})">${suggestion}p <span style="font-size:0.75rem;color:var(--muted);">(suggested)</span></a>`;
            } else if (suggestion) {
                overrideHtml = `<span style="font-size:0.78rem;color:var(--muted);">${suggestion}p (suggested)</span>`;
            }
        }

        const actions = canEdit()
            ? (hasCorrection
                ? `<button class="btn-revert" onclick="revertCorrection(${r.fuel_price_id})">↩ Revert</button>`
                : `<input id="corr-${r.fuel_price_id}" class="correction-input" type="text" inputmode="decimal" placeholder="pence" onchange="markPending(${r.fuel_price_id})" oninput="markPending(${r.fuel_price_id})">`)
            : (hasCorrection ? '<span style="color:var(--muted);font-size:0.8rem;">Corrected</span>' : '');
        return `<tr class="price-editor-row ${rowClass}" id="pe-row-${r.fuel_price_id}">
            <td>${r.fuel_name || r.fuel_type}</td>
            <td>${ppl(r.original_price)}</td>
            <td>${hasCorrection ? ppl(r.corrected_price) : '—'}</td>
            <td><strong>${ppl(r.effective_price)}</strong></td>
            <td>${fmtDate(r.observed_at)}</td>
            <td>${statusHtml}</td>
            <td>${overrideHtml}</td>
            <td style="white-space:nowrap;">${actions}</td>
        </tr>`;
    }).join('');
    document.getElementById('price-editor-status').className = 'status-msg';
    document.getElementById('price-editor-status').textContent = '';
}

function markPending(fuelPriceId) {
    const input = document.getElementById('corr-' + fuelPriceId);
    const val = (input?.value || '').trim();
    const row = document.getElementById('pe-row-' + fuelPriceId);
    if (val && !isNaN(val) && parseFloat(val) > 0) {
        pendingCorrections.set(fuelPriceId, parseFloat(val));
        if (row) row.classList.add('pending-correction');
    } else {
        pendingCorrections.delete(fuelPriceId);
        if (row) row.classList.remove('pending-correction');
    }
    updatePeSaveBar();
}

function updatePeSaveBar() {
    const bar = document.getElementById('pe-save-bar');
    const btn = document.getElementById('pe-save-all');
    const count = pendingCorrections.size;
    if (bar) bar.style.display = count ? 'flex' : 'none';
    if (btn) btn.textContent = `Save ${count} correction${count !== 1 ? 's' : ''}`;
}

async function saveAllCorrections() {
    if (!pendingCorrections.size) return;
    const btn = document.getElementById('pe-save-all');
    btn.disabled = true;
    btn.textContent = 'Saving…';
    const corrections = [];
    for (const [fuelPriceId, correctedPrice] of pendingCorrections) {
        corrections.push({ fuel_price_id: fuelPriceId, corrected_price: correctedPrice });
    }
    try {
        const resp = await fetch(API + '/corrections/batch', {
            method: 'POST',
            headers: { ...authHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ corrections }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `API error: ${resp.status}`);
        }
        const result = await resp.json();
        const status = document.getElementById('price-editor-status');
        status.className = 'status-msg success';
        status.textContent = `Saved ${result.saved} correction${result.saved !== 1 ? 's' : ''}`;
        pendingCorrections.clear();
        updatePeSaveBar();
        await loadPriceEditorRecords();
    } catch (e) {
        alert('Failed to save corrections: ' + e.message);
    } finally {
        btn.disabled = false;
        updatePeSaveBar();
    }
}

async function revertCorrection(fuelPriceId) {
    if (!confirm('Revert this correction and restore the original price?')) return;
    try {
        const resp = await fetch(API + '/corrections/' + fuelPriceId, {
            method: 'DELETE',
            headers: authHeaders(),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `API error: ${resp.status}`);
        }
        const status = document.getElementById('price-editor-status');
        status.className = 'status-msg success';
        status.textContent = 'Correction reverted';
        await loadPriceEditorRecords();
    } catch (e) {
        alert('Failed to revert: ' + e.message);
    }
}
