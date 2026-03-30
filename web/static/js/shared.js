// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
let _authMode = 'none';
let _idToken = null;
let _refreshToken = null;
let _userRole = 'admin'; // 'admin' | 'editor' | 'readonly'
let _realRole = 'admin'; // actual role (never changes)
let _roleOverride = '';  // admin-only tier preview
let _cognitoRegion = null;
let _cognitoClientId = null;
let _cognitoSession = null;  // for NEW_PASSWORD_REQUIRED challenge
let _challengeUsername = null;

function showEnvBanner(env) {
    if (env && env !== 'production') {
        const banner = document.getElementById('env-banner');
        banner.textContent = env === 'local' ? '⚙ Local Development' : '⚠ Staging Environment';
        banner.className = 'env-banner ' + env;
        banner.style.display = '';
    }
}

async function initAuth() {
    try {
        const r = await fetch('/auth/config');
        const cfg = await r.json();
        _authMode = cfg.mode;
        showEnvBanner(cfg.environment);
        if (_authMode === 'cognito') {
            _cognitoRegion = cfg.region;
            _cognitoClientId = cfg.clientId;
            _idToken = localStorage.getItem('ff_id_token');
            _refreshToken = localStorage.getItem('ff_refresh_token');
            if (_idToken) {
                // Check if token is still valid by decoding exp
                try {
                    const payload = JSON.parse(atob(_idToken.split('.')[1]));
                    if (payload.exp * 1000 < Date.now()) {
                        // Token expired — try refresh
                        const refreshed = await refreshTokens();
                        if (!refreshed) { showLogin(); return false; }
                    }
                    showApp(payload.email || payload['cognito:username'] || '');
                    return true;
                } catch (e) {
                    console.warn('Token validation failed:', e);
                    showLogin(); return false;
                }
            }
            showLogin(); return false;
        }
        // api-key or no-auth mode — no login needed
        showApp('');
        return true;
    } catch {
        // Can't reach /auth/config — assume no auth (local dev)
        showApp('');
        return true;
    }
}

