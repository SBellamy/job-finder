const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function dashboard() {
  const elements = new Map();
  const storage = new Map();
  const create = () => ({
    value: '', checked: false, hidden: false, disabled: false, textContent: '', type: 'select-one',
    innerHTML: '', append() {}, replaceChildren() {}, querySelector() { return { disabled: false }; },
  });
  const context = vm.createContext({
    URL, Date, Map, Set, Number, Object, String, Math, console, setTimeout, clearTimeout,
    document: {
      querySelector(id) { if (!elements.has(id)) elements.set(id, create()); return elements.get(id); },
      createElement: create,
    },
    localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
  });
  const source = readFileSync(resolve(__dirname, '../frontend/app.js'), 'utf8').replace(/\ninit\(\)\.catch\([\s\S]*$/, '');
  vm.runInContext(source, context);
  vm.runInContext(`state.apiBaseUrl = 'http://localhost:3000'; renderJobs = () => {}; renderDigest = () => {}; renderSourceQuality = () => {}; renderFreshness = () => {};`, context);
  return { context, elements, run: (code) => vm.runInContext(code, context) };
}

test('numeric provider dates sort in the same units as ISO dates', () => {
  const { run } = dashboard();
  assert.equal(run(`parseSortDate('1770000000')`), run(`parseSortDate('1770000000000')`));
  assert.equal(run(`parseSortDate('1770000000000')`), run(`parseSortDate(new Date(1770000000000).toISOString())`));
});

test('export uses the same search, source, visit and eligibility filters as the feed', () => {
  const { run } = dashboard();
  run(`elements.sourceFilter.value = 'lever'; elements.searchInput.value = 'Terraform'; elements.confirmedOnlyToggle.checked = true; state.queryFoundAfter = '2026-11-13T00:00:00Z';`);
  const jobs = new URL(run(`buildJobsUrl().toString()`));
  const exported = new URL(run(`buildExportUrl().toString()`));
  for (const key of ['search', 'source_type', 'found_after', 'eligible_only', 'confirmed_us_only']) {
    assert.equal(jobs.searchParams.get(key), exported.searchParams.get(key));
  }
  assert.equal(exported.searchParams.get('source_type'), 'lever');
  assert.equal(exported.searchParams.get('search'), 'Terraform');
});

test('reviewing a new job removes it from the new feed and shortlist immediately', () => {
  const { run } = dashboard();
  run(`state.jobs = [{job_id:'one', status:'new', title:'DevOps', score:100}]; state.digest = {items:[{job_id:'one'}]}; elements.onlyNewToggle.checked = true; updateJobInState('one', {status:'saved'});`);
  assert.equal(run('state.filteredJobs.length'), 0);
  assert.equal(run('state.digest.count'), 0);
  assert.equal(run('state.jobs[0].status'), 'saved');
});

test('older network responses cannot replace newer filter results', async () => {
  const { context, run } = dashboard();
  run(`updateSummary = () => {}; updateSourceFilter = () => {}; applyClientFilters = () => {}; updateLoadedStatus = () => {};`);
  const requests = [];
  context.fetch = () => new Promise((resolve) => requests.push(resolve));
  const first = run('fetchJobs()');
  const second = run('fetchJobs()');
  requests[1]({ ok: true, json: async () => ({ items: [{ job_id: 'latest' }] }) });
  await second;
  requests[0]({ ok: true, json: async () => ({ items: [{ job_id: 'obsolete' }] }) });
  await first;
  assert.equal(run('state.jobs[0].job_id'), 'latest');
});

test('one failed insight does not erase successful freshness and source results', async () => {
  const { context, run } = dashboard();
  context.fetch = async (url) => {
    if (url.pathname === '/digest') throw new Error('digest unavailable');
    return { ok: true, json: async () => url.pathname === '/sources' ? { items: [{ source_id: 'one' }] } : { ok: true } };
  };
  await run('fetchInsights()');
  assert.equal(run('state.freshness.ok'), true);
  assert.equal(run('state.sources[0].source_id'), 'one');
  assert.equal(run('state.digest'), null);
});


test('availability filters share a stable cutoff across cursor pages and exports', () => {
  const { run } = dashboard();
  run(`elements.availabilityFilter.value = '7'; state.querySeenAfter = '2026-11-08T18:00:00Z';`);
  const first = new URL(run('buildJobsUrl().toString()'));
  const next = new URL(run("buildJobsUrl('cursor').toString()"));
  const exported = new URL(run('buildExportUrl().toString()'));
  assert.equal(first.searchParams.get('available_only'), 'true');
  for (const url of [next, exported]) assert.equal(url.searchParams.get('seen_after'), first.searchParams.get('seen_after'));
  run(`elements.statusFilter.value = 'saved';`);
  assert.equal(run('seenAfter()'), null);
  assert.equal(new URL(run('buildJobsUrl().toString()')).searchParams.get('available_only'), 'false');
  assert.equal(new URL(run('buildJobsUrl().toString()')).searchParams.get('seen_after'), null);
  assert.equal(new URL(run('buildJobsUrl().toString()')).searchParams.get('eligible_only'), 'false');
  assert.equal(new URL(run('buildJobsUrl().toString()')).searchParams.get('min_score'), null);
  assert.equal(new URL(run('buildJobsUrl().toString()')).searchParams.get('confirmed_us_only'), 'false');
});
