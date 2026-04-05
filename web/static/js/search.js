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
            <td><input type="checkbox" class="search-row-cb" value="${escHtml(r.node_id)}" data-name="${escHtml(r.trading_name)}" data-brand="${escHtml(r.brand_name || '')}" data-raw-brand="${escHtml(r.raw_brand_name || '')}" data-city="${escHtml(r.city || '')}" data-postcode="${escHtml(r.postcode || '')}" data-category="${escHtml(r.forecourt_type || '')}" data-lat="${r.latitude || ''}" data-lon="${r.longitude || ''}" data-motorway="${r.is_motorway_service_station || ''}" data-supermarket="${r.is_supermarket_service_station || ''}" data-region="${escHtml(r.region || '')}" data-district="${escHtml(r.admin_district || '')}" onchange="updateTrendButton()"></td>
            <td><a href="#" class="station-link" data-node="${escHtml(r.node_id)}" data-name="${escHtml(r.trading_name)}" data-brand="${escHtml(r.brand_name || '')}" data-raw-brand="${escHtml(r.raw_brand_name || '')}" data-city="${escHtml(r.city || '')}" data-postcode="${escHtml(r.postcode || '')}" data-category="${escHtml(r.forecourt_type || '')}" data-lat="${r.latitude || ''}" data-lon="${r.longitude || ''}" data-motorway="${r.is_motorway_service_station || ''}" data-supermarket="${r.is_supermarket_service_station || ''}" data-region="${escHtml(r.region || '')}" data-district="${escHtml(r.admin_district || '')}" style="color:var(--accent);text-decoration:none;">${escHtml(r.trading_name)}</a></td>
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

    syncSearchDownloadState();
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
    const nodeId = document.getElementById('search-node-id').value.trim();
    const category = getSelectedCategories();
    const district = document.getElementById('search-district').value;
    const constituency = document.getElementById('search-constituency').value;
    const ruralUrban = getMultiSelectValues('search-rural-urban-ms');
    const region = getMultiSelectValues('search-region-ms');
    const country = getMultiSelectValues('search-country-ms');

    let url = `/prices/search?limit=${limit}&offset=${offset}`;
    if (fuel) url += `&fuel_type=${fuel}`;
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
    if (nodeId) url += `&node_id=${encodeURIComponent(nodeId)}`;
    if (category) url += `&category=${encodeURIComponent(category)}`;
    if (district) url += `&district=${encodeURIComponent(district)}`;
    if (constituency) url += `&constituency=${encodeURIComponent(constituency)}`;
    if (ruralUrban) url += `&rural_urban=${encodeURIComponent(ruralUrban)}`;
    if (region) url += `&region=${encodeURIComponent(region)}`;
    if (country) url += `&country=${encodeURIComponent(country)}`;
    return url;
}

function getSearchDlScope() {
    const radio = document.querySelector('input[name="search-dl-scope"]:checked');
    return radio ? radio.value : 'all';
}

function getCheckedNodeIds() {
    return Array.from(document.querySelectorAll('.search-row-cb:checked')).map(cb => cb.value);
}

function syncSearchDownloadState() {
    const hasResults = searchState.total > 0;
    const allFuelsSearch = !document.getElementById('search-fuel').value;
    const scope = getSearchDlScope();
    const selectedIds = getCheckedNodeIds();
    const hasSelection = selectedIds.length > 0;
    const blocked = scope === 'selected' && !hasSelection;

    document.getElementById('search-dl-csv').disabled = !hasResults || blocked;
    document.getElementById('search-dl-json').disabled = !hasResults || blocked;
    document.getElementById('search-hist-dl-csv').disabled = !hasResults || allFuelsSearch || blocked;
    document.getElementById('search-hist-dl-json').disabled = !hasResults || allFuelsSearch || blocked;
    document.getElementById('search-hist-dl-csv').title = allFuelsSearch ? 'Select a single fuel type to export historical data' : '';
    document.getElementById('search-hist-dl-json').title = allFuelsSearch ? 'Select a single fuel type to export historical data' : '';
}

async function downloadSearchData(fmt) {
    const btn = document.getElementById('search-dl-' + fmt);
    btn.disabled = true;
    btn.textContent = '⏳ ' + fmt.toUpperCase();
    try {
        const data = await apiFetch(buildSearchUrl(10000, 0));
        let rows = data.results;
        if (getSearchDlScope() === 'selected') {
            const ids = new Set(getCheckedNodeIds());
            rows = rows.filter(r => ids.has(r.node_id));
        }
        downloadFile(rows, 'fuel-search-results', fmt);
    } finally {
        btn.disabled = false;
        btn.textContent = '⬇ ' + fmt.toUpperCase();
    }
}

