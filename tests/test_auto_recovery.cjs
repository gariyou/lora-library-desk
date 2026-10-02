const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm'), fs = require('node:fs'), path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../chrome_extension/auto_recovery.js'), 'utf8');
const stateKey = 'loraManagerAutomaticRecovery';
function item(id = 1, overrides = {}) {
  return {id, state: 'complete', exists: true, filename: `C:/CustomDownloads/model-${id}.safetensors`,
    startTime: new Date(Date.now() - 2 * 86400000).toISOString(), endTime: new Date().toISOString(),
    url: `https://civitai.red/api/download/models/${id}`, finalUrl: 'https://b2.civitai.com/file/model', referrer: '', ...overrides};
}
function fixture({storage = {}, items = [item()], offline = false, busy = false, outcomes = [], longRunning = false} = {}) {
  const changed = [], alarms = [], searches = [], posts = [], order = [];
  let clock = Date.now(), job = busy ? {status: 'running', id: 'manual', results: []} : {status: 'idle'}, seq = 0;
  const context = vm.createContext({URL, console: {warn() {}}, Date: class extends Date {static now() {return clock;}},
    setTimeout: fn => setImmediate(fn), MODEL_FILE_PATTERN: /\.(safetensors|ckpt|pt|pth|bin|json|zip)$/i,
    RECONCILE_ALARM: 'pending', activeJobId: '', toErrorMessage: String,
    isCivitaiHost: host => ['civitai.com', 'civitai.red'].some(suffix => host === suffix || host.endsWith('.' + suffix)),
    loadStoredBaseUrl: async () => 'http://127.0.0.1:8787',
    reconcileCompletedDownloads: async () => { order.push('pending'); if (offline) throw Error('offline'); },
    getJson: async () => { if (offline) throw Error('offline'); return structuredClone(job); },
    postJson: async (url, body) => {
      if (offline) throw Error('offline'); order.push('recovery'); posts.push(body);
      if (busy) return {status: 'running', id: 'manual', already_running: true};
      const results = body.downloads.map(row => ({source_path: row.source_path, status: outcomes.shift() || 'imported'}));
      job = {id: String(++seq), status: longRunning ? 'running' : 'complete', results};
      return structuredClone(job);
    },
    chrome: {storage: {local: {get: async key => ({[key]: structuredClone(storage[key])}),
      set: async value => Object.assign(storage, structuredClone(value))}},
      downloads: {onChanged: {addListener: fn => changed.push(fn)}, search: async query => {
        searches.push(query);
        return structuredClone(query.id !== undefined ? items.filter(x => x.id === query.id) :
          items.filter(x => Date.parse(x.endTime) > Date.parse(query.endedAfter)));
      }}, alarms: {onAlarm: {addListener: fn => alarms.push(fn)}}}
  });
  vm.runInContext(code, context);
  const flush = () => vm.runInContext('automaticRecoveryChain', context);
  const run = () => vm.runInContext('scheduleAutomaticRecovery()', context);
  return {context, storage, posts, searches, changed, alarms, order, flush, run,
    setItems: v => {items = v;}, setOffline: v => {offline = v;},
    setJob: v => {job = v;}, setBusy: v => {busy = v;}, advance: ms => {clock += ms;}};
}
test('startup catches recent completion of a two-day download without wait or popup', async () => {
  const f = fixture(); await f.flush();
  assert.equal(f.posts.length, 1); assert.equal(f.posts[0].downloads.length, 1);
  assert.equal(f.posts[0].scan_downloads_dir, false); assert.deepEqual(f.order, ['pending', 'recovery']);
  assert.ok(f.searches[0].endedAfter); assert.equal(f.searches[0].startedAfter, undefined);
  assert.equal(Object.keys(f.storage[stateKey].queue).length, 0);
  // Chrome can return stale exists=true; a saved completed key still prevents repeats.
  await f.run(); assert.equal(f.posts.length, 1);
});
test('unrelated, incomplete, missing, malicious-host and unsupported files are excluded', async () => {
  const f = fixture({items: [item(1, {url: 'https://civitai.red.evil.test/a', finalUrl: ''}),
    item(2, {state: 'in_progress'}), item(3, {exists: false}), item(4, {filename: 'C:/Downloads/file.exe'}),
    item(5, {url: 'https://example.test/a.json', finalUrl: '', filename: 'C:/Downloads/config.json'})]});
  await f.flush(); assert.equal(f.posts.length, 0);
});
test('completion event imports from a Civitai referrer; simultaneous events are serialized', async () => {
  const f = fixture({items: []}); await f.flush();
  f.setItems([item(9, {url: 'https://cdn.example.test/asset', finalUrl: '', referrer: 'https://civitai.com/models/9'})]);
  f.changed[0]({id: 9, state: {current: 'complete'}});
  f.changed[0]({id: 9, state: {current: 'complete'}});
  await f.flush(); assert.equal(f.posts.length, 1);
  assert.equal(f.posts[0].downloads[0].referrer, 'https://civitai.com/models/9');
});
test('offline queue survives worker restart beyond the history lookback and alarm retries', async () => {
  const old = item(); const f = fixture({offline: true, items: [old]}); await f.flush();
  assert.equal(f.posts.length, 0); assert.equal(Object.keys(f.storage[stateKey].queue).length, 1);
  const g = fixture({storage: f.storage, items: [old], offline: true}); await g.flush();
  g.advance(3 * 86400000); g.setOffline(false); g.alarms[0]({name: 'pending'}); await g.flush();
  assert.equal(g.posts.length, 1); assert.equal(Object.keys(g.storage[stateKey].queue).length, 0);
});
test('manual recovery in progress retains the queue and resumes after completion', async () => {
  const f = fixture({busy: true}); await f.flush(); assert.equal(f.posts.length, 0);
  assert.equal(Object.keys(f.storage[stateKey].queue).length, 1);
  f.setBusy(false); f.setJob({id: 'manual', status: 'complete', results: []}); await f.run();
  assert.equal(f.posts.length, 1);
});
test('long server jobs survive worker restart; no second POST while running', async () => {
  const rows = [item()]; const f = fixture({longRunning: true, items: rows}); await f.flush();
  const active = f.storage[stateKey].active; assert.equal(f.posts.length, 1); assert.ok(active);
  const g = fixture({storage: f.storage, busy: true, items: rows}); await g.flush(); assert.equal(g.posts.length, 0);
  g.setBusy(false); g.setJob({id: active.id, status: 'complete', results: [{source_path: item().filename, status: 'imported'}]});
  await g.run(); assert.equal(g.posts.length, 0); assert.equal(Object.keys(g.storage[stateKey].queue).length, 0);
});
test('failed metadata/file checks back off and retry; successful import is never repeated', async () => {
  const f = fixture({outcomes: ['skipped', 'failed', 'imported']}); await f.flush();
  await f.run(); assert.equal(f.posts.length, 1);
  f.advance(60001); await f.run(); assert.equal(f.posts.length, 2);
  f.advance(60001); await f.run(); assert.equal(f.posts.length, 2);
  f.advance(60001); await f.run(); assert.equal(f.posts.length, 3);
  await f.run(); assert.equal(f.posts.length, 3);
});
test('server restart after submission safely retries; removed sources leave the queue', async () => {
  const rows = [item()]; const f = fixture({longRunning: true, items: rows}); await f.flush();
  f.setJob({status: 'idle'}); f.setItems([]); await f.run();
  assert.equal(f.posts.length, 1); assert.equal(Object.keys(f.storage[stateKey].queue).length, 0);
});
test('a reused download ID with a different path is never submitted from stale queue data', async () => {
  const f = fixture({offline: true}); await f.flush();
  f.setItems([item(1, {filename: 'C:/Downloads/ordinary.json', url: 'https://example.test/file', finalUrl: ''})]);
  f.setOffline(false); await f.run();
  assert.equal(f.posts.length, 0); assert.equal(Object.keys(f.storage[stateKey].queue).length, 0);
});

