// ---------------------------------------------------------------------------
// Anomalies
// ---------------------------------------------------------------------------
async function loadAnomalies(offset = 0) {
    const data = await apiFetch(`/anomalies?limit=50&offset=${offset}`);
    const body = document.getElementById('anomaly-body');
    if (!data.rows.length) {
        body.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:2rem;color:var(--muted)">No anomalies detected</td></tr>';
        document.getElementById('anomaly-pagination').innerHTML = '';
        return;
    }
    const fmtDate = ts => ts ? new Date(ts).toLocaleDateString() : '—';
    body.innerHTML = data.rows.map(r => {
        let changeTxt = '—';
        let changeStyle = '';
        if (r.prev_price != null && r.prev_price > 0) {
            const pct = ((r.price - r.prev_price) / r.prev_price * 100).toFixed(1);
            const sign = pct > 0 ? '+' : '';
            changeStyle = pct > 0 ? 'color:#d4351c' : 'color:#00703c';
            changeTxt = `${sign}${pct}%`;
        }
        return `<tr>
            <td><a href="#" class="station-link" data-node="${escHtml(r.node_id)}" data-name="${escHtml(r.trading_name)}" data-brand="${escHtml(r.brand_name || '')}" data-city="${escHtml(r.city || '')}" data-postcode="${escHtml(r.postcode || '')}" style="color:var(--accent);text-decoration:none;">${escHtml(r.trading_name)}</a></td>
            <td>${r.city || '—'}</td>
            <td>${r.fuel_type}</td>
            <td>${r.prev_price != null ? ppl(r.prev_price) : '—'}</td>
            <td>${fmtDate(r.prev_observed_at)}</td>
            <td><strong>${ppl(r.price)}</strong></td>
            <td>${fmtDate(r.observed_at)}</td>
            <td style="${changeStyle}"><strong>${changeTxt}</strong></td>
            <td>${(r.anomaly_flags || []).map(f => `<span class="tag">${f}</span>`).join(' ')}</td>
            ${canEdit() ? `<td><a href="#" class="edit-prices-link" data-node="${escHtml(r.node_id)}" data-name="${escHtml(r.trading_name)}" style="color:var(--accent);font-size:0.8rem;white-space:nowrap;">Edit prices</a></td>` : '<td></td>'}
        </tr>`;
    }).join('');
    renderLogPagination('anomaly-pagination', data.total, offset, 50, loadAnomalies);
}

function switchAnomalySection(skipLoad) {
    const active = document.getElementById('anomaly-section').value;
    document.getElementById('anomaly-flagged').style.display = active === 'flagged' ? '' : 'none';
    document.getElementById('anomaly-outliers').style.display = active === 'outliers' ? '' : 'none';
    if (active === 'outliers' && !document.getElementById('outlier-fuel').options.length) {
        return initOutlierFuelSelect(skipLoad);
    }
}

async function initOutlierFuelSelect(skipLoad) {
    const fuelTypes = await apiFetch('/fuel-types');
    const sel = document.getElementById('outlier-fuel');
    sel.innerHTML = '<option value="">All fuel types</option>';
    fuelTypes.forEach(ft => {
        const o = document.createElement('option');
        o.value = ft.fuel_type_code;
        o.textContent = ft.fuel_name;
        sel.appendChild(o);
    });
    if (!skipLoad) loadOutliers();
}

