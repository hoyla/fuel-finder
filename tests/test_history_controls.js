const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function loadControls() {
    const context = {document: {addEventListener() {}}};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/static/js/sensitivity.js'), 'utf8'), context);
    return context;
}

function loadFuelControls() {
    const context = {document: {addEventListener() {}, querySelectorAll() { return []; }}};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/static/js/shared.js'), 'utf8'), context);
    vm.runInContext(`fuelTypes = [
        {fuel_type_code: 'E10', fuel_name: 'Unleaded'},
        {fuel_type_code: 'B7_STANDARD', fuel_name: 'Diesel'},
        {fuel_type_code: 'HVO', fuel_name: 'HVO'},
    ];`, context);
    return context;
}

function loadTrendUrlControls(url = 'https://fuel.hoy.la/#trends') {
    const elements = new Map();
    const element = (id, value = '') => {
        if (!elements.has(id)) elements.set(id, {id, value, checked: false, disabled: false, style: {}});
        return elements.get(id);
    };
    for (const [id, value] of [
        ['trend-range', '30'], ['trend-start', ''], ['trend-end', ''],
        ['trend-granularity', 'daily'], ['trend-sensitivity-age', 'none'],
    ]) element(id, value);
    element('trend-start-ctl');
    element('trend-end-ctl');
    element('trend-sensitivity-compare');

    const location = new URL(url);
    const selected = {fuel: '', 'trend-country-ms': '', 'trend-region-ms': '', 'trend-rural-urban-ms': ''};
    const context = {
        URL, location, selected, reconstructedHistoryEnabled: true,
        document: {
            addEventListener() {},
            getElementById(id) { return element(id); },
        },
        history: {
            state: {},
            replaceState(state, _title, next) {
                this.state = state;
                location.href = new URL(String(next), location.href).href;
            },
        },
        navigator: {clipboard: {async writeText() {}}},
        fuelTypes: [
            {fuel_type_code: 'E10'}, {fuel_type_code: 'E5'},
            {fuel_type_code: 'B7_STANDARD'},
        ],
        tomSelects: {
            'trend-country-ms': {options: {England: {}, Scotland: {}}},
            'trend-region-ms': {options: {London: {}, 'North East': {}}},
            'trend-rural-urban-ms': {options: {'Large Urban Areas': {}, 'Remote Rural': {}}},
        },
        setFuelSelection(_id, value) { selected.fuel = Array.isArray(value) ? value.join(',') : value; },
        setMultiSelectValues(id, value) { selected[id] = value; },
        resetMultiSelect(id) { selected[id] = ''; },
        updateHistoryFilterState() {},
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/static/js/trends.js'), 'utf8'), context);
    return context;
}

function loadRouter(url) {
    const location = new URL(url);
    const panel = {classList: {add() {}, remove() {}}};
    const order = [];
    const context = {
        location, order,
        document: {
            body: {classList: {toggle() {}}},
            querySelectorAll() { return []; },
            querySelector() { return null; },
            getElementById(id) { return id === 'panel-trends' ? panel : null; },
        },
        window: {addEventListener() {}},
        history: {
            state: {},
            pushState(state, _title, next) { this.state = state; location.href = new URL(next, location.href).href; },
            replaceState(state, _title, next) { this.state = state; location.href = new URL(next, location.href).href; },
        },
        restoreTrendUrl() { order.push('restore'); },
        loadTrends() { order.push('load'); },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/static/js/router.js'), 'utf8'), context);
    return context;
}

function loadWeightedTrendControls({fail = false} = {}) {
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {
            id, value: '', checked: false, disabled: false, textContent: '', innerHTML: '', title: '', style: {},
            selectedOptions: [{textContent: 'Last 7 days'}], getContext() { return this; },
        });
        return elements.get(id);
    };
    element('trend-sensitivity-age').value = '7';
    element('trend-sensitivity-compare').checked = true;
    const remembered = [];
    const requested = {
        fuel: 'E10', range: '30', granularity: 'daily', start: '2026-08-12', end: '2026-09-10',
        age: '7', country: '', region: '', 'rural-urban': '',
    };
    const context = {
        AbortController, remembered, requested, reconstructedHistoryEnabled: true, _userRole: 'admin', charts: {},
        document: {
            addEventListener() {},
            getElementById(id) { return element(id); },
            querySelector() { return {open: false}; },
        },
        selectedFuelTypes() { return [{fuel_type_code: 'E10', fuel_name: 'Unleaded'}]; },
        fuelColour() { return '#123456'; },
        escHtml(value) { return String(value); },
        rememberTrendUrl(filters, compare) { remembered.push({filters, compare}); },
        async apiFetch() {
            if (fail) throw new Error('request failed');
            return {
                method: 'last_reported_station_weighted', granularity: 'daily', age_limit: '7',
                range_start: '2026-08-12T00:00:00+00:00', range_capped: false, groups: [],
                data: [{bucket: '2026-08-12', avg_price: 150, age_price: 151, stations: 100,
                    included_stations: 90, excluded_stations: 10, reference_hours: 2400, included_hours: 2100,
                    hampel_avg_price_changed: false, hampel_age_price_changed: false}],
            };
        },
        Chart: function(_canvas, config) {
            this.data = config.data;
            this.options = config.options;
            this.destroy = () => {};
            this.update = () => {};
        },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/static/js/sensitivity.js'), 'utf8'), context);
    context.historyFilters = () => JSON.parse(JSON.stringify(requested));
    return context;
}

test('fuel subsets resolve in catalogue order and clearing means all fuels', () => {
    const controls = loadFuelControls();
    const codes = selection => Array.from(controls.selectedFuelTypes(selection), type => type.fuel_type_code);
    assert.deepEqual(codes('B7_STANDARD,E10'), ['E10', 'B7_STANDARD']);
    assert.deepEqual(codes(''), ['E10', 'B7_STANDARD', 'HVO']);
    assert.deepEqual(codes('E10,E10'), ['E10']);
});

test('setting and restoring fuel chips uses the Tom Select API silently', () => {
    const controls = loadFuelControls();
    vm.runInContext(`tomSelects['test-fuel'] = {
        values: [], getValue() { return this.values; },
        setValue(values, silent) { this.values = values; this.silent = silent; }
    };`, controls);
    controls.setFuelSelection('test-fuel', 'E10,B7_STANDARD');
    assert.equal(controls.getFuelSelection('test-fuel'), 'E10,B7_STANDARD');
    assert.equal(vm.runInContext("tomSelects['test-fuel'].silent", controls), true);
    controls.setFuelSelection('test-fuel', '');
    assert.equal(controls.getFuelSelection('test-fuel'), '');
});

test('fuel loading defaults Dashboard to E10 while keeping other menus multi-select', async () => {
    const controls = loadFuelControls();
    const dashboard = {
        id: 'dashboard-fuel', dataset: {defaultFuel: 'E10'},
        replaceChildren(...options) { this.options = options; },
        get selectedOptions() { return this.options.filter(option => option.value === this.value); },
    };
    controls.document.querySelectorAll = () => [dashboard, {id: 'trend-fuel', dataset: {}}];
    controls.document.getElementById = () => dashboard;
    controls.document.createElement = () => ({});
    controls.apiFetch = async () => [
        {fuel_type_code: 'B7_STANDARD', fuel_name: 'Diesel'},
        {fuel_type_code: 'E10', fuel_name: 'Unleaded'},
    ];
    vm.runInContext(`tomSelects['trend-fuel'] = {
        clear() {}, clearOptions() {}, addOptions(options) { this.options = options; },
        setValue(values) { this.values = values; }, getValue() { return this.values; }
    };`, controls);
    await controls.loadFuelTypes();
    assert.equal(dashboard.value, 'E10');
    assert.deepEqual(dashboard.options.map(option => option.textContent), ['Diesel', 'Unleaded']);
    assert.equal(controls.getFuelSelection('dashboard-fuel'), 'E10');
    dashboard.value = 'B7_STANDARD';
    assert.equal(controls.getFuelSelection('dashboard-fuel'), 'B7_STANDARD');
    assert.equal(controls.getFuelSelection('trend-fuel'), '');
    controls.setFuelSelection('trend-fuel', 'E10,B7_STANDARD');
    assert.equal(controls.getFuelSelection('trend-fuel'), 'E10,B7_STANDARD');
});

test('Dashboard trend uses the available daily range and shared fuel styling', async () => {
    const controls = loadFuelControls();
    const elements = new Map();
    const requests = [];
    controls.document.getElementById = id => {
        if (!elements.has(id)) elements.set(id, {style: {}, getContext() { return this; }, addEventListener() {}});
        return elements.get(id);
    };
    controls.getFuelSelection = () => 'E10';
    controls.Chart = function(element, config) {
        this.data = config.data;
        this.options = config.options;
    };
    controls.apiFetch = async url => {
        requests.push(new URL(url, 'http://localhost'));
        if (url.startsWith('/prices/history?')) return {data: [
            {bucket: '2026-02-07', avg_price: 130},
            {bucket: '2026-02-08', avg_price: null},
            {bucket: '2026-02-09', avg_price: 131},
        ]};
        return [{avg_price: 130, station_count: 1, region: 'North', forecourt_type: 'Independent',
            brand_name: 'Test', unified_label: 'Rural', rural_urban_values: ['Rural'], admin_district: 'North'}];
    };
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/static/js/dashboard.js'), 'utf8'), controls);
    await controls.loadDashboardCharts();
    const query = requests.find(url => url.pathname === '/prices/history').searchParams;
    assert.equal(query.get('end_date'), new Date().toISOString().slice(0, 10));
    assert.equal(query.get('granularity'), 'daily');
    assert.equal(query.has('days'), false);
    assert.equal(query.has('start_date'), false);
    const chart = vm.runInContext("charts['chart-dashboard-trend']", controls);
    const series = chart.data.datasets[0];
    assert.equal(series.borderColor, controls.fuelColour('E10'));
    assert.equal(series.borderWidth, 2.5);
    assert.equal(series.tension, 0);
    assert.equal(series.fill, false);
    assert.equal(series.spanGaps, false);
    assert.equal(series.data[1].y, null);
    assert.equal(chart.data.labels[0], '2026-02-07');
    assert.equal(chart.options.maintainAspectRatio, false);
    assert.equal(chart.options.scales.y.title.text, 'Pence per litre');
});

test('legacy multi-fuel requests and data remain separate for the selected subset', async () => {
    const controls = loadFuelControls();
    const requests = [];
    controls.apiFetch = async url => {
        requests.push(url);
        return {granularity: 'daily', data: [{bucket: '2026-09-01', avg_price: url === 'E10' ? 150 : 180}]};
    };
    const result = await controls.fetchAllFuelTrends(code => code, false, 'E10,B7_STANDARD');
    assert.deepEqual(requests, ['E10', 'B7_STANDARD']);
    assert.deepEqual(Array.from(result.datasets, dataset => dataset.data[0].y), [150, 180]);
    assert.deepEqual(Array.from(result.datasets, dataset => dataset.label), ['Unleaded', 'Diesel']);
});

test('search download follows pagination rather than truncating multi-fuel results', async () => {
    const controls = loadFuelControls();
    controls.URL = URL;
    controls.location = {origin: 'http://localhost'};
    controls.document.getElementById = () => ({});
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/static/js/search.js'), 'utf8'), controls);
    controls.buildSearchUrl = () => '/prices/search?fuel_type=E10,B7_STANDARD&limit=10000&offset=0';
    controls.getSearchDlScope = () => 'all';
    const offsets = [];
    controls.apiFetch = async url => {
        const query = new URL(url, 'http://localhost').searchParams;
        offsets.push(Number(query.get('offset')));
        assert.equal(query.get('fuel_type'), 'E10,B7_STANDARD');
        return {total: 3, results: offsets.length === 1 ? [{node_id: 'one'}, {node_id: 'two'}] : [{node_id: 'three'}]};
    };
    let exported;
    controls.downloadFile = rows => { exported = rows; };
    await controls.downloadSearchData('csv');
    assert.deepEqual(offsets, [0, 2]);
    assert.equal(exported.length, 3);
});

test('daily and hourly views request only the chosen resolution for each fuel', async () => {
    const elements = new Map();
    const requests = [];
    const controls = {
        document: {addEventListener() {}, getElementById(id) {
            if (!elements.has(id)) elements.set(id, {value: '', style: {}});
            return elements.get(id);
        }},
        reconstructedHistoryEnabled: true,
        charts: {}, canEdit: () => true,
        getFuelSelection: () => 'E10,B7_STANDARD',
        getMultiSelectValues: () => '',
        selectedFuelTypes: () => [{fuel_type_code: 'E10'}, {fuel_type_code: 'B7_STANDARD'}],
        loadWeightedTrend: async (scope, selection, buildUrl) => {
            for (const fuel of selection.split(',')) requests.push(new URL(buildUrl(fuel), 'http://localhost'));
        },
    };
    vm.createContext(controls);
    for (const file of ['trends.js', 'search.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/static/js', file), 'utf8'), controls);
    vm.runInContext("stationTrendState = {mode: 'single', nodeId: 'station-one'}", controls);
    for (const granularity of ['daily', 'hourly']) {
        controls.document.getElementById('trend-granularity').value = granularity;
        controls.document.getElementById('st-granularity').value = granularity;
        await controls.loadTrends();
        await controls.loadStationTrend();
        assert.equal(requests.length, 4);
        assert.ok(requests.every(url => url.searchParams.get('granularity') === granularity));
        assert.deepEqual(requests.map(url => url.searchParams.get('fuel_type')), ['E10', 'B7_STANDARD', 'E10', 'B7_STANDARD']);
        requests.length = 0;
    }
});

