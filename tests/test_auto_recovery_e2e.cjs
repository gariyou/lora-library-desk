const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const app = process.env.LORA_TEST_ROOT || path.resolve(__dirname, '..');
const proc = spawn(process.env.PYTHON || 'python',
  ['-X', 'utf8', path.join(app, 'tests/recovery_e2e_server.py')], {windowsHide: true, env: {...process.env, E2E_HISTORY_IDENTITY: '1'}});
let context;
async function until(fn, timeout = 18000) {
  const end = Date.now() + timeout;
  do { const result = await fn(); if (result) return result; await new Promise(r => setTimeout(r, 150)); } while (Date.now() < end);
  throw Error('Timed out waiting for automatic import');
}
(async () => {
  const info = await new Promise((resolve, reject) => {
    let data = '';
    proc.stdout.on('data', b => { data += b; if (data.includes('\n')) resolve(JSON.parse(data.split('\n')[0])); });
    proc.on('error', reject); proc.stderr.on('data', b => process.stderr.write(b));
  });
  const base = `http://127.0.0.1:${info.port}`;
  const extension = path.join(app, 'chrome_extension');
  context = await chromium.launchPersistentContext('', {headless: true, channel: 'chromium', acceptDownloads: true,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]});
  const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
  await worker.evaluate(baseUrl => chrome.storage.sync.set({loraManagerBaseUrl: baseUrl}), base);
  const custom = path.join(info.root, 'CustomDownloads'); fs.mkdirSync(custom);
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Browser.setDownloadBehavior', {behavior: 'allow', downloadPath: custom, eventsEnabled: true});
  const bytes = fs.readFileSync(path.join(info.root, 'history-fixture.bin'));
  // Only fixture responses: the test sends no requests to the real distribution service.
  await context.route('https://civitai.red/**', r => r.fulfill({contentType: 'application/octet-stream',
    headers: {'Content-Disposition': 'attachment; filename="automatic.safetensors"'}, body: bytes}));
  const download = page.waitForEvent('download');
  await page.goto('https://civitai.red/api/download/models/3292162').catch(() => {});
  assert.equal(await (await download).failure(), null);
  const source = path.join(custom, 'automatic.safetensors');
  const target = path.join(info.checkpoints, 'automatic.safetensors');
  if (process.env.AUTO_EXPECT_BASELINE === '1') {
    await new Promise(r => setTimeout(r, 4000));
    assert.equal(fs.existsSync(source), true); assert.equal(fs.existsSync(target), false);
    console.log('BASELINE: completed Civitai download remains in custom Downloads without a pending wait or popup button');
    return;
  }
  await until(() => fs.existsSync(target));
  assert.deepEqual(fs.readFileSync(target), bytes); assert.equal(fs.existsSync(source), false);
  assert.equal(fs.existsSync(info.direct), true); // Never sweep unrelated files from Downloads automatically.
  const first = await (await fetch(base + '/api/lora/recover-downloads')).json();
  assert.equal(first.imported, 1, JSON.stringify(first));
  console.log('PASS: no popup or armed wait; actual Chrome completion auto-imports byte-identical renamed Anima checkpoint');

  // An unrelated pending wait must not block the next download or receive its metadata.
  const pending = await (await fetch(base + '/api/lora/await-download-import', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({metadata: {source_url: 'https://civitai.red/models/777',
      download_url: 'https://civitai.red/api/download/models/777', title: 'Unrelated pending', model_family_hint: 'lora'},
      expected_filename: 'unrelated.safetensors', auto_triggered: true})})).json();
  assert.ok(pending.pending);
  await worker.evaluate(() => chrome.storage.sync.set({loraManagerBaseUrl: 'http://127.0.0.1:1'}));
  const second = page.waitForEvent('download');
  await page.goto('https://civitai.red/api/download/models/3292162').catch(() => {});
  assert.equal(await (await second).failure(), null);
  await until(() => worker.evaluate(async () => Object.keys((await chrome.storage.local.get('loraManagerAutomaticRecovery')).loraManagerAutomaticRecovery?.queue || {}).length > 0));
  const filenames = fs.readdirSync(custom).filter(n => n.endsWith('.safetensors'));
  assert.equal(filenames.length, 1);
  await worker.evaluate(baseUrl => chrome.storage.sync.set({loraManagerBaseUrl: baseUrl}), base);
  // The same scheduler that the one-minute alarm calls, with persisted queue state.
  await worker.evaluate(() => scheduleAutomaticRecovery());
  const target2 = path.join(info.checkpoints, filenames[0]);
  await until(() => fs.existsSync(target2));
  assert.deepEqual(fs.readFileSync(target2), bytes);
  const after = await (await fetch(base + '/api/lora/pending-download')).json();
  assert.equal(after.pending.created_epoch, pending.pending.created_epoch);
  await worker.evaluate(() => scheduleAutomaticRecovery());
  assert.equal(fs.readdirSync(info.checkpoints).filter(n => n.endsWith('.safetensors')).length, 2);
  assert.equal(fs.existsSync(info.direct), true);
  console.log('PASS: app-offline queue retries; unrelated pending wait preserved; repeated sweep creates no duplicates or unrelated moves');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(async () => {
  if (context) await context.close(); proc.stdin.end('\n');
});