async function loadOutliers(offset = 0) {
    const fuel = document.getElementById('outlier-fuel').value;
    let url = `/outliers?limit=50&offset=${offset}`;
    if (fuel) url += `&fuel_type=${encodeURIComponent(fuel)}`;
    const data = await apiFetch(url);

    // Show IQR bounds summary
    const boundsDiv = document.getElementById('outlier-bounds');
    const boundsArr = Object.values(data.bounds);
    if (boundsArr.length) {
        boundsDiv.innerHTML = `
            <table>
                <thead>
                    <tr>
                        <th>Fuel Type</th><th>Q1 (25th pct)</th><th>Q3 (75th pct)</th>
                        <th>IQR</th><th>Lower Fence</th><th>Upper Fence</th><th>Stations (clean)</th>
                    </tr>
                </thead>
                <tbody>
                    ${boundsArr.map(b => `<tr>
                        <td>${b.fuel_type}</td>
                        <td>${ppl(b.q1)}</td><td>${ppl(b.q3)}</td>
                        <td>${b.iqr}p</td>
                        <td>${ppl(b.lower_fence)}</td><td>${ppl(b.upper_fence)}</td>
                        <td>${b.total_stations.toLocaleString()}</td>
                    </tr>`).join('')}
                </tbody>
            </table>
            <p style="font-size:0.8rem;color:var(--muted);margin-top:0.5rem;">
                Prices below the lower fence or above the upper fence are excluded from averages.
            </p>
        `;
    }

    // Show outlier table
    const body = document.getElementById('outlier-body');
    if (!data.outliers.length) {
        body.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:2rem;color:var(--muted)">No outliers for this fuel type</td></tr>';
        document.getElementById('outlier-pagination').innerHTML = '';
        return;
    }
    const fmtDate = ts => ts ? new Date(ts).toLocaleDateString() : '—';
    body.innerHTML = data.outliers.map(r => {
        const reasonLabel = r.exclusion_reason === 'anomaly_flagged'
            ? (r.anomaly_flags || []).map(f => `<span class="tag">${escHtml(f)}</span>`).join(' ')
            : '<span class="tag" style="background:#fff3cd;color:#856404;">outside IQR fence</span>';
        const overrideHtml = r.corrected_price != null
            ? `<span style="font-size:0.78rem;color:var(--muted);">${ppl(r.original_price)} → ${ppl(r.corrected_price)}</span>`
            : '—';
        return `<tr>
            <td><a href="#" class="station-link" data-node="${escHtml(r.node_id)}" data-name="${escHtml(r.trading_name)}" data-brand="${escHtml(r.brand_name || '')}" data-city="${escHtml(r.city || '')}" data-postcode="${escHtml(r.postcode || '')}" style="color:var(--accent);text-decoration:none;">${escHtml(r.trading_name)}</a></td>
            <td>${escHtml(r.city || '—')}</td>
            <td>${escHtml(r.postcode || '—')}</td>
            <td>${escHtml(r.brand_name || '—')}</td>
            <td>${escHtml(r.fuel_name || r.fuel_type)}</td>
            <td><strong>${ppl(r.price)}</strong></td>
            <td>${reasonLabel}</td>
            <td>${overrideHtml}</td>
            <td>${fmtDate(r.observed_at)}</td>
        </tr>`;
    }).join('');
    renderLogPagination('outlier-pagination', data.total, offset, 50, loadOutliers);
}

// ---------------------------------------------------------------------------
// Data / Admin tab
// ---------------------------------------------------------------------------
const dataSections = ['report', 'aliases', 'categories', 'overrides', 'postcodes'];
function switchDataSection() {
    const active = document.getElementById('data-section').value;
    dataSections.forEach(s => {
        document.getElementById('data-' + s).style.display = s === active ? '' : 'none';
    });
    // Lazy-load the section
    if (active === 'report') loadReport();
    else if (active === 'aliases') loadAliases();
    else if (active === 'categories') loadCategories();
    else if (active === 'overrides') loadOverrides();
    else if (active === 'postcodes') loadPostcodeIssues();
}

// --- Log section switcher ---
function switchLogSection() {
    const active = document.getElementById('log-section').value;
    document.getElementById('log-scrapes').style.display = active === 'scrapes' ? '' : 'none';
    document.getElementById('log-corrections').style.display = active === 'corrections' ? '' : 'none';
}

function renderLogPagination(containerId, total, offset, limit, loadFn) {
    const pag = document.getElementById(containerId);
    if (total <= limit) { pag.innerHTML = ''; return; }
    const page = Math.floor(offset / limit) + 1;
    const pages = Math.ceil(total / limit);
    pag.innerHTML = `
        <button ${offset === 0 ? 'disabled' : ''} id="${containerId}-prev">← Prev</button>
        <span class="info">Page ${page} of ${pages} (${total.toLocaleString()} records)</span>
        <button ${offset + limit >= total ? 'disabled' : ''} id="${containerId}-next">Next →</button>
    `;
    pag.querySelector(`#${containerId}-prev`)?.addEventListener('click', () => loadFn(offset - limit));
    pag.querySelector(`#${containerId}-next`)?.addEventListener('click', () => loadFn(offset + limit));
}

