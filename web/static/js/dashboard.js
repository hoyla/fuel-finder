// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
let charts = {};

function renderBarChart(canvasId, labels, values, label, colour) {
    if (charts[canvasId]) charts[canvasId].destroy();
    const ctx = document.getElementById(canvasId).getContext('2d');
    charts[canvasId] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{ label, data: values, backgroundColor: colour + '99', borderColor: colour, borderWidth: 1 }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: false } }
        }
    });
}

// Helper to add click-through on a Chart.js bar chart
function addChartClickHandler(chartId, dataArray, mapFn) {
    const chart = charts[chartId];
    if (!chart) return;
    chart.options.onClick = (evt, elements) => {
        if (!elements.length) return;
        const idx = elements[0].index;
        const item = dataArray[idx];
        if (item) navigateToSearch(mapFn(item));
    };
    // Show pointer cursor on hover over bars
    const canvas = document.getElementById(chartId);
    canvas.style.cursor = 'default';
    canvas.addEventListener('mousemove', (evt) => {
        const points = chart.getElementsAtEventForMode(evt, 'nearest', { intersect: true }, false);
        canvas.style.cursor = points.length ? 'pointer' : 'default';
    });
}

async function loadDashboard() {
    const data = await apiFetch('/summary');

    if (data.last_scrape) {
        const d = new Date(data.last_scrape);
        document.getElementById('last-scrape').textContent =
            'Last fetch: ' + d.toLocaleString();
    }

    const cards = document.getElementById('summary-cards');
    const countryLines = (data.by_country || [])
        .map(c => `<div class="sub"><a href="#" class="country-link" data-country="${c.country_name}" style="color:var(--accent);text-decoration:underline;cursor:pointer;">${c.country_name}: ${c.station_count.toLocaleString()}</a></div>`)
        .join('');
    cards.innerHTML = `
        <div class="card">
            <div class="label">Stations</div>
            <div class="value">${data.total_stations.toLocaleString()}</div>
            ${countryLines}
        </div>
    `;
    data.by_fuel_type.forEach(ft => {
        const outlierNote = ft.outliers_excluded > 0
            ? `<div class="sub"><a href="#" class="outlier-link" data-fuel="${ft.fuel_type}" style="color:var(--accent);text-decoration:underline;cursor:pointer;">${ft.outliers_excluded} outlier${ft.outliers_excluded === 1 ? '' : 's'} excluded</a></div>`
            : '';
        cards.innerHTML += `
            <div class="card">
                <div class="label">${ft.fuel_name || ft.fuel_type} average price</div>
                <div class="value">${ppl(ft.avg_price)}</div>
                <div class="sub">${ppl(ft.min_price)} – ${ppl(ft.max_price)}</div>
                <div class="sub">${ft.station_count} stations</div>
                ${outlierNote}
            </div>
        `;
    });

    const summaryNowByFuel = Object.fromEntries(
        (data.by_fuel_type || []).map(ft => [ft.fuel_type, Number(ft.avg_price)])
    );

    const [e10Past, b7Past] = await Promise.all([
        fetchThirtyDaysAgoBaseline('E10'),
        fetchThirtyDaysAgoBaseline('B7_STANDARD'),
    ]);
    cards.innerHTML += renderThirtyDayChangeCard('E10', 'Unleaded (E10)<br>30-day change', summaryNowByFuel.E10, e10Past);
    cards.innerHTML += renderThirtyDayChangeCard('B7_STANDARD', 'Diesel (B7)<br>30-day change', summaryNowByFuel.B7_STANDARD, b7Past);

    // Wire up outlier links to jump to the anomalies → outliers sub-section
    document.querySelectorAll('.outlier-link').forEach(link => {
        link.addEventListener('click', async e => {
            e.preventDefault();
            const fuel = link.dataset.fuel;
            // Switch to anomalies tab
            switchTab('anomalies');
            // Switch to outliers sub-section
            document.getElementById('anomaly-section').value = 'outliers';
            await switchAnomalySection(true);
            // Set fuel type and load
            const sel = document.getElementById('outlier-fuel');
            if (sel.querySelector(`option[value="${fuel}"]`)) sel.value = fuel;
            loadOutliers();
        });
    });

    // Wire up country links to search by country
    document.querySelectorAll('.country-link').forEach(link => {
        link.addEventListener('click', e => {
            e.preventDefault();
            const ft = document.getElementById('dashboard-fuel').value || 'E10';
            navigateToSearch({ fuel_type: ft, country: link.dataset.country });
        });
    });

    await loadDashboardCharts();
}

