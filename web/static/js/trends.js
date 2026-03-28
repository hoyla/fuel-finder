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
    let url = `/api/prices/history/export?fuel_type=${encodeURIComponent(fuel)}&format=${fmt}`;
    if (startDate) url += `&start_date=${startDate}`;
    if (endDate) url += `&end_date=${endDate}`;
    if (!startDate && !endDate) url += '&days=30';
    if (region) url += `&region=${encodeURIComponent(region)}`;
    if (country) url += `&country=${encodeURIComponent(country)}`;
    if (ruralUrban) url += `&rural_urban=${encodeURIComponent(ruralUrban)}`;
    // Add auth header via a fetch-and-download approach
    const btn = document.getElementById(fmt === 'csv' ? 'trend-dl-csv' : 'trend-dl-json');
    btn.disabled = true;
    btn.textContent = '⏳ Exporting…';
    fetch(url, { headers: authHeaders() })
        .then(resp => {
            if (!resp.ok) throw new Error('Export failed');
            return resp.blob();
        })
        .then(blob => {
            const parts = ['fuel-prices'];
            if (fuel) parts.push(fuel.replace(/\s+/g, '-'));
            if (region) region.split(',').forEach(v => parts.push(v.trim().replace(/\s+/g, '-')));
            if (country) country.split(',').forEach(v => parts.push(v.trim().replace(/\s+/g, '-')));
            if (ruralUrban) ruralUrban.split(',').forEach(v => parts.push(v.trim().replace(/\s+/g, '-')));
            if (startDate) parts.push('from-' + startDate);
            if (endDate) parts.push('to-' + endDate);
            const ts = new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').slice(0, 19);
            parts.push(ts);
            const ext = '.' + fmt;
            const maxLen = 251 - ext.length;          // APFS/HFS+ 255-byte limit
            let stem = parts.join('_');
            if (stem.length > maxLen) stem = stem.slice(0, maxLen - 1) + '\u2026';
            const filename = stem + ext;
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
        })
        .catch(err => alert('Export failed: ' + err.message))
        .finally(() => {
            btn.disabled = false;
            btn.textContent = `⬇ ${fmt.toUpperCase()}`;
        });
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
    let url = `/prices/history?fuel_type=${fuel}`;
    if (startDate) url += `&start_date=${startDate}`;
    if (endDate) url += `&end_date=${endDate}`;
    if (!startDate && !endDate) url += '&days=30';
    if (gran !== 'auto') url += `&granularity=${gran}`;
    if (region) url += `&region=${encodeURIComponent(region)}`;
    if (country) url += `&country=${encodeURIComponent(country)}`;
    if (ruralUrban) url += `&rural_urban=${encodeURIComponent(ruralUrban)}`;
    const resp = await apiFetch(url);
    const data = resp.data;
    lastTrendData = data;
    document.getElementById('trend-dl-csv').disabled = !data.length;
    document.getElementById('trend-dl-json').disabled = !data.length;
    const hourly = resp.granularity === 'hourly';
    document.getElementById('trend-heading').textContent =
        hourly ? 'Hourly average price' : 'Daily average price';
    document.getElementById('trend-granularity-note').textContent = hourly
        ? 'Showing average per scrape window (data is fetched every 30 minutes).'
        : 'Showing daily averages.';

    // Format labels: show time for hourly, date-only for daily
    const labels = data.map(d => {
        const dt = new Date(d.bucket);
        if (hourly) {
            return dt.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
                 + ' ' + dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        }
        return dt.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
    });

    if (charts['chart-trend']) charts['chart-trend'].destroy();
    const ctx = document.getElementById('chart-trend').getContext('2d');
    charts['chart-trend'] = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Avg pence/litre',
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
                        label: (item) => `${item.parsed.y}p · averaged from ${data[item.dataIndex].stations} stations`
                    }
                }
            }
        }
    });
}