// --- Corrections Log ---
async function loadCorrectionsLog(offset = 0) {
    const data = await apiFetch(`/admin/corrections?limit=50&offset=${offset}`);
    const body = document.getElementById('corrections-body');
    if (!data.rows.length) {
        body.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:2rem;color:var(--muted)">No overrides recorded</td></tr>';
        document.getElementById('corrections-pagination').innerHTML = '';
        return;
    }
    body.innerHTML = data.rows.map(r => {
        const date = r.corrected_at ? new Date(r.corrected_at).toLocaleString() : '—';
        const priceDate = r.observed_at ? new Date(r.observed_at).toLocaleDateString() : '—';
        return `<tr>
            <td>${date}</td>
            <td>${priceDate}</td>
            <td>${escHtml(r.trading_name || '—')}</td>
            <td>${escHtml(r.city || '—')}</td>
            <td>${escHtml(r.fuel_type || '—')}</td>
            <td>${ppl(r.original_price)}</td>
            <td>${ppl(r.corrected_price)}</td>
            <td>${escHtml(r.reason || '—')}</td>
            <td>${escHtml(r.corrected_by || '—')}</td>
        </tr>`;
    }).join('');
    initSortableTables();
    renderLogPagination('corrections-pagination', data.total, offset, 50, loadCorrectionsLog);
}

// --- Scrape History ---
async function loadScrapeHistory(offset = 0) {
    const data = await apiFetch(`/admin/scrape-runs?limit=50&offset=${offset}`);
    const body = document.getElementById('scrapes-body');
    body.innerHTML = data.rows.map(r => {
        const started = r.started_at ? new Date(r.started_at).toLocaleString() : '—';
        const finished = r.finished_at ? new Date(r.finished_at).toLocaleString() : '—';
        const dur = r.duration_secs != null
            ? (r.duration_secs >= 60 ? Math.floor(r.duration_secs / 60) + 'm ' + (r.duration_secs % 60) + 's' : r.duration_secs + 's')
            : '—';
        const statusCls = r.status === 'completed' ? 'color:var(--green)' :
                          r.status === 'failed' ? 'color:var(--red)' : 'color:var(--muted)';
        return `<tr>
            <td>${r.id}</td>
            <td>${started}</td>
            <td>${finished}</td>
            <td>${dur}</td>
            <td>${escHtml(r.run_type)}</td>
            <td style="${statusCls};font-weight:600">${escHtml(r.status)}</td>
            <td>${r.batches_fetched ?? '—'}</td>
            <td>${r.stations_count != null ? r.stations_count.toLocaleString() : '—'}</td>
            <td>${r.price_records_count != null ? r.price_records_count.toLocaleString() : '—'}</td>
            <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHtml(r.error_message) || ''}">${escHtml(r.error_message) || '—'}</td>
        </tr>`;
    }).join('');
    initSortableTables();
    renderLogPagination('scrapes-pagination', data.total, offset, 50, loadScrapeHistory);
}

// --- Normalisation Report ---
async function loadReport() {
    const filter = document.getElementById('report-filter').value;
    const brand = document.getElementById('report-brand').value;
    let url = '/admin/normalisation-report?limit=200';
    if (filter) url += `&type=${filter}`;
    if (brand) url += `&brand=${encodeURIComponent(brand)}`;
    const data = await apiFetch(url);
    const body = document.getElementById('report-body');
    if (!data.length) {
        body.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:2rem;color:var(--muted)">No results</td></tr>';
        return;
    }
    body.innerHTML = data.map(r => `
        <tr>
            <td>${escHtml(r.raw_brand)}</td>
            <td>${r.alias_resolved ? escHtml(r.alias_resolved) : '<span style="color:var(--muted)">—</span>'}</td>
            <td>${r.override_resolved ? escHtml(r.override_resolved) : '<span style="color:var(--muted)">—</span>'}</td>
            <td><strong>${escHtml(r.final_brand)}</strong></td>
            <td>${categoryTag(r.forecourt_type)}</td>
            <td><span class="tag">${r.resolution_method}</span></td>
            <td>${r.station_count}</td>
        </tr>
    `).join('');
}

