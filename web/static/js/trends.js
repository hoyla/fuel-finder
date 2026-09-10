// ---------------------------------------------------------------------------
// Trends
// ---------------------------------------------------------------------------
let lastTrendData = [];
const comparisonState = { data: null, rows: [], loading: false, template: null };
const comparisonBaselines = {
    no_age_limit: 'No age limit',
    event_weighted: 'Change reports (unsmoothed)',
    api_displayed: 'Existing chart (Hampel-filtered)',
    equal_reporting_station: 'Equal-weight reporting stations',
};
const comparisonAgeLimits = ['none', '30', '14', '7', 'today'];

function comparisonNumber(value, precision = 2) {
    if (value == null || !Number.isFinite(Number(value))) return 'n/a';
    return Number(value).toLocaleString('en-GB', { minimumFractionDigits: precision, maximumFractionDigits: precision });
}

function comparisonMean(values) {
    const available = values.filter(value => value != null && Number.isFinite(value));
    return available.length ? available.reduce((sum, value) => sum + value, 0) / available.length : null;
}

function comparisonPercent(numerator, denominator) {
    return denominator ? 100 * numerator / denominator : null;
}

function comparisonDifference(first, second) {
    return first == null || second == null ? null : first - second;
}

function comparisonSigned(value) {
    return (value > 0 ? '+' : '') + comparisonNumber(value);
}

function comparisonMeasure(value, unit, signed = false) {
    if (value == null || !Number.isFinite(value)) return 'n/a';
    return (signed ? comparisonSigned(value) : comparisonNumber(value)) + unit;
}

function comparisonDate(value) {
    return new Date(value + 'T00:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

async function loadTrendComparison() {
    if (comparisonState.loading) return;
    if (comparisonState.data) { renderTrendComparison(); return; }
    comparisonState.loading = true;
    const status = document.getElementById('comparison-status');
    status.textContent = 'Loading comparison...';
    document.getElementById('comparison-retry').hidden = true;
    try {
        const data = await apiFetch('/local/trend-comparison');
        if (!data.fuels?.E10?.daily?.length || !data.fuels?.B7_STANDARD?.daily?.length) throw new Error('Invalid audit dataset');
        for (const fuel of ['E10', 'B7_STANDARD']) {
            if (!comparisonAgeLimits.every(policy => data.fuels[fuel].age_sensitivity?.[policy]?.daily?.length === data.fuels[fuel].daily.length)) throw new Error('Age sensitivity dataset missing');
        }
        comparisonState.data = data;
        const dates = data.fuels.E10.daily.map(row => row.date);
        const parameters = new URL(location.href).searchParams;
        setFuelSelection('comparison-fuel', parameters.get('compareFuel') ?? 'E10');
        const baseline = parameters.get('compareBaseline');
        document.getElementById('comparison-baseline').value = Object.hasOwn(comparisonBaselines, baseline) ? baseline : 'no_age_limit';
        const ageLimit = parameters.get('compareAge');
        document.getElementById('comparison-age-limit').value = comparisonAgeLimits.includes(ageLimit) ? ageLimit : 'none';
        document.getElementById('comparison-group').value = parameters.get('compareGroup') === 'forecourt_type' ? 'forecourt_type' : 'region';
        for (const [suffix, parameter, fallback] of [['from', 'compareFrom', dates[0]], ['to', 'compareTo', dates.at(-1)]]) {
            const input = document.getElementById('comparison-' + suffix);
            input.min = dates[0];
            input.max = dates.at(-1);
            input.value = parameters.get(parameter) || fallback;
        }
        document.getElementById('comparison-controls').disabled = false;
        const captured = new Date(data.snapshot.captured_at).toLocaleString('en-GB', { timeZone: 'UTC', dateStyle: 'medium', timeStyle: 'short' });
        document.getElementById('comparison-snapshot').textContent = 'Snapshot: ' + captured + ' UTC. Not live.';
        document.getElementById('comparison-source-hash').textContent = data.snapshot.sha256;
        document.getElementById('comparison-audit-hash').textContent = data.audit_script_sha256;
        renderTrendComparison();
    } catch (error) {
        comparisonState.data = null;
        comparisonState.rows = [];
        status.textContent = 'Comparison unavailable. The local audit dataset could not be loaded.';
        document.getElementById('comparison-controls').disabled = true;
        document.getElementById('comparison-results').hidden = true;
        document.getElementById('comparison-retry').hidden = false;
    } finally {
        comparisonState.loading = false;
    }
}

function comparisonChart(canvas, rows, datasets, ylabel, stacked = false) {
    const id = canvas.id;
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(canvas, {
        type: 'line',
        data: { labels: rows.map(row => row.date), datasets },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            interaction: { mode: 'index', intersect: false },
            elements: { line: { tension: 0, borderWidth: 2 }, point: { radius: 1.5, hoverRadius: 4 } },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 7, maxRotation: 0, callback(value) { return comparisonDate(this.getLabelForValue(value)); } } },
                y: { title: { display: true, text: ylabel }, stacked, ...(stacked ? { min: 0, max: 100 } : {}), ...(ylabel === 'Stations' ? { min: 0 } : {}) },
            },
            plugins: {
                legend: { position: 'bottom', labels: { boxWidth: 14, font: { size: 11 } } },
                tooltip: { callbacks: {
                    title: items => comparisonDate(items[0].label) + ' UTC',
                    label: item => item.dataset.label + ': ' + comparisonNumber(item.parsed.y, ylabel === 'Stations' ? 0 : 2) + (ylabel === 'Pence per litre' ? 'p/l' : ylabel === '% of station-hours' ? '%' : ''),
                } },
            },
        },
    });
}