function renderThirtyDayChangeCard(fuelCode, title, priceNow, price30DaysAgo) {
    if (priceNow == null || price30DaysAgo == null || !isFinite(priceNow) || !isFinite(price30DaysAgo) || price30DaysAgo <= 0) {
        return `
            <div class="card">
                <div class="label">${title}</div>
                <div class="value">—</div>
                <div class="sub">30 days ago: —</div>
                <div class="sub">Now: —</div>
            </div>
        `;
    }

    const pctChange = ((priceNow - price30DaysAgo) / price30DaysAgo) * 100;

    let trendClass = 'flat';
    let arrow = '→';
    if (pctChange > 0) {
        trendClass = 'up';
        arrow = '↑';
    } else if (pctChange < 0) {
        trendClass = 'down';
        arrow = '↓';
    }

    const pct = Math.abs(pctChange).toFixed(1) + '%';
    return `
        <div class="card" data-fuel-change="${fuelCode}">
            <div class="label">${title}</div>
            <div class="value change-value ${trendClass}"><span class="change-arrow">${arrow}</span><span>${pct}</span></div>
            <div class="sub">30 days ago: ${ppl(price30DaysAgo)}</div>
            <div class="sub">Now: ${ppl(priceNow)}</div>
        </div>
    `;
}

async function fetchThirtyDaysAgoBaseline(fuelCode) {
    try {
        const target = new Date();
        target.setDate(target.getDate() - 30);

        const rangeStart = new Date(target);
        rangeStart.setDate(rangeStart.getDate() - 3);
        const rangeEnd = new Date(target);
        rangeEnd.setDate(rangeEnd.getDate() + 3);

        const fmt = d => d.toISOString().slice(0, 10);
        const resp = await apiFetch(
            `/prices/history?fuel_type=${encodeURIComponent(fuelCode)}&start_date=${fmt(rangeStart)}&end_date=${fmt(rangeEnd)}&granularity=daily`
        );

        const points = (resp.data || []).filter(d => d.avg_price != null);
        if (!points.length) return null;

        points.sort((a, b) => {
            const da = Math.abs(new Date(a.bucket).getTime() - target.getTime());
            const db = Math.abs(new Date(b.bucket).getTime() - target.getTime());
            return da - db;
        });

        const baseline = Number(points[0].avg_price);
        return isFinite(baseline) ? baseline : null;
    } catch {
        return null;
    }
}