// --- Brand Aliases ---
async function loadAliases() {
    const data = await apiFetch('/admin/brand-aliases');
    const body = document.getElementById('alias-body');
    if (!data.length) {
        body.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:2rem;color:var(--muted)">No aliases defined</td></tr>';
        return;
    }
    body.innerHTML = data.map(r => `
        <tr>
            <td>${escHtml(r.raw_brand_name)}</td>
            <td><strong>${escHtml(r.canonical_brand)}</strong></td>
            <td>${r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}</td>
            ${canEdit() ? `<td><button class="btn-delete" onclick="deleteAlias('${escHtml(r.raw_brand_name)}')">Delete</button></td>` : ''}
        </tr>
    `).join('');
}

async function saveAlias() {
    const raw = document.getElementById('alias-raw').value.trim();
    const canonical = document.getElementById('alias-canonical').value.trim();
    if (!raw || !canonical) return alert('Both fields required');
    try {
        await apiPost('/admin/brand-aliases', { raw_brand_name: raw, canonical_brand: canonical });
        document.getElementById('alias-raw').value = '';
        document.getElementById('alias-canonical').value = '';
        loadAliases();
    } catch (e) { alert('Error: ' + e.message); }
}

async function deleteAlias(raw) {
    if (!confirm(`Delete alias "${raw}"?`)) return;
    try {
        await apiDelete(`/admin/brand-aliases/${encodeURIComponent(raw)}`);
        loadAliases();
    } catch (e) { alert('Error: ' + e.message); }
}

// --- Brand Categories ---
async function loadCategories() {
    const data = await apiFetch('/admin/brand-categories');
    const body = document.getElementById('category-body');
    if (!data.length) {
        body.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:2rem;color:var(--muted)">No categories defined</td></tr>';
        return;
    }
    body.innerHTML = data.map(r => `
        <tr>
            <td><strong>${escHtml(r.canonical_brand)}</strong></td>
            <td>${categoryTag(r.forecourt_type)}</td>
            ${canEdit() ? `<td><button class="btn-delete" onclick="deleteCategory('${escHtml(r.canonical_brand)}')">Delete</button></td>` : ''}
        </tr>
    `).join('');
}

async function saveCategory() {
    const brand = document.getElementById('cat-brand').value.trim();
    const type = document.getElementById('cat-type').value;
    if (!brand) return alert('Brand name required');
    try {
        await apiPost('/admin/brand-categories', { canonical_brand: brand, forecourt_type: type });
        document.getElementById('cat-brand').value = '';
        loadCategories();
    } catch (e) { alert('Error: ' + e.message); }
}

async function deleteCategory(brand) {
    if (!confirm(`Delete category for "${brand}"? It will default to Independent.`)) return;
    try {
        await apiDelete(`/admin/brand-categories/${encodeURIComponent(brand)}`);
        loadCategories();
    } catch (e) { alert('Error: ' + e.message); }
}

// --- Station Overrides ---
async function loadOverrides() {
    const data = await apiFetch('/admin/station-overrides');
    const body = document.getElementById('override-body');
    if (!data.length) {
        body.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:2rem;color:var(--muted)">No overrides defined</td></tr>';
        return;
    }
    body.innerHTML = data.map(r => `
        <tr>
            <td style="font-family:monospace;font-size:0.8rem">${escHtml(r.node_id)}</td>
            <td>${escHtml(r.trading_name)}</td>
            <td>${escHtml(r.raw_brand_name)}</td>
            <td><strong>${escHtml(r.canonical_brand)}</strong></td>
            <td>${escHtml(r.notes) || '<span style="color:var(--muted)">—</span>'}</td>
            <td>${r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}</td>
            ${canEdit() ? `<td><button class="btn-delete" onclick="deleteOverride('${escHtml(r.node_id)}')">Delete</button></td>` : ''}
        </tr>
    `).join('');
}

async function saveOverride() {
    const node = document.getElementById('override-node').value.trim();
    const brand = document.getElementById('override-brand').value.trim();
    const notes = document.getElementById('override-notes').value.trim();
    if (!node || !brand) return alert('Node ID and brand required');
    try {
        await apiPost('/admin/station-overrides', { node_id: node, canonical_brand: brand, notes: notes || null });
        document.getElementById('override-node').value = '';
        document.getElementById('override-brand').value = '';
        document.getElementById('override-notes').value = '';
        loadOverrides();
    } catch (e) { alert('Error: ' + e.message); }
}

async function deleteOverride(nodeId) {
    if (!confirm(`Delete override for station ${nodeId}?`)) return;
    try {
        await apiDelete(`/admin/station-overrides/${encodeURIComponent(nodeId)}`);
        loadOverrides();
    } catch (e) { alert('Error: ' + e.message); }
}

