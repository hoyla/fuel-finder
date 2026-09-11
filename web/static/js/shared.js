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
let _cognitoDomain = null;
let _cognitoProvider = null;
let _allowedGoogleDomain = null;
let _authMethod = null;
let _cognitoSession = null;  // for NEW_PASSWORD_REQUIRED challenge
let _challengeUsername = null;
let reconstructedHistoryEnabled = false;

const OAUTH_STATE_KEY = 'ff_oauth_state';
const OAUTH_NONCE_KEY = 'ff_oauth_nonce';
const OAUTH_VERIFIER_KEY = 'ff_oauth_verifier';
const OAUTH_RETURN_HASH_KEY = 'ff_oauth_return_hash';

function showEnvBanner(env) {
    if (env && env !== 'production') {
        const banner = document.getElementById('env-banner');
        banner.textContent = env === 'local' ? '⚙ Local Development' : '⚠ Staging Environment';
        banner.className = 'env-banner ' + env;
        banner.style.display = '';
    }
}

async function initAuth() {
    let cfg;
    try {
        const r = await fetch('/auth/config');
        if (!r.ok) throw new Error('Unable to load authentication configuration');
        cfg = await r.json();
    } catch (err) {
        console.warn('Authentication configuration failed:', err);
        showLogin('Unable to load authentication configuration');
        return false;
    }

    _authMode = cfg.mode;
    showEnvBanner(cfg.environment);
    reconstructedHistoryEnabled = Boolean(cfg.reconstructed_history);
    document.getElementById('trend-comparison-tab').hidden = !cfg.trend_comparison || reconstructedHistoryEnabled;
    initialiseSensitivity();
    if (_authMode !== 'cognito') {
        // api-key or no-auth mode — no login needed
        showApp('');
        return true;
    }

    _cognitoRegion = cfg.region;
    _cognitoClientId = cfg.clientId;
    _cognitoDomain = cfg.oauth?.domain?.replace(/\/+$/, '') || null;
    _cognitoProvider = cfg.oauth?.provider || null;
    _allowedGoogleDomain = cfg.oauth?.allowedDomain || null;
    configureLoginOptions();

    try {
        const oauthResult = await handleOAuthCallback();
        if (oauthResult) storeOAuthTokens(oauthResult, false);
    } catch (err) {
        console.warn('OAuth callback failed:', err);
        clearAuthTokens();
        showLogin(err.message || 'Google sign-in failed');
        return false;
    }

    _idToken = localStorage.getItem('ff_id_token');
    _refreshToken = localStorage.getItem('ff_refresh_token');
    _authMethod = localStorage.getItem('ff_auth_method');
    if (_idToken) {
        // Check if token is still valid by decoding exp
        try {
            let payload = decodeJwtPayload(_idToken);
            if (payload.exp * 1000 < Date.now()) {
                // Token expired — try refresh
                const refreshed = await refreshTokens();
                if (!refreshed) { showLogin(); return false; }
                payload = decodeJwtPayload(_idToken);
            }
            showApp(payload.email || payload['cognito:username'] || '');
            return true;
        } catch (e) {
            console.warn('Token validation failed:', e);
            clearAuthTokens();
            showLogin();
            return false;
        }
    }
    showLogin();
    return false;
}

function configureLoginOptions() {
    const googleButton = document.getElementById('google-login-btn');
    const googleHelp = document.getElementById('google-login-help');
    const passwordLogin = document.getElementById('password-login');
    if (!googleButton || !googleHelp || !passwordLogin) return;
    if (_cognitoDomain && _cognitoProvider) {
        googleButton.style.display = '';
        googleHelp.style.display = '';
        if (_allowedGoogleDomain) {
            googleHelp.textContent = `Use your @${_allowedGoogleDomain} Google account.`;
        }
        googleButton.textContent = 'Sign in with Guardian Google';
        passwordLogin.open = false;
    } else {
        googleButton.style.display = 'none';
        googleHelp.style.display = 'none';
        passwordLogin.open = true;
    }
}