function renderTrendComparison() {
    if (!comparisonState.data) return;
    const results = document.getElementById('comparison-results');
    comparisonState.template ||= results.cloneNode(true);
    for (const id of Object.keys(charts).filter(key => key.startsWith('chart-comparison-'))) {
        charts[id].destroy();
        delete charts[id];
    }
    results.replaceChildren();
    comparisonState.rows = [];
    for (const type of selectedFuelTypes(getFuelSelection('comparison-fuel'))) {
        const fuel = type.fuel_type_code;
        if (!comparisonState.data.fuels[fuel]) continue;
        const section = comparisonState.template.cloneNode(true);
        section.id = 'comparison-results-' + fuel;
        section.dataset.comparisonFuel = fuel;
        for (const element of section.querySelectorAll('[id]')) {
            element.dataset.comparisonId = element.id;
            element.id += '-' + fuel;
        }
        const heading = document.createElement('h2');
        heading.textContent = type.fuel_name;
        section.prepend(heading);
        results.append(section);
        renderComparisonFuel(fuel, section);
    }
    results.hidden = !comparisonState.rows.length;
}

function renderComparisonFuel(fuel, section) {
    const find = id => section.querySelector('[data-comparison-id="' + id + '"]') || document.getElementById(id);
    const baseline = document.getElementById('comparison-baseline').value;
    const from = document.getElementById('comparison-from');
    const to = document.getElementById('comparison-to');
    const status = document.getElementById('comparison-status');
    const results = section;
    const download = document.getElementById('comparison-download');
    const valid = from.value && to.value && from.checkValidity() && to.checkValidity() && from.value <= to.value;
    const audit = comparisonState.data.fuels[fuel];
    const ageLimit = document.getElementById('comparison-age-limit').value;
    const ageSeries = audit.age_sensitivity[ageLimit];
    const selected = new Map(ageSeries.daily.map(row => [row.date, row]));
    const rows = valid ? audit.daily.filter(row => row.date >= from.value && row.date <= to.value).map(row => ({ ...row, ...selected.get(row.date), no_age_limit: row.equal_station_hourly })) : [];
    comparisonState.rows.push(...rows.map(row => ({...row, fuel_type: fuel})));
    results.hidden = !rows.length;
    download.disabled = !rows.length || _userRole === 'readonly';
    document.querySelector('[data-comparison-action="copy"]').disabled = !rows.length;
    find('comparison-group-download').disabled = !rows.length || _userRole === 'readonly';
    if (!rows.length) {
        status.textContent = 'Select a valid date range from ' + from.min + ' to ' + to.max + ', with the start on or before the end.';
        return;
    }
    const populated = rows.filter(row => row.price != null);
    status.textContent = populated.length === rows.length ? '' : (rows.length - populated.length) + ' day(s) have no eligible hourly prices under this age limit. Missing values are gaps, not zero prices.';
    document.getElementById('comparison-age-note').textContent = ageLimit === 'today'
        ? 'Recorded today: since midnight of each historical UTC day. Late reports contribute fewer sampled hours; reports after 23:00 may contribute no hourly sample that day. This is a narrow sensitivity test, not a confirmation of current pump prices.'
        : ageLimit === 'none' ? 'No age limit: latest known report at each historical hour, regardless of change-record age. No closure cutoff.'
        : ageSeries.label + ': exclude hourly prices whose latest change record is more than ' + ageLimit + ' days old at that hour. This does not establish whether excluded prices were wrong.';
    const total = key => rows.reduce((sum, row) => sum + row[key], 0);
    const gap = comparisonMean(populated.map(row => Math.abs(row[baseline] - row.price)));
    find('comparison-gap').textContent = comparisonMeasure(gap, 'p/l');
    find('comparison-price').textContent = comparisonMeasure(comparisonMean(populated.map(row => row.price)), 'p/l');
    find('comparison-age-difference').textContent = comparisonMeasure(comparisonMean(populated.map(row => row.difference_from_no_limit)), 'p/l', true);
    find('comparison-excluded').textContent = comparisonNumber(total('excluded_stations') / rows.length, 0);
    find('comparison-removed-hours').textContent = comparisonMeasure(comparisonPercent(total('age_excluded_hours'), total('no_limit_hours')), '%');
    find('comparison-reporters').textContent = comparisonNumber(total('reporting_stations') / rows.length, 0);
    find('comparison-stations').textContent = comparisonNumber(total('included_stations') / rows.length, 0);
    find('comparison-age').textContent = comparisonMeasure(comparisonPercent(total('observation_over_30_days_hours'), total('eligible_hours')), '%');
    const days = rows.length === 1 ? '1 day' : rows.length + ' days';
    find('comparison-price-note').textContent = days + ', ' + comparisonDate(rows[0].date) + ' to ' + comparisonDate(rows.at(-1).date) + ' (UTC). ' + ageSeries.label + '; equal station weight, hourly last-known prices, no smoothing. Means omit days without data.';
    comparisonChart(find('chart-comparison-price'), rows, [
        { label: comparisonBaselines[baseline], data: rows.map(row => row[baseline]), borderColor: '#ba3b26', backgroundColor: '#ba3b26' },
        { label: 'Last-known: ' + ageSeries.label.toLowerCase(), data: rows.map(row => row.price), borderColor: '#087e78', backgroundColor: '#087e78' },
    ], 'Pence per litre');
    comparisonChart(find('chart-comparison-coverage'), rows, [
        { label: 'No age limit', data: rows.map(row => row.no_limit_stations), borderColor: '#767676', backgroundColor: '#767676', borderDash: [4, 3] },
        { label: 'Included: ' + ageSeries.label.toLowerCase(), data: rows.map(row => row.included_stations), borderColor: '#087e78', backgroundColor: '#087e78' },
    ], 'Stations');
    comparisonChart(find('chart-comparison-age'), rows, [
        { label: 'Up to 30 days', data: rows.map(row => comparisonPercent(row.eligible_hours - row.observation_over_30_days_hours, row.eligible_hours)), borderColor: '#1d70b8', backgroundColor: '#1d70b844', fill: true },
        { label: 'Over 30 to 90 days', data: rows.map(row => comparisonPercent(row.observation_over_30_days_hours - row.observation_over_90_days_hours, row.eligible_hours)), borderColor: '#b87808', backgroundColor: '#b8780866', fill: true },
        { label: 'Over 90 days', data: rows.map(row => comparisonPercent(row.observation_over_90_days_hours, row.eligible_hours)), borderColor: '#a23451', backgroundColor: '#a2345166', fill: true },
    ], '% of station-hours', true);
    find('comparison-baseline-heading').textContent = comparisonBaselines[baseline] + ' (p/l)';
    find('comparison-body').innerHTML = rows.map(row => {
        const difference = comparisonDifference(row[baseline], row.price);
        return '<tr><th scope="row">' + escHtml(comparisonDate(row.date)) + '</th><td>' + comparisonNumber(row[baseline]) + '</td><td>' + comparisonNumber(row.price) + '</td><td>' + comparisonSigned(difference) + '</td><td>' + comparisonSigned(row.difference_from_no_limit) + '</td><td>' + comparisonNumber(row.included_stations, 0) + '</td><td>' + comparisonNumber(row.excluded_stations, 0) + '</td><td>' + comparisonNumber(row.eligible_hours, 0) + '</td></tr>';
    }).join('');
    find('comparison-age-summary').innerHTML = comparisonAgeLimits.map(policy => {
        const series = audit.age_sensitivity[policy];
        const values = series.daily.filter(row => row.date >= from.value && row.date <= to.value);
        const mean = comparisonMean(values.map(row => row.price));
        const difference = comparisonMean(values.map(row => row.difference_from_no_limit));
        const change = values.length > 1 ? comparisonDifference(values.at(-1).price, values[0].price) : null;
        return '<tr' + (policy === ageLimit ? ' aria-current="true"' : '') + '><th scope="row">' + escHtml(series.label) + '</th><td>' + comparisonNumber(mean) + '</td><td>' + comparisonSigned(difference) + '</td><td>' + comparisonNumber(comparisonMean(values.map(row => row.included_stations)), 0) + '</td><td>' + comparisonNumber(comparisonMean(values.map(row => row.excluded_stations)), 0) + '</td><td>' + comparisonSigned(change) + '</td><td>' + values.filter(row => row.price == null).length + '</td></tr>';
    }).join('');
    const groups = comparisonGroupRows(fuel, rows);
    find('comparison-groups').innerHTML = groups.map(row => '<tr><th scope="row">' + escHtml(row.group) + '</th><td>' + comparisonNumber(row.no_limit_stations_per_day, 1) + '</td><td>' + comparisonNumber(row.included_stations_per_day, 1) + '</td><td>' + comparisonNumber(row.excluded_stations_per_day, 1) + '</td><td>' + comparisonNumber(row.excluded_pct) + '%</td><td>' + comparisonSigned(row.vs_national_pp) + '</td><td>' + comparisonNumber(row.hours_removed_pct) + '%</td></tr>').join('');
    find('comparison-group-note').textContent = ageSeries.label + '. National fully excluded station-day rate: ' + comparisonNumber(comparisonPercent(total('excluded_stations'), total('no_limit_stations'))) + '%. Rates use unrounded counts.';
    find('comparison-verification').textContent = 'Full audit window: ' + audit.checks.daily_cache.mismatches + ' daily-cache mismatches. Independent SQL check for ' + ageSeries.label.toLowerCase() + ' on ' + comparisonDate(ageSeries.sql_check.date) + '. ' + audit.checks.local_api.hampel_changed_days + ' point(s) altered by Hampel smoothing in the existing chart. The selected subrange does not recompute that filter.';
    const url = new URL(location.href);
    for (const [key, value] of Object.entries({ compareFuel: getFuelSelection('comparison-fuel'), compareFrom: from.value, compareTo: to.value, compareBaseline: baseline, compareAge: ageLimit, compareGroup: document.getElementById('comparison-group').value })) url.searchParams.set(key, value);
    url.hash = 'trend-comparison';
    history.replaceState({ panel: 'trend-comparison' }, '', url);
}