test('unchanged filters are clean and edited filters are pending', () => {
    const controls = loadControls();
    const applied = {fuel: 'E10', age: 'none'};
    assert.equal(controls.historyFilterMessage({...applied}, {applied}), '');
    assert.equal(controls.historyFilterMessage({...applied, age: '7'}, {applied}), 'Unapplied changes');
    assert.equal(controls.historyFilterMessage(applied, {}), 'Unapplied changes');
});

test('edits made during an in-flight request stay unapplied', () => {
    const controls = loadControls();
    const applied = {fuel: 'E10', age: 'none'};
    const requested = {fuel: 'E10', age: '7'};
    const draft = {fuel: 'B7_STANDARD', age: '7'};
    assert.equal(controls.historyFilterMessage(requested, {applied, requested, loading: true}), 'Applying filters...');
    assert.equal(controls.historyFilterMessage(draft, {applied, requested, loading: true}), 'Applying earlier filters; unapplied changes');
    assert.equal(controls.historyFilterMessage(draft, {applied: requested, loading: false}), 'Unapplied changes');
});

test('reverting a draft to displayed filters clears the pending state', () => {
    const controls = loadControls();
    const applied = {fuel: 'E10', age: 'none', start: '2026-08-11', end: '2026-09-09'};
    assert.equal(controls.historyFiltersDiffer({...applied, start: '2026-08-12'}, applied), true);
    assert.equal(controls.historyFiltersDiffer({...applied}, applied), false);
});