function base64UrlEncode(bytes) {
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function randomBase64Url(byteLength = 32) {
    const bytes = new Uint8Array(byteLength);
    crypto.getRandomValues(bytes);
    return base64UrlEncode(bytes);
}

async function pkceChallenge(verifier) {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
    return base64UrlEncode(new Uint8Array(digest));
}

function oauthRedirectUri() {
    return `${window.location.origin}/`;
}

function buildOAuthAuthorizeUrl(state, nonce, challenge) {
    const url = new URL(`${_cognitoDomain}/oauth2/authorize`);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('client_id', _cognitoClientId);
    url.searchParams.set('redirect_uri', oauthRedirectUri());
    url.searchParams.set('scope', 'openid email profile');
    url.searchParams.set('identity_provider', _cognitoProvider);
    url.searchParams.set('state', state);
    url.searchParams.set('nonce', nonce);
    url.searchParams.set('code_challenge_method', 'S256');
    url.searchParams.set('code_challenge', challenge);
    url.searchParams.set('prompt', 'select_account');
    return url.toString();
}

async function startGoogleLogin() {
    const button = document.getElementById('google-login-btn');
    const errEl = document.getElementById('login-error');
    if (!_cognitoDomain || !_cognitoProvider) {
        showLogin('Google sign-in is not configured');
        return;
    }
    button.disabled = true;
    button.textContent = 'Redirecting…';
    errEl.className = 'login-error';
    try {
        const state = randomBase64Url();
        const nonce = randomBase64Url();
        const verifier = randomBase64Url(64);
        const challenge = await pkceChallenge(verifier);
        sessionStorage.setItem(OAUTH_STATE_KEY, state);
        sessionStorage.setItem(OAUTH_NONCE_KEY, nonce);
        sessionStorage.setItem(OAUTH_VERIFIER_KEY, verifier);
        sessionStorage.setItem(OAUTH_RETURN_HASH_KEY, window.location.hash || '');
        window.location.assign(buildOAuthAuthorizeUrl(state, nonce, challenge));
    } catch (err) {
        button.disabled = false;
        button.textContent = 'Sign in with Guardian Google';
        showLogin(err.message || 'Unable to start Google sign-in');
    }
}

function clearOAuthRequest() {
    sessionStorage.removeItem(OAUTH_STATE_KEY);
    sessionStorage.removeItem(OAUTH_NONCE_KEY);
    sessionStorage.removeItem(OAUTH_VERIFIER_KEY);
    sessionStorage.removeItem(OAUTH_RETURN_HASH_KEY);
}

function cleanOAuthCallbackUrl(returnHash = '') {
    const url = new URL(window.location.href);
    for (const key of ['code', 'state', 'error', 'error_description']) {
        url.searchParams.delete(key);
    }
    url.hash = returnHash;
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
}

async function exchangeOAuthCode(code, verifier) {
    const body = new URLSearchParams({
        grant_type: 'authorization_code',
        client_id: _cognitoClientId,
        code,
        code_verifier: verifier,
        redirect_uri: oauthRedirectUri(),
    });
    const response = await fetch(`${_cognitoDomain}/oauth2/token`, {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body,
    });
    const result = await response.json();
    if (!response.ok) {
        throw new Error(result.error_description || result.error || 'Unable to complete Google sign-in');
    }
    return result;
}

async function handleOAuthCallback() {
    const params = new URLSearchParams(window.location.search);
    if (!params.has('code') && !params.has('error')) return null;

    const returnHash = sessionStorage.getItem(OAUTH_RETURN_HASH_KEY) || '';
    const expectedState = sessionStorage.getItem(OAUTH_STATE_KEY);
    const expectedNonce = sessionStorage.getItem(OAUTH_NONCE_KEY);
    const verifier = sessionStorage.getItem(OAUTH_VERIFIER_KEY);
    try {
        if (params.get('error')) {
            throw new Error(params.get('error_description') || params.get('error'));
        }
        if (!expectedState || params.get('state') !== expectedState || !verifier || !expectedNonce) {
            throw new Error('Google sign-in response could not be verified');
        }
        const result = await exchangeOAuthCode(params.get('code'), verifier);
        const payload = decodeJwtPayload(result.id_token);
        if (payload.nonce !== expectedNonce) {
            throw new Error('Google sign-in response had an invalid nonce');
        }
        return result;
    } finally {
        cleanOAuthCallbackUrl(returnHash);
        clearOAuthRequest();
    }
}

function decodeJwtPayload(token) {
    const encoded = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=');
    return JSON.parse(atob(padded));
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
            storeTokens(resp.AuthenticationResult, true, 'password');
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

        storeTokens(resp.AuthenticationResult, true, 'password');
    } catch (err) {
        errEl.textContent = err.message;
        errEl.className = 'login-error visible';
        btn.disabled = false;
        btn.textContent = _cognitoSession ? 'Set new password' : 'Sign in';
    }
    return false;
}

function storeTokens(result, start = true, authMethod = _authMethod || 'password') {
    _idToken = result.IdToken;
    _refreshToken = result.RefreshToken || _refreshToken;
    _authMethod = authMethod;
    localStorage.setItem('ff_id_token', _idToken);
    if (_refreshToken) localStorage.setItem('ff_refresh_token', _refreshToken);
    localStorage.setItem('ff_auth_method', _authMethod);
    const payload = decodeJwtPayload(_idToken);
    showApp(payload.email || payload['cognito:username'] || '');
    // Schedule token refresh ~5 min before expiry
    const expiresIn = (payload.exp * 1000) - Date.now() - 300000;
    if (expiresIn > 0) setTimeout(() => refreshTokens(), expiresIn);
    if (start) startApp();
}