function comparisonGroupRows(fuel, rows = comparisonState.rows.filter(row => row.fuel_type === fuel)) {
    const ageLimit = document.getElementById('comparison-age-limit').value;
    const dimension = document.getElementById('comparison-group').value;
    const dates = new Set(rows.map(row => row.date));
    const groups = new Map();
    const keys = ['no_limit_stations', 'included_stations', 'excluded_stations', 'no_limit_hours', 'included_hours', 'age_excluded_hours'];
    for (const row of comparisonState.data.fuels[fuel].age_sensitivity[ageLimit].groups) {
        if (!dates.has(row.date) || row.dimension !== dimension) continue;
        if (!groups.has(row.group)) groups.set(row.group, Object.fromEntries(keys.map(key => [key, 0])));
        const group = groups.get(row.group);
        for (const key of keys) group[key] += row[key];
    }
    const noLimit = rows.reduce((sum, row) => sum + row.no_limit_stations, 0);
    const excluded = rows.reduce((sum, row) => sum + row.excluded_stations, 0);
    const national = comparisonPercent(excluded, noLimit);
    return [...groups].map(([group, totals]) => ({
        dimension, group, no_limit_station_days: totals.no_limit_stations,
        included_station_days: totals.included_stations, excluded_station_days: totals.excluded_stations,
        no_limit_stations_per_day: totals.no_limit_stations / dates.size,
        included_stations_per_day: totals.included_stations / dates.size,
        excluded_stations_per_day: totals.excluded_stations / dates.size,
        excluded_pct: comparisonPercent(totals.excluded_stations, totals.no_limit_stations),
        national_excluded_pct: national,
        vs_national_pp: comparisonDifference(comparisonPercent(totals.excluded_stations, totals.no_limit_stations), national),
        no_limit_station_hours: totals.no_limit_hours, included_station_hours: totals.included_hours,
        age_excluded_station_hours: totals.age_excluded_hours,
        hours_removed_pct: comparisonPercent(totals.age_excluded_hours, totals.no_limit_hours),
    })).sort((first, second) => second.excluded_pct - first.excluded_pct || first.group.localeCompare(second.group));
}