test('selected line is solid and the optional reference is light and dashed', () => {
    const controls = loadControls();
    const results = [{name: 'Unleaded', colour: '#123456', response: {age_limit: '7', data: [
        {bucket: '2026-08-11', age_price: 151, avg_price: 150, included_stations: 50, stations: 100},
    ]}}];
    const selected = controls.historyDatasets(results, false);
    assert.equal(selected.length, 1);
    assert.equal(selected[0].data[0].y, 151);
    const compared = controls.historyDatasets(results, true);
    assert.equal(compared.length, 2);
    assert.equal(compared[1].data[0].y, 150);
    assert.equal(compared[1].borderColor, '#12345680');
    assert.ok(compared[1].borderWidth < compared[0].borderWidth);
    assert.ok(compared[1].borderDash.length > 0);
    const labels = controls.historyLegendLabels({data: {datasets: compared}, isDatasetVisible: () => true});
    assert.equal(labels[0].pointStyle, 'line');
    assert.equal(labels[0].strokeStyle, compared[0].borderColor);
    assert.equal(labels[1].pointStyle, 'line');
    assert.equal(labels[1].strokeStyle, compared[1].borderColor);
    assert.equal(labels[1].lineDash, compared[1].borderDash);
    results[0].response.age_limit = 'none';
    assert.equal(controls.historyDatasets(results, true).length, 1);
});

