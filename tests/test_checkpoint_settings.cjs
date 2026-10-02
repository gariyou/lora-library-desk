const { chromium, expect } = require('playwright/test');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const app = path.resolve(__dirname, '..');
const artifactDir = path.resolve(process.env.CHECKPOINT_ARTIFACT_DIR || path.join(os.tmpdir(), 'checkpoint-settings-artifacts'));
fs.mkdirSync(artifactDir, { recursive: true });
const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'checkpoint-settings-e2e-'));
const python = process.env.PYTHON || 'python';
let proc, browser, base;
const settings = {
  recommended_steps: '20〜30 / 高品質なら40',
  recommended_sampler: 'DPM++ 2M / Euler a',
  recommended_scheduler: 'Karras / 通常用途',
};
const keys = Object.keys(settings);
const errors = [];
async function start() {
  proc = spawn(python, ['-X', 'utf8', path.join(__dirname, 'checkpoint_settings_e2e_server.py'), app, fixtureRoot], { windowsHide: true });
  const info = await new Promise((resolve, reject) => {
    let buf = '';
    proc.stdout.on('data', b => { buf += b; if (buf.includes('\n')) resolve(JSON.parse(buf.split('\n')[0])); });
    proc.stderr.on('data', b => process.stderr.write(b));
    proc.on('error', reject);
    proc.once('exit', code => reject(new Error('server exited ' + code)));
  });
  base = 'http://127.0.0.1:' + info.port;
}
async function stop() {
  if (!proc || proc.exitCode !== null) return;
  const exited = new Promise(resolve => proc.once('exit', resolve));
  proc.stdin.end('\n');
  await exited;
}
const card = (page, name) => page.locator('#library-grid button[data-path]').filter({ has: page.locator('.card-copy h3', { hasText: name }) });
const field = (page, key) => page.locator('#detail-' + key.replaceAll('_', '-'));
async function open(page) {
  await page.goto(base + '/lora');
  await expect(page.locator('#library-grid button[data-path]')).toHaveCount(4);
}
async function save(page) {
  const response = page.waitForResponse(r => r.url().endsWith('/api/lora/item') && r.request().method() === 'POST');
  await page.locator('#detail-form button[type="submit"]').click();
  assert.equal((await response).status(), 200);
  await expect(page.locator('#toast')).toHaveText('保存しました。');
}
async function checkValues(page, expected) {
  for (const key of keys) await expect(field(page, key)).toHaveValue(expected[key]);
}
async function stored(page, modelPath) {
  const response = await page.request.post(base + '/api/lora/item', { data: { path: modelPath, metadata: { rating: 4 } } });
  assert.equal(response.status(), 200);
  return (await response.json()).metadata;
}