document.addEventListener('change', event => {
    if (event.target.matches('[data-comparison-control]')) renderTrendComparison();
});

document.addEventListener('click', async event => {
    const action = event.target.closest('[data-comparison-action]')?.dataset.comparisonAction;
    if (!action) return;
    if (action === 'retry') { await loadTrendComparison(); return; }
    if (!comparisonState.data) return;
    if (action === 'reset') {
        for (const suffix of ['from', 'to']) {
            const input = document.getElementById('comparison-' + suffix);
            input.value = suffix === 'from' ? input.min : input.max;
        }
        renderTrendComparison();
    } else if (action === 'copy' && comparisonState.rows.length) {
        try {
            await navigator.clipboard.writeText(location.href);
            document.getElementById('comparison-status').textContent = 'Comparison link copied.';
        } catch {
            document.getElementById('comparison-status').textContent = 'Clipboard access unavailable. The address bar contains this comparison link.';
        }
    } else if (action === 'download' && comparisonState.rows.length && _userRole !== 'readonly') {
        const fuel = getFuelSelection('comparison-fuel') || 'all';
        const baseline = document.getElementById('comparison-baseline').value;
        const rows = comparisonState.rows.map(row => ({
            snapshot_captured_at: comparisonState.data.snapshot.captured_at,
            source_snapshot_sha256: comparisonState.data.snapshot.sha256,
            audit_script_sha256: comparisonState.data.audit_script_sha256,
            age_limit: document.getElementById('comparison-age-limit').value,
            fuel_type: row.fuel_type, date_utc: row.date, baseline_method: baseline,
            baseline_ppl: row[baseline], reconstructed_ppl: row.price,
            gap_ppl: comparisonDifference(row[baseline], row.price),
            no_limit_ppl: row.no_limit_price, difference_from_no_limit_ppl: row.difference_from_no_limit,
            reporting_stations: row.reporting_stations, included_stations: row.included_stations,
            fully_excluded_stations: row.excluded_stations, no_limit_stations: row.no_limit_stations,
            age_excluded_station_hours: row.age_excluded_hours, no_limit_station_hours: row.no_limit_hours,
            eligible_station_hours: row.eligible_hours, flagged_station_hours: row.flagged_hours,
            unknown_station_hours: row.unknown_hours,
            observation_over_30_days_hours: row.observation_over_30_days_hours,
            observation_over_90_days_hours: row.observation_over_90_days_hours,
        }));
        downloadFile(rows, 'trend-comparison-' + fuel + '-age-' + rows[0].age_limit + '-' + rows[0].date_utc + '-to-' + rows.at(-1).date_utc, 'csv');
    } else if (action === 'download-groups' && comparisonState.rows.length && _userRole !== 'readonly') {
        const fuel = event.target.closest('[data-comparison-fuel]').dataset.comparisonFuel;
        const ageLimit = document.getElementById('comparison-age-limit').value;
        const rows = comparisonGroupRows(fuel).map(row => ({
            snapshot_captured_at: comparisonState.data.snapshot.captured_at,
            source_snapshot_sha256: comparisonState.data.snapshot.sha256,
            audit_script_sha256: comparisonState.data.audit_script_sha256,
            classification_basis: 'current snapshot, not historical membership', fuel_type: fuel, age_limit: ageLimit,
            from_utc: comparisonState.rows[0].date, to_utc: comparisonState.rows.at(-1).date, ...row,
        }));
        downloadFile(rows, 'trend-exclusions-' + fuel + '-age-' + ageLimit + '-' + document.getElementById('comparison-group').value, 'csv');
    }
});