function storeOAuthTokens(result, start = true) {
    storeTokens({
        IdToken: result.id_token,
        RefreshToken: result.refresh_token,
    }, start, 'oauth');
}

async function refreshTokens() {
    if (!_refreshToken) return false;
    try {
        if (_authMethod === 'oauth' && _cognitoDomain) {
            const response = await fetch(`${_cognitoDomain}/oauth2/token`, {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: new URLSearchParams({
                    grant_type: 'refresh_token',
                    client_id: _cognitoClientId,
                    refresh_token: _refreshToken,
                }),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error_description || result.error || 'Token refresh failed');
            storeOAuthTokens(result, false);
            return true;
        }
        const resp = await cognitoCall('InitiateAuth', {
            AuthFlow: 'REFRESH_TOKEN_AUTH',
            ClientId: _cognitoClientId,
            AuthParameters: { REFRESH_TOKEN: _refreshToken },
        });
        _idToken = resp.AuthenticationResult.IdToken;
        localStorage.setItem('ff_id_token', _idToken);
        const payload = decodeJwtPayload(_idToken);
        const expiresIn = (payload.exp * 1000) - Date.now() - 300000;
        if (expiresIn > 0) setTimeout(() => refreshTokens(), expiresIn);
        return true;
    } catch (e) {
        console.warn('Token refresh failed:', e);
        clearAuthTokens();
        return false;
    }
}

function showLogin(message = '') {
    document.getElementById('login-overlay').classList.remove('hidden');
    document.getElementById('logout-btn').style.display = 'none';
    document.getElementById('user-email').textContent = '';
    const errEl = document.getElementById('login-error');
    if (message) {
        errEl.textContent = message;
        errEl.className = 'login-error visible';
    } else {
        errEl.className = 'login-error';
    }
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
        } else if (_authMode === 'cognito' && (r.status === 401 || r.status === 403)) {
            const data = await r.json().catch(() => ({}));
            clearAuthTokens();
            showLogin(data.detail || 'Your account is not authorised');
            return false;
        }
    } catch (err) {
        if (_authMode === 'cognito') {
            console.warn('Account verification failed:', err);
            clearAuthTokens();
            showLogin('Unable to verify your account');
            return false;
        }
        // No-auth mode defaults to admin.
    }
    applyRolePermissions();
    // Show tier switcher for admins
    const switcher = document.getElementById('role-switcher');
    if (switcher) switcher.style.display = _realRole === 'admin' ? '' : 'none';
    return true;
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

function clearAuthTokens() {
    localStorage.removeItem('ff_id_token');
    localStorage.removeItem('ff_refresh_token');
    localStorage.removeItem('ff_auth_method');
    _idToken = null;
    _refreshToken = null;
    _authMethod = null;
}