// --- Refresh materialised view ---
async function refreshView() {
    const btn = document.getElementById('btn-refresh-view');
    btn.textContent = 'Refreshing…';
    btn.disabled = true;
    try {
        await apiPost('/admin/refresh-view', {});
        btn.textContent = '✓ Refreshed';
        setTimeout(() => { btn.textContent = 'Refresh View'; btn.disabled = false; }, 2000);
    } catch (e) {
        btn.textContent = 'Refresh View';
        btn.disabled = false;
        alert('Error refreshing view: ' + e.message);
    }
}

async function loadPostcodeIssues() {
    const body = document.getElementById('postcode-issues-body');
    body.innerHTML = '<tr><td colspan="9">Loading…</td></tr>';
    try {
        const r = await fetch(API + '/admin/postcode-issues', { headers: authHeaders() });
        const rows = await r.json();
        body.innerHTML = rows.length ? rows.map(s => {
            const gmaps = (lat, lon) => lat != null && lon != null
                ? `<a href="https://www.google.com/maps?q=${lat},${lon}" target="_blank" rel="noopener">${lat}</a>`
                : '';
            const gmapsLon = (lat, lon) => lat != null && lon != null
                ? `<a href="https://www.google.com/maps?q=${lat},${lon}" target="_blank" rel="noopener">${lon}</a>`
                : '';
            let status = '';
            if (s.fixed_latitude != null) {
                status = `<span title="Manually set to ${s.fixed_latitude}, ${s.fixed_longitude}" style="color:var(--green,#00703c)">✓ Fixed</span>`
                    + `<br><small><a href="https://www.google.com/maps?q=${s.fixed_latitude},${s.fixed_longitude}" target="_blank" rel="noopener">${s.fixed_latitude}, ${s.fixed_longitude}</a></small>`;
            }
            return `<tr${s.coords_outside_uk ? ' style="background:var(--bg-warn,#fff3cd)"' : ''}>
            <td><code>${escHtml(s.postcode || '(empty)')}</code></td>
            <td>${escHtml(s.trading_name || '')}</td>
            <td>${escHtml(s.brand_name || '')}</td>
            <td>${escHtml(s.city || '')}</td>
            <td>${gmaps(s.api_latitude, s.api_longitude)}</td>
            <td>${gmapsLon(s.api_latitude, s.api_longitude)}</td>
            <td>${s.coords_outside_uk ? '⚠ Yes' : ''}</td>
            <td>${status}</td>
            <td>${canEdit() && s.postcode ? `<button class="small" onclick="fixPostcodeCoords('${s.postcode}', ${s.api_latitude}, ${s.api_longitude})">Fix coords</button>` : ''}</td>
        </tr>`;
        }).join('') : '<tr><td colspan="9">No postcode issues found.</td></tr>';
    } catch (e) {
        body.innerHTML = `<tr><td colspan="9">Error: ${e.message}</td></tr>`;
    }
}