function downloadTrendData(fmt) {
    const fuel = selectedFuelTypes(getFuelSelection('trend-fuel')).map(type => type.fuel_type_code).join(',');
    const region = getMultiSelectValues('trend-region-ms');
    const country = getMultiSelectValues('trend-country-ms');
    const ruralUrban = getMultiSelectValues('trend-rural-urban-ms');
    const startDate = document.getElementById('trend-start').value;
    const endDate = document.getElementById('trend-end').value;
    let url = `/api/prices/history/export?format=${fmt}`;
    if (fuel) url += `&fuel_type=${encodeURIComponent(fuel)}`;
    if (startDate) url += `&start_date=${startDate}`;
    if (endDate) url += `&end_date=${endDate}`;
    if (!startDate && !endDate) url += '&days=30';
    if (region) url += `&region=${encodeURIComponent(region)}`;
    if (country) url += `&country=${encodeURIComponent(country)}`;
    if (ruralUrban) url += `&rural_urban=${encodeURIComponent(ruralUrban)}`;

    const parts = ['fuel-prices'];
    if (fuel) parts.push(fuel.replace(/\s+/g, '-'));
    if (region) region.split(',').forEach(v => parts.push(v.trim().replace(/\s+/g, '-')));
    if (country) country.split(',').forEach(v => parts.push(v.trim().replace(/\s+/g, '-')));
    if (ruralUrban) ruralUrban.split(',').forEach(v => parts.push(v.trim().replace(/\s+/g, '-')));
    if (startDate) parts.push('from-' + startDate);
    if (endDate) parts.push('to-' + endDate);

    fetchExport(url, parts, fmt, document.getElementById(fmt === 'csv' ? 'trend-dl-csv' : 'trend-dl-json'));
}

