// ---------------------------------------------------------------------------
// Trends
// ---------------------------------------------------------------------------
let lastTrendData = [];

function downloadTrendData(fmt) {
    const fuel = document.getElementById('trend-fuel').value;
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

function setTrendRange(value) {
    const customFields = value === 'custom';
    document.getElementById('trend-start-ctl').style.display = customFields ? '' : 'none';
    document.getElementById('trend-end-ctl').style.display = customFields ? '' : 'none';
    if (!customFields) {
        const end = new Date();
        document.getElementById('trend-end').value = end.toISOString().slice(0, 10);
        if (value === 'all') {
            document.getElementById('trend-start').value = '';
        } else {
            const start = new Date();
            start.setDate(start.getDate() - parseInt(value));
            document.getElementById('trend-start').value = start.toISOString().slice(0, 10);
        }
        loadTrends();
    }
}
// Set initial dates for default 30-day range
setTrendRange('30');

async function loadTrends() {
    const fuel = document.getElementById('trend-fuel').value;
    const region = getMultiSelectValues('trend-region-ms');
    const country = getMultiSelectValues('trend-country-ms');
    const ruralUrban = getMultiSelectValues('trend-rural-urban-ms');
    const startDate = document.getElementById('trend-start').value;
    const endDate = document.getElementById('trend-end').value;
    const gran = document.getElementById('trend-granularity').value;

    function buildUrl(fuelCode) {
        let url = `/prices/history?fuel_type=${fuelCode}`;
        if (startDate) url += `&start_date=${startDate}`;
        if (endDate) url += `&end_date=${endDate}`;
        if (!startDate && !endDate) url += '&days=30';
        if (gran !== 'auto') url += `&granularity=${gran}`;
        if (region) url += `&region=${encodeURIComponent(region)}`;
        if (country) url += `&country=${encodeURIComponent(country)}`;
        if (ruralUrban) url += `&rural_urban=${encodeURIComponent(ruralUrban)}`;
        return url;
    }

    const allFuels = !fuel;

    if (allFuels) {
        const { datasets, granularity, allData } = await fetchAllFuelTrends(buildUrl);
        const hourly = granularity === 'hourly';
        // Store first fuel's data for download fallback
        const firstKey = Object.keys(allData)[0];
        lastTrendData = firstKey ? allData[firstKey] : [];
        const hasData = datasets.length > 0;
        document.getElementById('trend-dl-csv').disabled = true;
        document.getElementById('trend-dl-json').disabled = true;
        document.getElementById('trend-dl-csv').title = 'Select a single fuel type to export raw data';
        document.getElementById('trend-dl-json').title = 'Select a single fuel type to export raw data';
        document.getElementById('trend-heading').textContent =
            hourly ? 'Hourly average price — all fuel types' : 'Daily average price — all fuel types';
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