function logout() {
    clearAuthTokens();
    if (_cognitoDomain && _cognitoClientId) {
        const url = new URL(`${_cognitoDomain}/logout`);
        url.searchParams.set('client_id', _cognitoClientId);
        url.searchParams.set('logout_uri', oauthRedirectUri());
        window.location.assign(url.toString());
        return;
    }
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
async function apiFetch(path, options = {}) {
    let r = await fetch(API + path, { ...options, headers: { ...authHeaders(), ...options.headers } });
    if (r.status === 401 && _authMode === 'cognito') {
        const refreshed = await refreshTokens();
        if (refreshed) {
            r = await fetch(API + path, { ...options, headers: { ...authHeaders(), ...options.headers } });
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
    const fuelSelection = link.closest('#map') ? getFuelSelection('map-fuel')
        : link.closest('#anomaly-outliers') ? getFuelSelection('outlier-fuel') : undefined;
    openStationTrend(link.dataset.node, link.dataset.name, link.dataset.brand, link.dataset.city, link.dataset.postcode, link.dataset.category, link.dataset.rawBrand, link.dataset.lat, link.dataset.lon, link.dataset.motorway, link.dataset.supermarket, link.dataset.region, link.dataset.district, fuelSelection);
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
    setFuelSelection('search-fuel', '');
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
    if (filters.fuel_type != null) {
        setFuelSelection('search-fuel', filters.fuel_type);
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

// Colours for multi-fuel trend charts
const FUEL_COLOURS = {
    E10:         '#5694CA',  // light blue
    E5:          '#1d4f91',  // dark blue
    B7_STANDARD: '#e07070',  // light red-pink
    B7_PREMIUM:  '#8b3a3a',  // dark red-brown
    B10:         '#6fbf73',  // light green
    HVO:         '#2e7d32',  // dark green
};

function fuelColour(code) {
    return FUEL_COLOURS[code] || '#888';
}

function fuelLabel(code) {
    const ft = fuelTypes.find(f => f.fuel_type_code === code);
    return ft ? (ft.fuel_name || code) : code;
}

/**
 * Fetch history for all fuel types in parallel, returning Chart.js datasets.
 * @param {function} urlBuilder - fn(fuelCode) returning the API URL
 * @param {boolean} hourly - whether the data is hourly granularity
 * @returns {Promise<{datasets: Array, granularity: string, allData: Object}>}
 */
async function fetchAllFuelTrends(urlBuilder, hourly, selection = '') {
    const results = await Promise.all(
        selectedFuelTypes(selection).map(async ft => {
            const resp = await apiFetch(urlBuilder(ft.fuel_type_code));
            return { code: ft.fuel_type_code, resp };
        })
    );
    // Determine granularity from first non-empty response
    let granularity = hourly ? 'hourly' : 'daily';
    for (const r of results) {
        if (r.resp.granularity) { granularity = r.resp.granularity; break; }
    }
    const isHourly = granularity === 'hourly';
    const datasets = [];
    const allData = {};
    for (const { code, resp } of results) {
        const data = resp.data || [];
        if (!data.length) continue;
        allData[code] = data;
        const colour = fuelColour(code);
        datasets.push({
            label: fuelLabel(code),
            data: data.map(d => ({ x: new Date(d.bucket), y: d.avg_price })),
            borderColor: colour,
            backgroundColor: colour + '33',
            fill: false,
            tension: 0.3,
            pointRadius: isHourly ? 1 : 3,
            pointHoverRadius: isHourly ? 4 : 6,
            pointBackgroundColor: colour,
            borderWidth: isHourly ? 1.5 : 2,
        });
    }
    return { datasets, granularity, allData };
}

async function loadFuelTypes() {
    fuelTypes = await apiFetch('/fuel-types');
    for (const sel of document.querySelectorAll('[data-fuel-select]')) {
        const control = tomSelects[sel.id];
        const options = selectedFuelTypes(sel.dataset.fuelCodes).map(type => ({value: type.fuel_type_code, text: type.fuel_name || type.fuel_type_code}));
        if (!control) {
            sel.replaceChildren(...options.map(({value, text}) => {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = text;
                return option;
            }));
            sel.value = sel.dataset.defaultFuel || options[0]?.value || '';
            continue;
        }
        control.clear(true);
        control.clearOptions();
        control.addOptions(options);
        control.setValue(sel.dataset.defaultFuel ? [sel.dataset.defaultFuel] : [], true);
    }
}

function getFuelSelection(id) {
    const control = tomSelects[id];
    if (control) return control.getValue().join(',');
    return Array.from(document.getElementById(id)?.selectedOptions || []).map(option => option.value).filter(Boolean).join(',');
}

function setFuelSelection(id, value) {
    const values = Array.isArray(value) ? value : (value || '').split(',').filter(Boolean);
    const control = tomSelects[id];
    if (control) control.setValue(values, true);
    else for (const option of document.getElementById(id)?.options || []) option.selected = values.includes(option.value);
}

function selectedFuelTypes(value) {
    const selected = new Set((value || '').split(',').filter(Boolean));
    return fuelTypes.filter(type => !selected.size || selected.has(type.fuel_type_code));
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
        const ts = tomSelects[msId];
        if (!ts) return;
        regions.forEach(r => ts.addOption({ value: r, text: r }));
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
// Multi-select helpers (Tom Select)
// ---------------------------------------------------------------------------
const tomSelects = {};

function initTomSelects() {
    document.querySelectorAll('select[multiple]').forEach(sel => {
        if (tomSelects[sel.id]) return;
        tomSelects[sel.id] = new TomSelect('#' + sel.id, {
            plugins: ['remove_button'],
            placeholder: sel.getAttribute('placeholder') || 'All',
            hidePlaceholder: false,
            ...(sel.hasAttribute('data-fuel-select') ? {maxItems: null, closeAfterSelect: false} : {}),
        });
    });
}

function getMultiSelectValues(msId) {
    const ts = tomSelects[msId];
    if (!ts) return '';
    return ts.getValue().join(',');
}
function updateMultiSelectDisplay(msId) {
    // no-op — Tom Select manages its own display
}
function resetMultiSelect(msId) {
    const ts = tomSelects[msId];
    if (ts) ts.clear();
}
function setMultiSelectValues(msId, csv) {
    const ts = tomSelects[msId];
    if (!ts) return;
    ts.setValue(csv.split(','));
}
function getSelectedCategories() { return getMultiSelectValues('search-category-ms'); }

// ---------------------------------------------------------------------------
// Toast notifications
// ---------------------------------------------------------------------------
function showToast(message, type = 'success', duration = 2000) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => {
        el.classList.add('fade-out');
        el.addEventListener('animationend', () => el.remove());
    }, duration);
}

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
