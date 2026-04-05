// ---------------------------------------------------------------------------
// Map
// ---------------------------------------------------------------------------
let map, mapLayer, mapLegend;
let lastMapData = [];

function downloadMapData(fmt) { downloadFile(lastMapData, 'fuel-map-data', fmt); }

function initMap() {
    map = L.map('map').setView([53.5, -1.5], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 18,
    }).addTo(map);

    loadMapPrices();
    document.getElementById('map-fuel').addEventListener('change', loadMapPrices);
}

async function loadMapPrices() {
    const fuel = document.getElementById('map-fuel').value;
    const region = document.getElementById('map-region').value;
    const brand = document.getElementById('map-brand').value;
    const category = document.getElementById('map-category').value;
    const excludeOutliers = document.getElementById('map-exclude-outliers').checked;

    let url = `/prices/map?fuel_type=${encodeURIComponent(fuel)}`;
    if (region) url += `&region=${encodeURIComponent(region)}`;
    if (brand) url += `&brand=${encodeURIComponent(brand)}`;
    if (category) url += `&category=${encodeURIComponent(category)}`;
    if (excludeOutliers) url += '&exclude_outliers=true';

    const loading = document.getElementById('map-loading');
    loading.style.display = 'block';

    const data = await apiFetch(url);

    loading.style.display = 'none';

    lastMapData = data;
    document.getElementById('map-dl-csv').disabled = !data.length;
    document.getElementById('map-dl-json').disabled = !data.length;

    if (mapLayer) map.removeLayer(mapLayer);
    if (mapLegend) map.removeControl(mapLegend);

    if (!data.length) return;

    // Safe min/max (no stack overflow with spread on large arrays)
    let minP = Infinity, maxP = -Infinity;
    for (const d of data) {
        if (d.price < minP) minP = d.price;
        if (d.price > maxP) maxP = d.price;
    }

    function priceColour(p) {
        const t = (p - minP) / (maxP - minP || 1);
        const r = Math.round(0 + t * 212);
        const g = Math.round(112 - t * 59);
        const b = Math.round(60 - t * 32);
        return `rgb(${r},${g},${b})`;
    }

    const cluster = L.markerClusterGroup({
        maxClusterRadius: 40,
        disableClusteringAtZoom: 12,
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false,
        iconCreateFunction: function (cl) {
            const children = cl.getAllChildMarkers();
            let sum = 0;
            for (const m of children) sum += m.options.price;
            const avg = sum / children.length;
            const bg = priceColour(avg);
            const count = children.length;
            const size = count < 10 ? 36 : count < 100 ? 44 : 52;
            return L.divIcon({
                html: `<div style="background:${bg};width:${size}px;height:${size}px;" class="price-cluster"><span>${count}</span></div>`,
                className: 'price-cluster-icon',
                iconSize: L.point(size, size),
            });
        },
    });

    for (const d of data) {
        const marker = L.circleMarker([d.latitude, d.longitude], {
            radius: 5, fillColor: priceColour(d.price), color: '#333',
            weight: 0.5, fillOpacity: 0.85, price: d.price,
        }).bindPopup(`
            <strong><a href="#" class="station-link" data-node="${escHtml(d.node_id)}" data-name="${escHtml(d.trading_name)}" data-brand="${escHtml(d.brand_name || '')}" data-raw-brand="${escHtml(d.raw_brand_name || '')}" data-city="${escHtml(d.city || '')}" data-postcode="${escHtml(d.postcode || '')}" data-category="${escHtml(d.forecourt_type || '')}" data-lat="${d.latitude || ''}" data-lon="${d.longitude || ''}" data-motorway="${d.is_motorway_service_station || ''}" data-supermarket="${d.is_supermarket_service_station || ''}" data-region="" data-district="${escHtml(d.admin_district || '')}" style="color:var(--accent);text-decoration:none;">${escHtml(d.trading_name)}</a></strong><br>
            ${escHtml(d.brand_name) || ''} · ${escHtml(d.forecourt_type) || 'Uncategorised'}<br>
            ${escHtml(d.city)} ${escHtml(d.postcode)}<br>
            ${d.admin_district ? escHtml(d.admin_district) + '<br>' : ''}
            ${d.rural_urban ? '<em>' + escHtml(d.rural_urban) + '</em><br>' : ''}
            <strong>${ppl(d.price)}</strong> ${escHtml(d.fuel_name) || fuel}<br>
            <small style="color:var(--muted)">Updated: ${d.observed_at ? new Date(d.observed_at).toLocaleString() : '—'}</small>
        `);
        cluster.addLayer(marker);
    }
    mapLayer = cluster;
    map.addLayer(cluster);

    // Legend
    mapLegend = L.control({ position: 'bottomright' });
    mapLegend.onAdd = function () {
        const div = L.DomUtil.create('div', 'map-legend');
        div.innerHTML = `
            <strong>${data.length.toLocaleString()} stations</strong>
            <div class="gradient"></div>
            <div class="labels"><span>${ppl(minP)}</span><span>${ppl(maxP)}</span></div>
        `;
        return div;
    };
    mapLegend.addTo(map);
}