async function fixPostcodeCoords(postcode, apiLat, apiLon) {
    const lat = prompt(`Correct latitude for ${postcode}:`, apiLat);
    if (lat === null) return;
    const lon = prompt(`Correct longitude for ${postcode}:`, apiLon);
    if (lon === null) return;
    const pLat = parseFloat(lat), pLon = parseFloat(lon);
    if (isNaN(pLat) || isNaN(pLon)) { alert('Invalid coordinates'); return; }
    try {
        const r = await fetch(API + '/admin/postcode-lookups/' + encodeURIComponent(postcode), {
            method: 'PATCH',
            headers: {...authHeaders(), 'Content-Type': 'application/json'},
            body: JSON.stringify({latitude: pLat, longitude: pLon}),
        });
        if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
        await apiPost('/admin/refresh-view', {});
        loadPostcodeIssues();
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

// ---------------------------------------------------------------------------
// User management
// ---------------------------------------------------------------------------
async function loadUsers() {
    const body = document.getElementById('users-body');
    body.innerHTML = '<tr><td colspan="5">Loading…</td></tr>';
    try {
        const users = await apiFetch('/admin/users');
        const adminCount = users.filter(u => u.groups.includes('admin')).length;
        body.innerHTML = users.length ? users.map(u => {
            const isLastAdmin = u.groups.includes('admin') && adminCount === 1;
            const role = u.groups.includes('admin') ? 'admin' : u.groups.includes('editor') ? 'editor' : 'readonly';
            const roleBtns = role === 'admin'
                ? `<button class="btn-delete" onclick="setUserRole('${escHtml(u.username)}','editor')"${isLastAdmin ? ' disabled title="Cannot demote the only admin"' : ''}>Demote to Editor</button>`
                : role === 'editor'
                ? `<button class="primary" style="padding:0.25rem 0.6rem;font-size:0.8rem" onclick="setUserRole('${escHtml(u.username)}','admin')">Make Admin</button>
                   <button class="btn-delete" onclick="setUserRole('${escHtml(u.username)}','readonly')">Demote to Read-only</button>`
                : `<button class="primary" style="padding:0.25rem 0.6rem;font-size:0.8rem" onclick="setUserRole('${escHtml(u.username)}','editor')">Make Editor</button>`;
            return `<tr>
            <td>${escHtml(u.email)}</td>
            <td>${escHtml(u.status)}${u.enabled ? '' : ' <span style="color:var(--red)">(disabled)</span>'}</td>
            <td><span class="tag">${role}</span></td>
            <td>${new Date(u.created).toLocaleDateString()}</td>
            <td style="white-space:nowrap">
                ${roleBtns}
                ${u.enabled
                    ? `<button class="btn-delete" onclick="toggleUser('${escHtml(u.username)}',false)"${isLastAdmin ? ' disabled title="Cannot disable the only admin"' : ''}>Disable</button>`
                    : `<button class="primary" style="padding:0.25rem 0.6rem;font-size:0.8rem" onclick="toggleUser('${escHtml(u.username)}',true)">Enable</button>`}
                <button class="btn-delete" onclick="deleteUser('${escHtml(u.username)}','${escHtml(u.email)}')"${isLastAdmin ? ' disabled title="Cannot delete the only admin"' : ''}>Delete</button>
            </td>
        </tr>`;
        }).join('') : '<tr><td colspan="5">No users found</td></tr>';
    } catch (e) {
        body.innerHTML = `<tr><td colspan="5" style="color:var(--red)">${escHtml(e.message)}</td></tr>`;
    }
}

async function createUser() {
    const email = document.getElementById('new-user-email').value.trim();
    const role = document.getElementById('new-user-role').value;
    const status = document.getElementById('user-status');
    if (!email) { alert('Email is required'); return; }
    try {
        await apiPost('/admin/users', { email, role });
        status.className = 'status-msg success';
        status.textContent = `User ${email} created (${role}). They will receive an email with a temporary password.`;
        document.getElementById('new-user-email').value = '';
        document.getElementById('new-user-role').value = 'editor';
        await loadUsers();
    } catch (e) {
        status.className = 'status-msg error';
        status.textContent = e.message;
    }
}

async function setUserRole(username, newRole) {
    try {
        // Remove from current groups, then add to new
        for (const g of ['admin', 'editor']) {
            try { await apiDelete(`/admin/users/${encodeURIComponent(username)}/groups/${encodeURIComponent(g)}`); } catch {}
        }
        if (newRole === 'admin' || newRole === 'editor') {
            await apiPost(`/admin/users/${encodeURIComponent(username)}/groups/${encodeURIComponent(newRole)}`, {});
        }
        await loadUsers();
    } catch (e) { alert(e.message); }
}

async function toggleGroup(username, group, add) {
    try {
        if (add) await apiPost(`/admin/users/${encodeURIComponent(username)}/groups/${encodeURIComponent(group)}`, {});
        else await apiDelete(`/admin/users/${encodeURIComponent(username)}/groups/${encodeURIComponent(group)}`);
        await loadUsers();
    } catch (e) { alert(e.message); }
}

async function toggleUser(username, enable) {
    try {
        await apiPost(`/admin/users/${encodeURIComponent(username)}/${enable ? 'enable' : 'disable'}`, {});
        await loadUsers();
    } catch (e) { alert(e.message); }
}

async function deleteUser(username, email) {
    if (!confirm(`Permanently delete user ${email}? This cannot be undone.`)) return;
    try {
        const r = await fetch(API + `/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE', headers: authHeaders() });
        if (!r.ok) throw new Error(`API error: ${r.status}`);
        await loadUsers();
    } catch (e) { alert(e.message); }
}