test('rolling trend links store the preset and omit absolute dates', () => {
    const controls = loadTrendUrlControls();
    controls.rememberTrendUrl({
        fuel: 'E10,E5', range: '30', start: '2026-08-12', end: '2026-09-10',
        granularity: 'daily', age: '7', country: 'England', region: 'London',
        'rural-urban': 'Large Urban Areas',
    }, true);
    const url = new URL(controls.location.href);
    assert.equal(url.hash, '#trends');
    assert.equal(url.searchParams.get('trendFuel'), 'E10,E5');
    assert.equal(url.searchParams.get('trendRange'), '30');
    assert.equal(url.searchParams.has('trendStart'), false);
    assert.equal(url.searchParams.has('trendEnd'), false);
    assert.equal(url.searchParams.get('trendGranularity'), 'daily');
    assert.equal(url.searchParams.get('trendAge'), '7');
    assert.equal(url.searchParams.get('trendCountry'), 'England');
    assert.equal(url.searchParams.get('trendRegion'), 'London');
    assert.equal(url.searchParams.get('trendRuralUrban'), 'Large Urban Areas');
    assert.equal(url.searchParams.get('trendCompare'), '1');
});

test('custom trend links retain exact dates', () => {
    const controls = loadTrendUrlControls();
    controls.rememberTrendUrl({
        fuel: 'B7_STANDARD', range: 'custom', start: '2026-03-01', end: '2026-04-15',
        granularity: 'hourly', age: 'none', country: '', region: '', 'rural-urban': '',
    }, true);
    const parameters = new URL(controls.location.href).searchParams;
    assert.equal(parameters.get('trendRange'), 'custom');
    assert.equal(parameters.get('trendStart'), '2026-03-01');
    assert.equal(parameters.get('trendEnd'), '2026-04-15');
    assert.equal(parameters.has('trendCompare'), false);
});