(async () => {
  await start();
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  page.on('pageerror', e => errors.push(e.message));
  await open(page);
  for (const id of ['author', 'base-model', 'strength-min', 'strength-max']) {
    await expect(page.locator('#detail-' + id + ', #bulk-' + id)).toHaveCount(0);
  }
  await card(page, 'Checkpoint A').click();
  await expect(page.locator('#detail-generation-settings')).toBeVisible();
  const modelPath = await card(page, 'Checkpoint A').getAttribute('data-path');
  for (const [key, value] of Object.entries(settings)) await field(page, key).fill(value);
  // Same-revision polling must leave drafts intact.
  await page.evaluate(() => refreshLibraryState({ silent: true }));
  await checkValues(page, settings);
  await save(page);
  await card(page, 'Checkpoint B').click();
  await checkValues(page, Object.fromEntries(keys.map(k => [k, ''])));
  await card(page, 'Checkpoint A').click();
  await checkValues(page, settings);
  await page.reload();
  await expect(card(page, 'Checkpoint A')).toBeVisible();
  await card(page, 'Checkpoint A').click();
  await checkValues(page, settings);
  let metadata = await stored(page, modelPath);
  assert.equal(metadata.author, '既存の作者');
  assert.equal(metadata.base_model, 'Anima');
  assert.equal(metadata.strength_min, 0.5);
  assert.equal(metadata.strength_max, 1.0);
  assert.equal(metadata.notes, '既存のメモ');
  assert.deepEqual(metadata.triggers, ['original trigger']);
  assert.equal(metadata.favorite, true);
  assert.equal(metadata.rating, 4);
  console.log('PASS: per-checkpoint settings, selection/reload persistence, hidden fields removed, existing metadata preserved');
  await page.locator('#detail-generation-settings').scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(artifactDir, 'checkpoint-settings-desktop.png') });

  await page.locator('#search-input').fill('Karras');
  await expect(page.locator('#library-grid button[data-path]')).toHaveCount(1);
  await page.locator('#search-input').fill('');
  await expect(page.locator('#library-grid button[data-path]')).toHaveCount(4);
  await card(page, 'LoRA').click();
  await expect(page.locator('#detail-generation-settings')).toBeHidden();
  await expect(page.locator('#detail-prompt-preview')).toHaveText('<lora:LoRA:1>, original trigger');
  await page.locator('#detail-notes').fill('LoRAメモのみ変更');
  await save(page);
  const loraPath = await card(page, 'LoRA').getAttribute('data-path');
  metadata = await stored(page, loraPath);
  assert.equal(metadata.strength_min, 0.5);
  assert.equal(metadata.strength_max, 1.0);
  await expect(page.locator('#detail-prompt-preview')).toHaveText('<lora:LoRA:1>, original trigger');
  await card(page, 'Workflow').click();
  await expect(page.locator('#detail-generation-settings')).toBeHidden();
  await card(page, 'Checkpoint A').click();
  await page.locator('#bulk-category-custom').fill('確認済み');
  const bulkResponse = page.waitForResponse(r => r.url().endsWith('/api/lora/items/bulk'));
  await page.locator('#bulk-form button[type="submit"]').click();
  assert.equal((await bulkResponse).status(), 200);
  await checkValues(page, settings);
  console.log('PASS: searchable settings, LoRA/Workflow fields hidden, LoRA prompt weight and bulk editing preserved');

  await page.route('**/api/lora/item', route => route.fulfill({ status: 500, json: { error: 'fixture save failure' } }));
  await field(page, 'recommended_steps').fill('失敗する編集');
  await page.locator('#detail-form button[type="submit"]').click();
  await expect(page.locator('#toast')).toContainText('fixture save failure');
  await expect(field(page, 'recommended_steps')).toHaveValue('失敗する編集');
  await page.unroute('**/api/lora/item');
  await page.reload();
  await expect(card(page, 'Checkpoint A')).toBeVisible();
  await card(page, 'Checkpoint A').click();
  await checkValues(page, settings);
  console.log('PASS: failed save retains draft and leaves persisted values intact');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#detail-generation-settings').scrollIntoViewIfNeeded();
  const bounds = await page.locator('#detail-generation-settings').evaluate(el => {
    const r = el.getBoundingClientRect(); return { left: r.left, right: r.right, width: innerWidth };
  });
  assert.ok(bounds.left >= 0 && bounds.right <= bounds.width);
  await page.screenshot({ path: path.join(artifactDir, 'checkpoint-settings-mobile.png') });
  await page.close();
  await stop();
  await start();
  const after = await browser.newPage();
  after.on('pageerror', e => errors.push(e.message));
  await open(after);
  await card(after, 'Checkpoint A').click();
  await checkValues(after, settings);
  for (const key of keys) await field(after, key).fill('');
  await save(after);
  await after.reload();
  await expect(card(after, 'Checkpoint A')).toBeVisible();
  await card(after, 'Checkpoint A').click();
  await checkValues(after, Object.fromEntries(keys.map(k => [k, ''])));
  assert.deepEqual(errors, []);
  console.log('PASS: real server restart, empty-value clearing, 390px layout, no browser exceptions');
  fs.writeFileSync(path.join(artifactDir, 'checkpoint-settings-result.json'), JSON.stringify({ ok: true, settings, bounds, fixtureRoot }, null, 2));
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(async () => {
  await browser?.close(); await stop();
});
