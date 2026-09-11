const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {TextEncoder} = require('node:util');

function storage() {
    const values = new Map();
    return {
        getItem(key) { return values.has(key) ? values.get(key) : null; },
        setItem(key, value) { values.set(key, String(value)); },
        removeItem(key) { values.delete(key); },
    };
}

function loadAuth(fetchImpl = async () => { throw new Error('unexpected fetch'); }) {
    const elements = new Map();
    function element(id) {
        if (!elements.has(id)) {
            elements.set(id, {
                id,
                textContent: '',
                style: {},
                className: '',
                classList: {add() {}, remove() {}},
            });
        }
        return elements.get(id);
    }
    const location = {
        origin: 'https://staging-fuel.hoy.la',
        href: 'https://staging-fuel.hoy.la/',
        search: '',
        hash: '',
        assign() {},
        reload() {},
    };
    const context = {
        URL,
        URLSearchParams,
        Uint8Array,
        TextEncoder,
        crypto: webcrypto,
        btoa(value) { return Buffer.from(value, 'binary').toString('base64'); },
        atob(value) { return Buffer.from(value, 'base64').toString('binary'); },
        fetch: fetchImpl,
        console,
        document: {
            addEventListener() {},
            getElementById(id) { return element(id); },
            querySelectorAll() { return []; },
        },
        history: {replaceState() {}},
        localStorage: storage(),
        sessionStorage: storage(),
        location,
        window: {location, history: {replaceState() {}}},
        setTimeout() {},
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, '../web/static/js/shared.js'), 'utf8'),
        context,
    );
    vm.runInContext(`
        _cognitoDomain = 'https://guardian-fuel-tracker.auth.eu-north-1.amazoncognito.com';
        _cognitoClientId = 'test-client';
        _cognitoProvider = 'GuardianGoogle';
    `, context);
    return context;
}

test('PKCE challenge matches the RFC 7636 S256 example', async () => {
    const context = loadAuth();
    const challenge = await context.pkceChallenge(
        'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk',
    );
    assert.equal(challenge, 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
});

test('Google authorize URL uses code flow, PKCE, state, nonce and account selection', () => {
    const context = loadAuth();
    const url = new URL(context.buildOAuthAuthorizeUrl('state-value', 'nonce-value', 'challenge-value'));
    assert.equal(url.pathname, '/oauth2/authorize');
    assert.equal(url.searchParams.get('response_type'), 'code');
    assert.equal(url.searchParams.get('client_id'), 'test-client');
    assert.equal(url.searchParams.get('redirect_uri'), 'https://staging-fuel.hoy.la/');
    assert.equal(url.searchParams.get('scope'), 'openid email profile');
    assert.equal(url.searchParams.get('identity_provider'), 'GuardianGoogle');
    assert.equal(url.searchParams.get('state'), 'state-value');
    assert.equal(url.searchParams.get('nonce'), 'nonce-value');
    assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
    assert.equal(url.searchParams.get('code_challenge'), 'challenge-value');
    assert.equal(url.searchParams.get('prompt'), 'select_account');
});

test('silent session recovery requests prompt none', () => {
    const context = loadAuth();
    const url = new URL(context.buildOAuthAuthorizeUrl(
        'state-value', 'nonce-value', 'challenge-value', 'none',
    ));
    assert.equal(url.searchParams.get('prompt'), 'none');
});

test('authorization code exchange sends the verifier to Cognito token endpoint', async () => {
    let request;
    const context = loadAuth(async (url, options) => {
        request = {url, options};
        return {
            ok: true,
            async json() { return {id_token: 'token', refresh_token: 'refresh'}; },
        };
    });
    await context.exchangeOAuthCode('auth-code', 'verifier-value');
    assert.equal(
        request.url,
        'https://guardian-fuel-tracker.auth.eu-north-1.amazoncognito.com/oauth2/token',
    );
    assert.equal(request.options.method, 'POST');
    assert.equal(request.options.headers['Content-Type'], 'application/x-www-form-urlencoded');
    const body = new URLSearchParams(request.options.body);
    assert.equal(body.get('grant_type'), 'authorization_code');
    assert.equal(body.get('client_id'), 'test-client');
    assert.equal(body.get('code'), 'auth-code');
    assert.equal(body.get('code_verifier'), 'verifier-value');
    assert.equal(body.get('redirect_uri'), 'https://staging-fuel.hoy.la/');
});

test('Cognito tokens are kept in memory and never written to Web Storage', () => {
    const context = loadAuth();
    const payload = Buffer.from(JSON.stringify({
        exp: Math.floor(Date.now() / 1000) + 3600,
        email: 'reporter@guardian.co.uk',
    })).toString('base64url');
    const idToken = `header.${payload}.signature`;
    context.storeTokens({IdToken: idToken, RefreshToken: 'refresh-secret'}, false, 'oauth');
    assert.equal(context.localStorage.getItem('ff_id_token'), null);
    assert.equal(context.localStorage.getItem('ff_refresh_token'), null);
    assert.equal(context.localStorage.getItem('ff_auth_method'), 'oauth');
    assert.equal(vm.runInContext('_idToken', context), idToken);
    assert.equal(vm.runInContext('_refreshToken', context), 'refresh-secret');
});

test('interactive login controls are hidden while startup authentication resolves', () => {
    const html = fs.readFileSync(path.join(__dirname, '../web/static/index.html'), 'utf8');
    assert.match(html, /id="login-pending"[^>]*>Checking sign-in…<\/p>/);
    assert.match(html, /id="login-options" hidden/);
});

test('showLogin replaces the pending state with the configured login choices', () => {
    const context = loadAuth();
    context.showLogin('Please sign in');
    assert.equal(context.document.getElementById('login-pending').hidden, true);
    assert.equal(context.document.getElementById('login-options').hidden, false);
    assert.equal(context.document.getElementById('login-error').textContent, 'Please sign in');
});