function downloadSearchHistoryData(fmt) {
    let url = buildSearchUrl(10000, 0)
        .replace('/prices/search?', '/prices/search/export?')
        .replace(/&limit=\d+/, '').replace(/&offset=\d+/, '');
    if (getSearchDlScope() === 'selected') {
        const ids = getCheckedNodeIds();
        if (ids.length) url += '&node_ids=' + encodeURIComponent(ids.join(','));
    }
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
    syncSearchDownloadState();
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

function goToOverrideStation() {
    const st = stationTrendState;
    document.getElementById('override-node').value = st.nodeId;
    // Pre-populate the station card from data we already have
    lookupStationForOverride();
    switchTab('data');
    document.getElementById('data-section').value = 'overrides';
    switchDataSection();
    history.replaceState({ panel: 'data', section: 'overrides' }, '', '#data/overrides');
}

function initStationTrendFuels() {
    const sel = document.getElementById('st-fuel');
    if (!sel.options.length) {
        const allOpt = document.createElement('option');
        allOpt.value = ''; allOpt.textContent = 'All fuel types';
        sel.appendChild(allOpt);
        fuelTypes.forEach(ft => {
            const o = document.createElement('option');
            o.value = ft.fuel_type_code;
            o.textContent = ft.fuel_name;
            sel.appendChild(o);
        });
    }
    // Sync fuel type from search panel
    const searchFuel = document.getElementById('search-fuel').value;
    sel.value = searchFuel; // empty string = "All fuel types"
}

function openStationTrend(nodeId, name, brand, city, postcode, category, rawBrand, lat, lon, motorway, supermarket, region, district) {
    stationTrendState = { mode: 'single', nodeId, nodeIds: null, title: name, postcode, lat, lon };
    initStationTrendFuels();
    // Reset range to 30 days
    document.getElementById('st-range').value = '30';
    document.getElementById('st-granularity').value = 'auto';
    document.getElementById('station-trend-title').textContent = name;

    // If key detail fields are missing, fetch them via lookup then re-render subtitle
    if (!lat && !lon && !region && !district) {
        renderStationSubtitle(nodeId, name, brand, city, postcode, category, rawBrand, lat, lon, motorway, supermarket, region, district);
        apiPost('/stations/lookup', { node_ids: [nodeId] }).then(data => {
            const s = data.results && data.results[0];
            if (s && s.found) {
                stationTrendState.lat = s.latitude;
                stationTrendState.lon = s.longitude;
                renderStationSubtitle(nodeId, s.trading_name || name, s.brand || brand, s.city || city, s.postcode || postcode, s.forecourt_type || category, s.raw_brand || rawBrand, s.latitude, s.longitude, s.is_motorway_service_station, s.is_supermarket_service_station, s.region, s.admin_district);
            }
        }).catch(() => {});
    } else {
        renderStationSubtitle(nodeId, name, brand, city, postcode, category, rawBrand, lat, lon, motorway, supermarket, region, district);
    }

    showStationTrendPanel();
    setStationTrendRange('30');
}

function renderStationSubtitle(nodeId, name, brand, city, postcode, category, rawBrand, lat, lon, motorway, supermarket, region, district) {
    const subtitleEl = document.getElementById('station-trend-subtitle');
    const parts = [city, postcode].filter(Boolean);
    if (district && district !== city) parts.push(district);
    if (region) parts.push(region);
    const rawLabel = rawBrand || brand || '';
    let subtitle = rawLabel + (parts.length ? ' · ' + parts.join(' · ') : '')
        + ' · UK Fuel Finder node id: ' + nodeId;
    let badges = '';
    if (brand && rawBrand && brand !== rawBrand) {
        badges += ' · <span style="font-size:0.8rem;background:var(--accent,#1d70b8);color:#fff;padding:0.1rem 0.45rem;border-radius:3px;">' + escHtml(brand) + '</span>';
    }
    if (category) badges += ' · ' + categoryTag(category);
    if (motorway === 'true' || motorway === true) {
        badges += ' · <span style="font-size:0.8rem;background:#912b88;color:#fff;padding:0.1rem 0.45rem;border-radius:3px;" title="is_motorway_service_station flag set in GOV.UK Fuel Finder source data">Motorway</span>';
    }
    if (supermarket === 'true' || supermarket === true) {
        badges += ' · <span style="font-size:0.8rem;background:#00703c;color:#fff;padding:0.1rem 0.45rem;border-radius:3px;" title="is_supermarket_service_station flag set in GOV.UK Fuel Finder source data (unreliable — some non-supermarkets are flagged)">Supermarket</span>';
    }
    if (lat && lon) {
        badges += ` · <a href="https://www.google.com/maps?q=${lat},${lon}" target="_blank" rel="noopener" style="font-size:0.8rem;color:var(--muted);text-decoration:none;" title="${lat}, ${lon}">📍 Coordinates</a>`;
    }
    subtitleEl.innerHTML = escHtml(subtitle) + badges;
}

function viewSelectedTrend() {
    const checked = document.querySelectorAll('.search-row-cb:checked');
    if (!checked.length) return;
    const ids = Array.from(checked).map(cb => cb.value);
    const names = Array.from(checked).map(cb => cb.dataset.name);

    if (ids.length === 1) {
        const cb = checked[0];
        openStationTrend(cb.value, cb.dataset.name, cb.dataset.brand, cb.dataset.city, cb.dataset.postcode, cb.dataset.category, cb.dataset.rawBrand, cb.dataset.lat, cb.dataset.lon, cb.dataset.motorway, cb.dataset.supermarket, cb.dataset.region, cb.dataset.district);
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

    const isSingle = stationTrendState.mode === 'single';
    const allFuels = !fuel;

    function buildUrl(fuelCode) {
        let url;
        if (stationTrendState.mode === 'single') {
            url = `/prices/station/${encodeURIComponent(stationTrendState.nodeId)}/history?fuel_type=${fuelCode}`;
        } else if (stationTrendState.mode === 'search') {
            url = `/prices/history?fuel_type=${fuelCode}`;
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
            url = `/prices/history?fuel_type=${fuelCode}&node_ids=${encodeURIComponent(ids)}`;
        }
        if (startDate) url += `&start_date=${startDate}`;
        if (endDate) url += `&end_date=${endDate}`;
        if (!startDate && !endDate) url += '&days=30';
        if (gran !== 'auto') url += `&granularity=${gran}`;
        return url;
    }

    // Show table view / override buttons for single-station views
    document.getElementById('st-edit-btn').style.display = isSingle ? '' : 'none';
    document.getElementById('st-override-btn').style.display =
        isSingle && canEdit() ? '' : 'none';

    if (allFuels) {
        const { datasets, granularity } = await fetchAllFuelTrends(buildUrl);
        const hourly = granularity === 'hourly';
        const hasData = datasets.length > 0;
        lastStationTrendData = [];
        document.getElementById('st-dl-csv').disabled = true;
        document.getElementById('st-dl-json').disabled = true;
        document.getElementById('st-dl-csv').title = 'Select a single fuel type to export raw data';
        document.getElementById('st-dl-json').title = 'Select a single fuel type to export raw data';
        const suffix = ' — all fuel types';
        document.getElementById('st-trend-heading').textContent =
            isSingle ? (hourly ? 'Hourly price' + suffix : 'Daily price' + suffix)
                     : (hourly ? 'Hourly average price' + suffix : 'Daily average price' + suffix);
        document.getElementById('st-granularity-note').textContent = hourly
            ? 'Showing per scrape window (data is fetched every 30 minutes).'
            : 'Showing daily ' + (isSingle ? 'prices.' : 'averages.');

        const hampelNote = document.getElementById('st-hampel-note');
        if (hampelNote) {
            hampelNote.innerHTML = isSingle
                ? 'Anomaly-flagged prices are excluded. All other prices are shown unfiltered — <a href="/docs/about#outlier-methodology" style="color:var(--accent);">Hampel smoothing</a> is only applied to multi-station aggregate charts.'
                : 'Anomaly-flagged prices are excluded. A <a href="/docs/about#outlier-methodology" style="color:var(--accent);">Hampel filter</a> (rolling median ± 3×MAD) smooths remaining outlier averages without distorting trends.';
        }

        if (charts['chart-station-trend']) charts['chart-station-trend'].destroy();
        const ctx = document.getElementById('chart-station-trend').getContext('2d');
        charts['chart-station-trend'] = new Chart(ctx, {
            type: 'line',
            data: { datasets },
            options: {
                responsive: true,
                scales: {
                    x: {
                        type: 'time',
                        time: {
                            unit: hourly ? 'hour' : 'day',
                            tooltipFormat: hourly ? 'd MMM, HH:mm' : 'd MMM yyyy',
                            displayFormats: { hour: 'd MMM HH:mm', day: 'd MMM' }
                        },
                        ticks: { maxRotation: 45 }
                    },
                    y: { beginAtZero: false }
                },
                plugins: {
                    legend: { display: true },
                    tooltip: {
                        callbacks: {
                            label: (item) => `${item.dataset.label}: ${item.parsed.y}p`
                        }
                    }
                }
            }
        });
        return;
    }

    const resp = await apiFetch(buildUrl(fuel));
    const data = resp.data;
    lastStationTrendData = data;

    document.getElementById('st-dl-csv').disabled = !data.length;
    document.getElementById('st-dl-json').disabled = !data.length;
    document.getElementById('st-dl-csv').title = '';
    document.getElementById('st-dl-json').title = '';
    const hourly = resp.granularity === 'hourly';
    document.getElementById('st-trend-heading').textContent =
        isSingle ? (hourly ? 'Hourly price' : 'Daily price')
                 : (hourly ? 'Hourly average price' : 'Daily average price');
    document.getElementById('st-granularity-note').textContent = hourly
        ? 'Showing per scrape window (data is fetched every 30 minutes).'
        : 'Showing daily ' + (isSingle ? 'prices.' : 'averages.');

    // Show Hampel filter note for multi-station views (aggregate data uses Hampel smoothing)
    const hampelNote = document.getElementById('st-hampel-note');
    if (hampelNote) {
        hampelNote.innerHTML = isSingle
            ? 'Anomaly-flagged prices are excluded. All other prices are shown unfiltered — <a href="/docs/about#outlier-methodology" style="color:var(--accent);">Hampel smoothing</a> is only applied to multi-station aggregate charts.'
            : 'Anomaly-flagged prices are excluded. A <a href="/docs/about#outlier-methodology" style="color:var(--accent);">Hampel filter</a> (rolling median ± 3×MAD) smooths remaining outlier averages without distorting trends.';
    }

    const chartData = data.map(d => ({ x: new Date(d.bucket), y: d.avg_price }));

    if (charts['chart-station-trend']) charts['chart-station-trend'].destroy();
    const ctx = document.getElementById('chart-station-trend').getContext('2d');
    charts['chart-station-trend'] = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: isSingle ? 'Pence/litre' : 'Avg pence/litre',
                data: chartData,
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
                x: {
                    type: 'time',
                    time: {
                        unit: hourly ? 'hour' : 'day',
                        tooltipFormat: hourly ? 'd MMM, HH:mm' : 'd MMM yyyy',
                        displayFormats: { hour: 'd MMM HH:mm', day: 'd MMM' }
                    },
                    ticks: { maxRotation: 45 }
                },
                y: { beginAtZero: false }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
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

    let url = `/api/prices/history/export?format=${fmt}`;
    if (fuel) url += `&fuel_type=${encodeURIComponent(fuel)}`;
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
        const rawLabel = station.raw_brand_name || station.brand_name || '';
        const parts = [station.city, station.postcode].filter(Boolean);
        let subtitle = rawLabel + (parts.length ? ' · ' + parts.join(' · ') : '')
            + ' · UK Fuel Finder node id: ' + nodeId;
        let badges = '';
        if (station.brand_name && station.raw_brand_name && station.brand_name !== station.raw_brand_name) {
            badges += ' · <span style="font-size:0.8rem;background:var(--accent,#1d70b8);color:#fff;padding:0.1rem 0.45rem;border-radius:3px;">' + escHtml(station.brand_name) + '</span>';
        }
        if (station.forecourt_type) badges += ' · ' + categoryTag(station.forecourt_type);
        document.getElementById('price-editor-subtitle').innerHTML = escHtml(subtitle) + badges;
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
            ? effFlags.map(f => {
                const iqrMatch = f.match(/^current_iqr_outlier:([\d.]+)([<>])([\d.]+)$/);
                if (iqrMatch) {
                    const fence = iqrMatch[3];
                    const label = iqrMatch[2] === '<' ? `below ${fence}p lower fence` : `above ${fence}p upper fence`;
                    return `<span class="tag" style="background:#fff3cd;color:#856404;">${label}</span>`;
                }
                if (f === 'current_iqr_outlier') return '<span class="tag" style="background:#fff3cd;color:#856404;">outside current IQR fence</span>';
                return '<span class="tag">' + escHtml(f) + '</span>';
            }).join(' ')
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