async function cognitoCall(action, body) {
    const r = await fetch(`https://cognito-idp.${_cognitoRegion}.amazonaws.com/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-amz-json-1.1',
            'X-Amz-Target': `AWSCognitoIdentityProviderService.${action}`,
        },
        body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.message || data.__type || 'Auth error');
    return data;
}

async function handleLogin(e) {
    e.preventDefault();
    const btn = document.getElementById('login-btn');
    const errEl = document.getElementById('login-error');
    errEl.className = 'login-error';
    btn.disabled = true;
    btn.textContent = 'Signing in…';

    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;

    try {
        // Check if we're responding to a NEW_PASSWORD_REQUIRED challenge
        if (_cognitoSession) {
            const newPw = document.getElementById('login-new-password').value;
            if (!newPw) { throw new Error('Please enter a new password'); }
            const resp = await cognitoCall('RespondToAuthChallenge', {
                ChallengeName: 'NEW_PASSWORD_REQUIRED',
                ClientId: _cognitoClientId,
                Session: _cognitoSession,
                ChallengeResponses: {
                    USERNAME: _challengeUsername,
                    NEW_PASSWORD: newPw,
                },
            });
            _cognitoSession = null;
            _challengeUsername = null;
            storeTokens(resp.AuthenticationResult);
            return;
        }

        const resp = await cognitoCall('InitiateAuth', {
            AuthFlow: 'USER_PASSWORD_AUTH',
            ClientId: _cognitoClientId,
            AuthParameters: { USERNAME: email, PASSWORD: password },
        });

        if (resp.ChallengeName === 'NEW_PASSWORD_REQUIRED') {
            _cognitoSession = resp.Session;
            _challengeUsername = email;
            document.getElementById('new-password-fields').style.display = 'block';
            btn.textContent = 'Set new password';
            btn.disabled = false;
            errEl.textContent = 'Please set a new password.';
            errEl.className = 'login-error visible';
            return;
        }

        storeTokens(resp.AuthenticationResult);
    } catch (err) {
        errEl.textContent = err.message;
        errEl.className = 'login-error visible';
        btn.disabled = false;
        btn.textContent = _cognitoSession ? 'Set new password' : 'Sign in';
    }
    return false;
}

function storeTokens(result) {
    _idToken = result.IdToken;
    _refreshToken = result.RefreshToken || _refreshToken;
    localStorage.setItem('ff_id_token', _idToken);
    if (_refreshToken) localStorage.setItem('ff_refresh_token', _refreshToken);
    const payload = JSON.parse(atob(_idToken.split('.')[1]));
    showApp(payload.email || payload['cognito:username'] || '');
    // Schedule token refresh ~5 min before expiry
    const expiresIn = (payload.exp * 1000) - Date.now() - 300000;
    if (expiresIn > 0) setTimeout(() => refreshTokens(), expiresIn);
    startApp();
}

async function refreshTokens() {
    if (!_refreshToken) return false;
    try {
        const resp = await cognitoCall('InitiateAuth', {
            AuthFlow: 'REFRESH_TOKEN_AUTH',
            ClientId: _cognitoClientId,
            AuthParameters: { REFRESH_TOKEN: _refreshToken },
        });
        _idToken = resp.AuthenticationResult.IdToken;
        localStorage.setItem('ff_id_token', _idToken);
        const payload = JSON.parse(atob(_idToken.split('.')[1]));
        const expiresIn = (payload.exp * 1000) - Date.now() - 300000;
        if (expiresIn > 0) setTimeout(() => refreshTokens(), expiresIn);
        return true;
    } catch (e) {
        console.warn('Token refresh failed:', e);
        localStorage.removeItem('ff_id_token');
        localStorage.removeItem('ff_refresh_token');
        _idToken = null;
        _refreshToken = null;
        return false;
    }
}

function showLogin() {
    document.getElementById('login-overlay').classList.remove('hidden');
    document.getElementById('logout-btn').style.display = 'none';
    document.getElementById('user-email').textContent = '';
}

function showApp(email) {
    document.getElementById('login-overlay').classList.add('hidden');
    if (email) {
        document.getElementById('user-email').textContent = email;
        document.getElementById('logout-btn').style.display = '';
    }
}

async function fetchUserRole() {
    try {
        const r = await fetch('/auth/me', { headers: authHeaders() });
        if (r.ok) {
            const data = await r.json();
            _userRole = data.role || 'readonly';
            _realRole = data.real_role || data.role || 'readonly';
        }
    } catch { /* no-auth mode defaults to admin */ }
    applyRolePermissions();
    // Show tier switcher for admins
    const switcher = document.getElementById('role-switcher');
    if (switcher) switcher.style.display = _realRole === 'admin' ? '' : 'none';
}

function canEdit() { return _userRole === 'admin' || _userRole === 'editor'; }

function applyRolePermissions() {
    // Hide Users tab for non-admin
    const usersTab = document.querySelector('.tab[data-panel="users"]');
    if (usersTab) usersTab.style.display = _userRole === 'admin' ? '' : 'none';

    // Data Cleanup: hide mutation controls for readonly
    document.querySelectorAll('#data-aliases .controls, #data-categories .controls, #data-overrides .controls').forEach(el => {
        el.style.display = canEdit() ? '' : 'none';
    });
    const refreshBtn = document.getElementById('btn-refresh-view');
    if (refreshBtn) refreshBtn.style.display = canEdit() ? '' : 'none';

    // Download buttons: editor+ only
    document.querySelectorAll('#map-download-btns, #trend-download-btns, #search-download-btns, .download-btns').forEach(el => {
        el.style.display = canEdit() ? '' : 'none';
    });

    // Readonly notices
    document.querySelectorAll('.readonly-notice').forEach(el => {
        el.style.display = _userRole === 'readonly' ? '' : 'none';
    });
}

function logout() {
    localStorage.removeItem('ff_id_token');
    localStorage.removeItem('ff_refresh_token');
    _idToken = null;
    _refreshToken = null;
    location.reload();
}

function switchRole(override) {
    _roleOverride = override;
    _userRole = override || _realRole;
    applyRolePermissions();
}

function authHeaders() {
    const h = {};
    if (_authMode === 'cognito' && _idToken) {
        h['Authorization'] = 'Bearer ' + _idToken;
    }
    if (_roleOverride) {
        h['X-Role-Override'] = _roleOverride;
    }
    return h;
}

const API = '/api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
async function apiFetch(path) {
    let r = await fetch(API + path, { headers: authHeaders() });
    if (r.status === 401 && _authMode === 'cognito') {
        const refreshed = await refreshTokens();
        if (refreshed) {
            r = await fetch(API + path, { headers: authHeaders() });
        }
        if (r.status === 401) { showLogin(); throw new Error('Session expired'); }
    }
    if (!r.ok) throw new Error(`API error: ${r.status}`);
    return r.json();
}

function ppl(v) { return v != null ? Number(v).toFixed(1) + 'p' : '—'; }

function escHtml(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
}

// Global delegation handler for station links (works in tables, Leaflet popups, etc.)
document.addEventListener('click', e => {
    const link = e.target.closest('.station-link');
    if (!link) return;
    e.preventDefault();
    openStationTrend(link.dataset.node, link.dataset.name, link.dataset.brand, link.dataset.city, link.dataset.postcode, link.dataset.category, link.dataset.rawBrand);
});

// Global delegation handler for edit-prices links (avoids inline onclick apostrophe issues)
document.addEventListener('click', e => {
    const link = e.target.closest('.edit-prices-link');
    if (!link) return;
    e.preventDefault();
    openPriceEditor(link.dataset.node, link.dataset.name, 'anomalies');
});

function categoryTag(type) {
    const cls = (type || 'uncategorised').toLowerCase().replace(/ /g, '-');
    return `<span class="category-tag ${cls}">${escHtml(type) || 'Uncategorised'}</span>`;
}

// ---------------------------------------------------------------------------
// Download helpers (CSV / JSON)
// ---------------------------------------------------------------------------
function toCsv(rows) {
    if (!rows.length) return '';
    const keys = Object.keys(rows[0]);
    const escape = v => {
        if (v == null) return '';
        const s = String(v);
        return s.includes(',') || s.includes('"') || s.includes('\n')
            ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    return [keys.join(','), ...rows.map(r => keys.map(k => escape(r[k])).join(','))].join('\n');
}

function triggerDownload(content, filename, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function downloadFile(rows, baseName, format) {
    if (!rows || !rows.length) return;
    if (format === 'csv') {
        triggerDownload(toCsv(rows), baseName + '.csv', 'text/csv');
    } else {
        triggerDownload(JSON.stringify(rows, null, 2), baseName + '.json', 'application/json');
    }
}

/**
 * Fetch a server-side export endpoint and trigger a file download.
 * @param {string} url      - API path (without API prefix), e.g. '/api/prices/history/export?...'
 * @param {string[]} nameParts - segments for the filename (joined with '_')
 * @param {string} fmt       - 'csv' or 'json'
 * @param {HTMLButtonElement} btn - button to show loading state on
 */
function fetchExport(url, nameParts, fmt, btn) {
    btn.disabled = true;
    btn.textContent = '⏳ Exporting…';
    fetch(url, { headers: authHeaders() })
        .then(async resp => {
            if (resp.status === 401 && _authMode === 'cognito') {
                const refreshed = await refreshTokens();
                if (refreshed) {
                    resp = await fetch(url, { headers: authHeaders() });
                }
                if (resp.status === 401) { showLogin(); throw new Error('Session expired'); }
            }
            if (!resp.ok) throw new Error('Export failed: ' + resp.status);
            return resp.blob();
        })
        .then(blob => {
            const ts = new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').slice(0, 19);
            nameParts.push(ts);
            const ext = '.' + fmt;
            const maxLen = 251 - ext.length;
            let stem = nameParts.join('_');
            if (stem.length > maxLen) stem = stem.slice(0, maxLen - 1) + '\u2026';
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = stem + ext;
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

const COLOURS = [
    '#1d70b8','#d4351c','#00703c','#f47738','#5694ca',
    '#912b88','#28a197','#b58840','#505a5f','#4c2c92',
    '#d53880','#006435','#1d70b8','#003078',
];

// ---------------------------------------------------------------------------
// Sortable tables
// ---------------------------------------------------------------------------
function initSortableTables() {
    document.querySelectorAll('table.sortable').forEach(table => {
        const headers = table.querySelectorAll('thead th');
        headers.forEach((th, colIdx) => {
            th.classList.add('sortable');
            th.addEventListener('click', () => sortTable(table, colIdx, th));
        });
    });
}

function sortTable(table, colIdx, th) {
    const tbody = table.querySelector('tbody');
    if (!tbody) return;
    const rows = Array.from(tbody.querySelectorAll('tr'));
    if (!rows.length) return;

    // Toggle direction
    const wasAsc = th.classList.contains('asc');
    table.querySelectorAll('th').forEach(h => h.classList.remove('asc', 'desc'));
    const dir = wasAsc ? 'desc' : 'asc';
    th.classList.add(dir);

    rows.sort((a, b) => {
        const aCell = a.cells[colIdx];
        const bCell = b.cells[colIdx];
        if (!aCell || !bCell) return 0;
        let aVal = aCell.textContent.trim();
        let bVal = bCell.textContent.trim();

        // Try numeric comparison (handles prices like "149.9p", plain numbers, etc.)
        const aNum = parseFloat(aVal.replace(/[^0-9.\-]/g, ''));
        const bNum = parseFloat(bVal.replace(/[^0-9.\-]/g, ''));
        if (!isNaN(aNum) && !isNaN(bNum)) {
            return dir === 'asc' ? aNum - bNum : bNum - aNum;
        }

        // Try date comparison
        const aDate = Date.parse(aVal);
        const bDate = Date.parse(bVal);
        if (!isNaN(aDate) && !isNaN(bDate)) {
            return dir === 'asc' ? aDate - bDate : bDate - aDate;
        }

        // String comparison
        return dir === 'asc'
            ? aVal.localeCompare(bVal, undefined, { sensitivity: 'base' })
            : bVal.localeCompare(aVal, undefined, { sensitivity: 'base' });
    });

    rows.forEach(r => tbody.appendChild(r));
}

// ---------------------------------------------------------------------------
// Clear all search filters
// ---------------------------------------------------------------------------
function clearSearchFilters() {
    document.getElementById('search-fuel').value = 'E10';
    document.getElementById('search-postcode').value = '';
    document.getElementById('search-station').value = '';
    document.getElementById('search-brand').value = '';
    document.getElementById('search-city').value = '';
    document.getElementById('search-min').value = '';
    document.getElementById('search-max').value = '';
    document.getElementById('search-supermarket').checked = false;
    document.getElementById('search-motorway').checked = false;
    document.getElementById('search-exclude-outliers').checked = false;
    ['search-category-ms', 'search-country-ms', 'search-region-ms', 'search-rural-urban-ms'].forEach(resetMultiSelect);
    document.getElementById('search-district').value = '';
    document.getElementById('search-constituency').value = '';
}

// ---------------------------------------------------------------------------
// Navigate to Search with pre-filled filters
// ---------------------------------------------------------------------------
function navigateToSearch(filters) {
    // Reset all search fields
    clearSearchFilters();

    // Set fuel type
    if (filters.fuel_type) {
        document.getElementById('search-fuel').value = filters.fuel_type;
    }
    // Set filters by matching select options or input values
    if (filters.station) document.getElementById('search-station').value = filters.station;
    if (filters.brand) document.getElementById('search-brand').value = filters.brand;
    if (filters.category) setMultiSelectValues('search-category-ms', filters.category);
    if (filters.rural_urban) setMultiSelectValues('search-rural-urban-ms', filters.rural_urban);
    if (filters.district) {
        const sel = document.getElementById('search-district');
        for (const opt of sel.options) {
            if (opt.value === filters.district) { sel.value = opt.value; break; }
        }
    }
    if (filters.region) setMultiSelectValues('search-region-ms', filters.region);
    if (filters.country) setMultiSelectValues('search-country-ms', filters.country);
    if (filters.exclude_outliers) {
        document.getElementById('search-exclude-outliers').checked = true;
    }

    // Switch to search tab and update hash
    switchTab('search');

    // Run the search
    doSearch(0);
}

// ---------------------------------------------------------------------------
// Populate fuel type selectors
// ---------------------------------------------------------------------------
let fuelTypes = [];

async function loadFuelTypes() {
    fuelTypes = await apiFetch('/fuel-types');
    for (const sel of document.querySelectorAll('#dashboard-fuel, #map-fuel, #trend-fuel, #search-fuel')) {
        sel.innerHTML = '';
        fuelTypes.forEach(ft => {
            const o = document.createElement('option');
            o.value = ft.fuel_type_code;
            o.textContent = ft.fuel_name || ft.fuel_type_code;
            if (ft.fuel_type_code === 'E10') o.selected = true;
            sel.appendChild(o);
        });
    }
}

async function loadRegions() {
    const regions = await apiFetch('/regions');
    ['map-region'].forEach(id => {
        const sel = document.getElementById(id);
        if (!sel) return;
        regions.forEach(r => {
            const o = document.createElement('option');
            o.value = r; o.textContent = r;
            sel.appendChild(o);
        });
    });
    // Populate multi-selects for search and trends
    ['search-region-ms', 'trend-region-ms'].forEach(msId => {
        const opts = document.querySelector('#' + msId + ' .multi-select-options');
        if (!opts) return;
        opts.innerHTML = regions.map(r =>
            `<label><input type="checkbox" value="${r}"> ${r}</label>`
        ).join('');
        opts.querySelectorAll('input[type=checkbox]').forEach(cb => {
            cb.addEventListener('change', () => updateMultiSelectDisplay(msId));
        });
    });
}

async function loadDistricts() {
    const districts = await apiFetch('/districts');
    const sel = document.getElementById('search-district');
    districts.forEach(d => {
        const o = document.createElement('option');
        o.value = d; o.textContent = d;
        sel.appendChild(o);
    });
}

async function loadConstituencies() {
    const constituencies = await apiFetch('/constituencies');
    const sel = document.getElementById('search-constituency');
    constituencies.forEach(c => {
        const o = document.createElement('option');
        o.value = c; o.textContent = c;
        sel.appendChild(o);
    });
}

// ---------------------------------------------------------------------------
// Multi-select helpers
// ---------------------------------------------------------------------------
function getMultiSelectValues(msId) {
    const checked = document.querySelectorAll('#' + msId + ' input[type=checkbox]:checked');
    return Array.from(checked).map(cb => cb.value).join(',');
}
function updateMultiSelectDisplay(msId) {
    const ms = document.getElementById(msId);
    if (!ms) return;
    const checked = ms.querySelectorAll('input[type=checkbox]:checked');
    const display = ms.querySelector('.multi-select-display');
    if (checked.length === 0) display.textContent = 'All';
    else if (checked.length <= 2) display.textContent = Array.from(checked).map(cb => cb.value.replace(/:.*/, '')).join(', ');
    else display.textContent = checked.length + ' selected';
}
function resetMultiSelect(msId) {
    document.querySelectorAll('#' + msId + ' input[type=checkbox]').forEach(cb => cb.checked = false);
    updateMultiSelectDisplay(msId);
}
function setMultiSelectValues(msId, csv) {
    const vals = csv.split(',');
    document.querySelectorAll('#' + msId + ' input[type=checkbox]').forEach(cb => {
        cb.checked = vals.includes(cb.value);
    });
    updateMultiSelectDisplay(msId);
}
function getSelectedCategories() { return getMultiSelectValues('search-category-ms'); }

// ---------------------------------------------------------------------------
// API wrappers (POST / DELETE)
// ---------------------------------------------------------------------------
async function apiPost(path, body) {
    const r = await fetch(API + path, {
        method: 'POST', headers: {...authHeaders(), 'Content-Type': 'application/json'},
        body: JSON.stringify(body),
    });
    if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(err.detail || r.statusText);
    }
    return r.json();
}

async function apiDelete(path) {
    const r = await fetch(API + path, { method: 'DELETE', headers: authHeaders() });
    if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        throw new Error(err.detail || r.statusText);
    }
    return r.json();
}