test('rolling trend links restore current dates and validated selections', () => {
    const url = new URL('https://fuel.hoy.la/#trends');
    for (const [name, value] of Object.entries({
        trendFuel: 'E10,NOT_A_FUEL', trendRange: '30', trendStart: '2020-01-01', trendEnd: '2020-01-30',
        trendGranularity: 'hourly', trendAge: '14', trendCountry: 'England,Atlantis',
        trendRegion: 'London,Nowhere', trendRuralUrban: 'Remote Rural', trendCompare: '1',
    })) url.searchParams.set(name, value);
    const controls = loadTrendUrlControls(url.href);
    assert.equal(controls.restoreTrendUrl(), true);
    assert.equal(controls.selected.fuel, 'E10');
    assert.equal(controls.selected['trend-country-ms'], 'England');
    assert.equal(controls.selected['trend-region-ms'], 'London');
    assert.equal(controls.selected['trend-rural-urban-ms'], 'Remote Rural');
    assert.equal(controls.document.getElementById('trend-range').value, '30');
    assert.equal(controls.document.getElementById('trend-end').value, new Date().toISOString().slice(0, 10));
    assert.notEqual(controls.document.getElementById('trend-start').value, '2020-01-01');
    assert.equal(controls.document.getElementById('trend-granularity').value, 'hourly');
    assert.equal(controls.document.getElementById('trend-sensitivity-age').value, '14');
    assert.equal(controls.document.getElementById('trend-sensitivity-compare').checked, true);
});

test('custom trend links restore exact dates and reject invalid ranges', () => {
    const exact = loadTrendUrlControls('https://fuel.hoy.la/?trendRange=custom&trendStart=2026-03-01&trendEnd=2026-04-15#trends');
    exact.restoreTrendUrl();
    assert.equal(exact.document.getElementById('trend-range').value, 'custom');
    assert.equal(exact.document.getElementById('trend-start').value, '2026-03-01');
    assert.equal(exact.document.getElementById('trend-end').value, '2026-04-15');

    const invalid = loadTrendUrlControls('https://fuel.hoy.la/?trendRange=custom&trendStart=2026-04-15&trendEnd=2026-03-01#trends');
    invalid.restoreTrendUrl();
    assert.equal(invalid.document.getElementById('trend-range').value, '30');
});

test('direct trend links restore criteria before loading and retain their query string', () => {
    const controls = loadRouter('https://fuel.hoy.la/?trendFuel=E10&trendRange=30#trends');
    controls.applyInitialHash();
    assert.deepEqual(controls.order, ['restore', 'load']);
    assert.equal(controls.location.searchParams.get('trendFuel'), 'E10');
    assert.equal(controls.location.searchParams.get('trendRange'), '30');
    assert.equal(controls.location.hash, '#trends');
});

test('trend URL changes only after a successful chart request', async () => {
    const successful = loadWeightedTrendControls();
    await successful.loadWeightedTrend('trend', 'E10', fuel => '/prices/history?fuel_type=' + fuel);
    assert.equal(successful.remembered.length, 1);
    assert.deepEqual(successful.remembered[0].filters, successful.requested);
    assert.equal(successful.remembered[0].compare, true);

    const failed = loadWeightedTrendControls({fail: true});
    await failed.loadWeightedTrend('trend', 'E10', fuel => '/prices/history?fuel_type=' + fuel);
    assert.equal(failed.remembered.length, 0);
});