function setTrendRange(value, refresh = true) {
    const customFields = value === 'custom';
    document.getElementById('trend-start-ctl').style.display = customFields || reconstructedHistoryEnabled ? '' : 'none';
    document.getElementById('trend-end-ctl').style.display = customFields || reconstructedHistoryEnabled ? '' : 'none';
    document.getElementById('trend-start').readOnly = reconstructedHistoryEnabled && !customFields;
    document.getElementById('trend-end').readOnly = reconstructedHistoryEnabled && !customFields;
    if (!customFields) {
        const end = new Date();
        document.getElementById('trend-end').value = end.toISOString().slice(0, 10);
        if (value === 'all') {
            document.getElementById('trend-start').value = '';
        } else {
            const start = new Date();
            start.setDate(start.getDate() - parseInt(value) + (reconstructedHistoryEnabled ? 1 : 0));
            document.getElementById('trend-start').value = start.toISOString().slice(0, 10);
        }
        if (refresh && !reconstructedHistoryEnabled) loadTrends();
    }
    if (refresh && reconstructedHistoryEnabled) updateHistoryFilterState('trend');
}
// Set initial dates for default 30-day range
setTrendRange('30', false);

async function loadTrends() {
    const fuel = getFuelSelection('trend-fuel');
    const region = getMultiSelectValues('trend-region-ms');
    const country = getMultiSelectValues('trend-country-ms');
    const ruralUrban = getMultiSelectValues('trend-rural-urban-ms');
    const startDate = document.getElementById('trend-start').value;
    const endDate = document.getElementById('trend-end').value;
    const gran = document.getElementById('trend-granularity').value;

    function buildUrl(fuelCode) {
        let url = `/prices/history?fuel_type=${fuelCode}`;
        if (startDate && document.getElementById('trend-range').value !== 'all') url += `&start_date=${startDate}`;
        if (endDate) url += `&end_date=${endDate}`;
        if (!startDate && !endDate) url += '&days=30';
        if (gran !== 'auto') url += `&granularity=${gran}`;
        if (region) url += `&region=${encodeURIComponent(region)}`;
        if (country) url += `&country=${encodeURIComponent(country)}`;
        if (ruralUrban) url += `&rural_urban=${encodeURIComponent(ruralUrban)}`;
        return url;
    }

    const allFuels = selectedFuelTypes(fuel).length !== 1;

    if (reconstructedHistoryEnabled) {
        await loadWeightedTrend('trend', fuel, buildUrl);
        return;
    }

    if (allFuels) {
        const { datasets, granularity, allData } = await fetchAllFuelTrends(buildUrl, false, fuel);
        const hourly = granularity === 'hourly';
        // Store first fuel's data for download fallback
        const firstKey = Object.keys(allData)[0];
        lastTrendData = firstKey ? allData[firstKey] : [];
        const hasData = datasets.length > 0;
        document.getElementById('trend-dl-csv').disabled = !hasData || _userRole === 'readonly';
        document.getElementById('trend-dl-json').disabled = !hasData || _userRole === 'readonly';
        document.getElementById('trend-dl-csv').title = '';
        document.getElementById('trend-dl-json').title = '';
        document.getElementById('trend-heading').textContent =
            (hourly ? 'Hourly average price' : 'Daily average price') + ' by fuel type';
        document.getElementById('trend-granularity-note').textContent = hourly
            ? 'Showing average per scrape window (data is fetched every 30 minutes).'
            : 'Showing daily averages.';

        if (charts['chart-trend']) charts['chart-trend'].destroy();
        const ctx = document.getElementById('chart-trend').getContext('2d');
        charts['chart-trend'] = new Chart(ctx, {
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
    lastTrendData = data;
    document.getElementById('trend-dl-csv').disabled = !data.length;
    document.getElementById('trend-dl-json').disabled = !data.length;
    document.getElementById('trend-dl-csv').title = '';
    document.getElementById('trend-dl-json').title = '';
    const hourly = resp.granularity === 'hourly';
    document.getElementById('trend-heading').textContent =
        hourly ? 'Hourly average price' : 'Daily average price';
    document.getElementById('trend-granularity-note').textContent = hourly
        ? 'Showing average per scrape window (data is fetched every 30 minutes).'
        : 'Showing daily averages.';

    const chartData = data.map(d => ({ x: new Date(d.bucket), y: d.avg_price }));

    if (charts['chart-trend']) charts['chart-trend'].destroy();
    const ctx = document.getElementById('chart-trend').getContext('2d');
    charts['chart-trend'] = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: 'Avg pence/litre',
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
                            return `${item.parsed.y}p · averaged from ${d.stations} stations`;
                        }
                    }
                }
            }
        }
    });
}