async function loadDashboardCharts() {
    const fuelCode = document.getElementById('dashboard-fuel').value || 'E10';
    const fuelSel = document.getElementById('dashboard-fuel');
    const fuelName = fuelSel.options[fuelSel.selectedIndex]?.textContent || fuelCode;

    // Update chart headings
    document.getElementById('heading-region').textContent = `Average price by region \u2013 ${fuelName}`;
    document.getElementById('heading-category').textContent = `Average price by forecourt type \u2013 ${fuelName}`;
    document.getElementById('heading-rural-urban').textContent = `Average price by rural/urban classification \u2013 ${fuelName}`;
    document.getElementById('heading-brand').textContent = `Cheapest brands \u2013 ${fuelName}`;
    document.getElementById('heading-district-expensive').textContent = `Most expensive local authorities \u2013 ${fuelName}`;
    document.getElementById('heading-district-cheap').textContent = `Cheapest local authorities \u2013 ${fuelName}`;

    // Region chart
    const regionData = await apiFetch(`/prices/by-region?fuel_type=${encodeURIComponent(fuelCode)}`);
    renderBarChart('chart-region', regionData.map(r => r.region), regionData.map(r => r.avg_price),
        'Avg pence/litre', '#1d70b8');

    // Category chart
    const catData = await apiFetch(`/prices/by-category?fuel_type=${encodeURIComponent(fuelCode)}`);
    const catColours = catData.map(c => {
        const m = { 'Supermarket':'#00703c', 'Major Oil':'#1d70b8', 'Motorway':'#d4351c',
                    'Motorway Operator':'#f47738', 'Fuel Group':'#505a5f',
                    'Convenience':'#b58840', 'Independent':'#5694ca' };
        return m[c.forecourt_type] || '#999';
    });
    if (charts['chart-category']) charts['chart-category'].destroy();
    const catCtx = document.getElementById('chart-category').getContext('2d');
    charts['chart-category'] = new Chart(catCtx, {
        type: 'bar',
        data: {
            labels: catData.map(c => `${c.forecourt_type} (${c.station_count})`),
            datasets: [{
                label: 'Avg pence/litre',
                data: catData.map(c => c.avg_price),
                backgroundColor: catColours.map(c => c + '99'),
                borderColor: catColours,
                borderWidth: 1,
            }]
        },
        options: {
            indexAxis: 'y', responsive: true,
            plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: false } }
        }
    });

    // Brand chart
    const brandData = await apiFetch(`/prices/by-brand?fuel_type=${encodeURIComponent(fuelCode)}&limit=15`);
    renderBarChart('chart-brand', brandData.map(b => b.brand_name), brandData.map(b => b.avg_price),
        'Avg pence/litre', '#00703c');

    // Rural/Urban chart
    const ruData = await apiFetch(`/prices/by-rural-urban?fuel_type=${encodeURIComponent(fuelCode)}`);
    const ruColours = ruData.map(r => {
        if (!r.unified_label) return '#999';
        const lc = r.unified_label.toLowerCase();
        if (lc.includes('urban')) return '#1d70b8';
        if (lc.includes('remote')) return '#d4351c';
        return '#00703c';
    });
    if (charts['chart-rural-urban']) charts['chart-rural-urban'].destroy();
    const ruCtx = document.getElementById('chart-rural-urban').getContext('2d');
    charts['chart-rural-urban'] = new Chart(ruCtx, {
        type: 'bar',
        data: {
            labels: ruData.map(r => `${r.unified_label || 'Unknown'} (${r.station_count})`),
            datasets: [{
                label: 'Avg pence/litre',
                data: ruData.map(r => r.avg_price),
                backgroundColor: ruColours.map(c => c + '99'),
                borderColor: ruColours,
                borderWidth: 1,
            }]
        },
        options: {
            indexAxis: 'y', responsive: true,
            plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: false } }
        }
    });

    // District charts (most/least expensive)
    const distExpensive = await apiFetch(`/prices/by-district?fuel_type=${encodeURIComponent(fuelCode)}&limit=15`);
    renderBarChart('chart-district-expensive',
        distExpensive.map(d => `${d.admin_district} (${d.station_count})`),
        distExpensive.map(d => d.avg_price), 'Avg pence/litre', '#d4351c');
    const distCheap = await apiFetch(`/prices/by-district?fuel_type=${encodeURIComponent(fuelCode)}&limit=500`);
    const cheapest15 = distCheap.slice(-15).reverse();
    renderBarChart('chart-district-cheap',
        cheapest15.map(d => `${d.admin_district} (${d.station_count})`),
        cheapest15.map(d => d.avg_price), 'Avg pence/litre', '#00703c');

    // Wire up chart click-throughs to Search
    addChartClickHandler('chart-region', regionData,
        r => ({ fuel_type: fuelCode, region: r.region, exclude_outliers: true }));

    addChartClickHandler('chart-category', catData,
        c => ({ fuel_type: fuelCode, category: c.forecourt_type, exclude_outliers: true }));

    addChartClickHandler('chart-brand', brandData,
        b => ({ fuel_type: fuelCode, brand: b.brand_name, exclude_outliers: true }));

    addChartClickHandler('chart-rural-urban', ruData,
        r => ({ fuel_type: fuelCode, rural_urban: r.rural_urban_values[0], exclude_outliers: true }));

    addChartClickHandler('chart-district-expensive', distExpensive,
        d => ({ fuel_type: fuelCode, district: d.admin_district, exclude_outliers: true }));

    addChartClickHandler('chart-district-cheap', cheapest15,
        d => ({ fuel_type: fuelCode, district: d.admin_district, exclude_outliers: true }));
}
