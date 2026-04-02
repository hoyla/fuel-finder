// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
let charts = {};

const FORECOURT_COLOURS = {
    'Supermarket': '#00703c', 'Major Oil': '#1d70b8', 'Motorway': '#d4351c',
    'Motorway Operator': '#f47738', 'Fuel Group': '#6f72af',
    'Convenience': '#b58840', 'Independent': '#5694ca', 'Uncategorised': '#505a5f'
};

function renderBarChart(canvasId, labels, values, label, colour) {
    if (charts[canvasId]) charts[canvasId].destroy();
    const ctx = document.getElementById(canvasId).getContext('2d');
    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    const padding = (maxVal - minVal) * 0.5 || 1;
    const xMin = Math.floor(minVal - padding);
    charts[canvasId] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{ label, data: values, backgroundColor: colour + '99', borderColor: colour, borderWidth: 1 }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            aspectRatio: 1.6,
            plugins: { legend: { display: false } },
            scales: { x: { min: xMin } }
        }
    });
}

function renderBrandChart(canvasId, data) {
    if (charts[canvasId]) charts[canvasId].destroy();
    const ctx = document.getElementById(canvasId).getContext('2d');
    const labels = data.map(b => `${b.brand_name} [${b.forecourt_type}]`);
    const values = data.map(b => b.avg_price);
    const colours = data.map(b => FORECOURT_COLOURS[b.forecourt_type] || '#999');
    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    const padding = (maxVal - minVal) * 0.5 || 1;
    charts[canvasId] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Avg pence/litre',
                data: values,
                backgroundColor: colours.map(c => c + '99'),
                borderColor: colours,
                borderWidth: 1,
            }]
        },
        options: {
            indexAxis: 'y', responsive: true,
            aspectRatio: 1.6,
            plugins: { legend: { display: false } },
            scales: { x: { min: Math.floor(minVal - padding) } }
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
    const reportsToday = data.reports_today || 0;
    const reportsTodaySub = `<div class="sub">${reportsToday.toLocaleString()} station updates today</div>`;
    cards.innerHTML = `
        <div class="card">
            <div class="label">Stations</div>
            <div class="value">${data.total_stations.toLocaleString()}</div>
            ${countryLines}
        </div>
        <div class="card">
            <div class="label">Reports</div>
            <div class="value">${(data.total_reports || 0).toLocaleString()}</div>
            <div class="sub">total price reports</div>
            ${reportsTodaySub}
        </div>
    `;
    data.by_fuel_type.forEach(ft => {
        const outlierNote = ft.outliers_excluded > 0
            ? `<div class="sub"><a href="#" class="outlier-link" data-fuel="${ft.fuel_type}" style="color:var(--accent);text-decoration:underline;cursor:pointer;">${ft.outliers_excluded} outlier${ft.outliers_excluded === 1 ? '' : 's'} excluded</a></div>`
            : '';
        cards.innerHTML += `
            <div class="card" data-fuel-price="${ft.fuel_type}">
                <div class="label">${ft.fuel_name || ft.fuel_type} average price</div>
                <div class="value">${ppl(ft.avg_price)}</div>
                <canvas class="sparkline" data-fuel="${ft.fuel_type}" width="160" height="32"></canvas>
                <div class="sub">${ppl(ft.min_price)} – ${ppl(ft.max_price)}</div>
                <div class="sub">${ft.station_count.toLocaleString()} stations</div>
                ${outlierNote}
            </div>
        `;
    });

    const summaryNowByFuel = Object.fromEntries(
        (data.by_fuel_type || []).map(ft => [ft.fuel_type, Number(ft.avg_price)])
    );

    const [e10Baseline, b7Baseline] = await Promise.all([
        fetchThirtyDaysAgoBaseline('E10'),
        fetchThirtyDaysAgoBaseline('B7_STANDARD'),
    ]);
    const e10Past = e10Baseline?.price ?? null;
    const e10Date = e10Baseline?.date ?? null;
    const b7Past = b7Baseline?.price ?? null;
    const b7Date = b7Baseline?.date ?? null;
    cards.innerHTML += renderThirtyDayChangeCard('E10', 'Unleaded (E10)<br>30-day change', summaryNowByFuel.E10, e10Past, e10Date);
    cards.innerHTML += renderThirtyDayChangeCard('B7_STANDARD', 'Diesel (B7)<br>30-day change', summaryNowByFuel.B7_STANDARD, b7Past, b7Date);

    // Wire up 30-day change cards to navigate to Trends tab
    document.querySelectorAll('[data-fuel-change]').forEach(card => {
        card.style.cursor = 'pointer';
        card.addEventListener('click', e => {
            if (e.target.closest('a')) return;
            const fuel = card.dataset.fuelChange;
            const sel = document.getElementById('trend-fuel');
            if (sel.querySelector(`option[value="${fuel}"]`)) sel.value = fuel;
            switchTab('trends');
            loadTrends();
        });
    });

    // Load sparklines for fuel price cards
    loadSparklines(data.by_fuel_type.map(ft => ft.fuel_type));

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

function renderThirtyDayChangeCard(fuelCode, title, priceNow, price30DaysAgo, baselineDate) {
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

    let dateLabel = '30 days ago';
    if (baselineDate) {
        const d = new Date(baselineDate + 'T00:00:00');
        dateLabel = d.toLocaleDateString('en-GB', { month: 'long', day: 'numeric' });
    }

    const pct = Math.abs(pctChange).toFixed(1) + '%';
    return `
        <div class="card" data-fuel-change="${fuelCode}">
            <div class="label">${title}</div>
            <div class="value change-value ${trendClass}"><span class="change-arrow">${arrow}</span><span>${pct}</span></div>
            <div class="sub">${dateLabel}: ${ppl(price30DaysAgo)}</div>
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
        const bucketDate = points[0].bucket;
        return isFinite(baseline) ? { price: baseline, date: bucketDate } : null;
    } catch {
        return null;
    }
}

async function loadSparklines(fuelCodes) {
    const fmt = d => d.toISOString().slice(0, 10);
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 30);
    const promises = fuelCodes.map(async code => {
        try {
            const resp = await apiFetch(
                `/prices/history?fuel_type=${encodeURIComponent(code)}&start_date=${fmt(start)}&end_date=${fmt(end)}&granularity=daily`
            );
            const points = (resp.data || []).filter(d => d.avg_price != null).map(d => d.avg_price);
            const canvas = document.querySelector(`.sparkline[data-fuel="${code}"]`);
            if (!canvas || points.length < 2) return;
            const ctx = canvas.getContext('2d');
            const w = canvas.width, h = canvas.height;
            const min = Math.min(...points), max = Math.max(...points);
            const range = max - min || 1;
            ctx.clearRect(0, 0, w, h);
            ctx.beginPath();
            points.forEach((v, i) => {
                const x = (i / (points.length - 1)) * w;
                const y = h - ((v - min) / range) * (h - 4) - 2;
                i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            });
            const trending = points[points.length - 1] > points[0];
            ctx.strokeStyle = trending ? 'var(--red, #d4351c)' : 'var(--green, #00703c)';
            ctx.lineWidth = 1.5;
            ctx.stroke();
        } catch { /* ignore */ }
    });
    await Promise.all(promises);
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
    document.getElementById('heading-brand-expensive').textContent = `Most expensive brands \u2013 ${fuelName}`;
    const trendHeading = document.getElementById('heading-trend');
    trendHeading.textContent = `Price trend \u2013 ${fuelName}`;
    trendHeading.title = 'Daily averages with Hampel filter smoothing. May differ slightly from the current snapshot shown in cards above.';
    document.getElementById('heading-district-expensive').textContent = `Most expensive local authorities \u2013 ${fuelName}`;
    document.getElementById('heading-district-cheap').textContent = `Cheapest local authorities \u2013 ${fuelName}`;

    // Region chart
    const regionData = await apiFetch(`/prices/by-region?fuel_type=${encodeURIComponent(fuelCode)}`);
    renderBarChart('chart-region', regionData.map(r => r.region), regionData.map(r => r.avg_price),
        'Avg pence/litre', '#1d70b8');

    // Category chart
    const catData = await apiFetch(`/prices/by-category?fuel_type=${encodeURIComponent(fuelCode)}`);
    const catColours = catData.map(c => FORECOURT_COLOURS[c.forecourt_type] || '#999');
    if (charts['chart-category']) charts['chart-category'].destroy();
    const catCtx = document.getElementById('chart-category').getContext('2d');
    const catValues = catData.map(c => c.avg_price);
    const catPad = (Math.max(...catValues) - Math.min(...catValues)) * 0.5 || 1;
    charts['chart-category'] = new Chart(catCtx, {
        type: 'bar',
        data: {
            labels: catData.map(c => `${c.forecourt_type} (${c.station_count})`),
            datasets: [{
                label: 'Avg pence/litre',
                data: catValues,
                backgroundColor: catColours.map(c => c + '99'),
                borderColor: catColours,
                borderWidth: 1,
            }]
        },
        options: {
            indexAxis: 'y', responsive: true,
            aspectRatio: 1.8,
            plugins: { legend: { display: false } },
            scales: { x: { min: Math.floor(Math.min(...catValues) - catPad) } }
        }
    });

    // Brand chart (cheapest)
    const brandData = await apiFetch(`/prices/by-brand?fuel_type=${encodeURIComponent(fuelCode)}&limit=15`);
    renderBrandChart('chart-brand', brandData);

    // Brand chart (most expensive)
    const brandExpData = await apiFetch(`/prices/by-brand?fuel_type=${encodeURIComponent(fuelCode)}&limit=15&order=desc`);
    renderBrandChart('chart-brand-expensive', brandExpData);

    // Price trend chart (all data, daily)
    const trendResp = await apiFetch(`/prices/history?fuel_type=${encodeURIComponent(fuelCode)}&days=365&granularity=daily`);
    const trendData = (trendResp.data || []).map(d => ({ x: new Date(d.bucket), y: d.avg_price }));
    if (charts['chart-dashboard-trend']) charts['chart-dashboard-trend'].destroy();
    const trendCtx = document.getElementById('chart-dashboard-trend').getContext('2d');
    charts['chart-dashboard-trend'] = new Chart(trendCtx, {
        type: 'line',
        data: {
            datasets: [{
                label: 'Avg pence/litre',
                data: trendData,
                borderColor: '#1d70b8',
                backgroundColor: '#1d70b833',
                fill: true, tension: 0.3,
                pointRadius: 1.5,
                pointHoverRadius: 4,
                pointBackgroundColor: '#1d70b8',
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            aspectRatio: 1.8,
            scales: {
                x: {
                    type: 'time',
                    time: { unit: 'day', tooltipFormat: 'd MMM yyyy', displayFormats: { day: 'd MMM' } },
                    ticks: { maxRotation: 45 }
                },
                y: { beginAtZero: false }
            },
            plugins: { legend: { display: false } }
        }
    });

    // Rural/Urban chart
    const ruData = await apiFetch(`/prices/by-rural-urban?fuel_type=${encodeURIComponent(fuelCode)}`);
    const ruColourMap = {
        'Urban (Eng & Wales)': '#d4351c',
        'Urban (Scot)':        '#e8756a',
        'Small towns (Scot)':  '#1d70b8',
        'Rural (Eng & Wales)': '#00703c',
        'Rural (Scot)':        '#4da67a',
    };
    const ruTooltipMap = {
        'Urban (Eng & Wales)': 'ONS RUC: Urban nearer to & further from a major town or city',
        'Urban (Scot)':        'SG: Large Urban Areas & Other Urban Areas',
        'Small towns (Scot)':  'SG: Accessible Small Towns & Remote Small Towns',
        'Rural (Eng & Wales)': 'ONS RUC: Smaller rural & Larger rural, nearer to & further from a major town or city',
        'Rural (Scot)':        'SG: Accessible Rural & Remote Rural',
    };
    const ruColours = ruData.map(r => ruColourMap[r.unified_label] || '#999');
    if (charts['chart-rural-urban']) charts['chart-rural-urban'].destroy();
    const ruCtx = document.getElementById('chart-rural-urban').getContext('2d');
    const ruValues = ruData.map(r => r.avg_price);
    const ruPad = (Math.max(...ruValues) - Math.min(...ruValues)) * 0.5 || 1;
    charts['chart-rural-urban'] = new Chart(ruCtx, {
        type: 'bar',
        data: {
            labels: ruData.map(r => `${r.unified_label || 'Unknown'} (${r.station_count})`),
            datasets: [{
                label: 'Avg pence/litre',
                data: ruValues,
                backgroundColor: ruColours.map(c => c + '99'),
                borderColor: ruColours,
                borderWidth: 1,
            }]
        },
        options: {
            indexAxis: 'y', responsive: true,
            aspectRatio: 1.8,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        afterLabel: (ctx) => {
                            const label = ruData[ctx.dataIndex]?.unified_label;
                            return ruTooltipMap[label] || '';
                        }
                    }
                }
            },
            scales: { x: { min: Math.floor(Math.min(...ruValues) - ruPad) } }
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

    addChartClickHandler('chart-brand-expensive', brandExpData,
        b => ({ fuel_type: fuelCode, brand: b.brand_name, exclude_outliers: true }));

    addChartClickHandler('chart-rural-urban', ruData,
        r => ({ fuel_type: fuelCode, rural_urban: r.rural_urban_values.join(','), exclude_outliers: true }));

    addChartClickHandler('chart-district-expensive', distExpensive,
        d => ({ fuel_type: fuelCode, district: d.admin_district, exclude_outliers: true }));

    addChartClickHandler('chart-district-cheap', cheapest15,
        d => ({ fuel_type: fuelCode, district: d.admin_district, exclude_outliers: true }));
}
