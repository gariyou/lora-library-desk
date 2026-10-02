const { chromium, expect } = require('playwright/test');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const app = path.resolve(process.argv[2] || path.join(__dirname, '..'));
const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'lora-rating-filter-'));
const output = path.resolve(process.env.RATING_FILTER_ARTIFACT_DIR || path.join(os.tmpdir(), 'lora-rating-filter-artifacts'));
fs.mkdirSync(output, { recursive: true });
const python = process.env.PYTHON || 'python';
const proc = spawn(python, ['-X', 'utf8', path.join(__dirname, 'rating_e2e_server.py'), app, fixtureRoot], { windowsHide: true });
let browser;
(async () => {
  const info = await new Promise((resolve, reject) => {
    let text = '';
    proc.stdout.on('data', data => { text += data; if (text.includes('\n')) resolve(JSON.parse(text.split('\n')[0])); });
    proc.stderr.on('data', data => process.stderr.write(data));
    proc.on('error', reject);
    proc.once('exit', code => reject(new Error('fixture server exited ' + code)));
  });
  const base = 'http://127.0.0.1:' + info.port;
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const seed = [
    ['Alpha', 1, true, 'style'], ['Beta', 2, false, 'style'], ['Delta', 3, true, 'character'],
    ['Epsilon', 4, false, 'character'], ['Gamma', 5, true, 'style'],
    ['Workflow', 0, true, 'character'], ['Zeta', 0, false, 'style'],
  ];
  for (const [name, rating, favorite, category] of seed) {
    const modelPath = name === 'Workflow' ? path.join(fixtureRoot, 'workflows', 'Workflow.json') : path.join(fixtureRoot, 'Lora', name + '.safetensors');
    const response = await page.request.post(base + '/api/lora/item', { data: { path: modelPath, metadata: { rating, favorite, category, notes: 'fixture ' + name } } });
    assert.equal(response.status(), 200);
  }
  const cards = page.locator('#library-grid button[data-path]');
  const filter = page.locator('#rating-filter');
  async function names(expected) {
    await expect(cards.locator('h3')).toHaveText(expected);
    await expect(page.locator('#result-count')).toHaveText(expected.length + '件');
  }
  async function setFilter(value, expected) {
    await filter.selectOption(value);
    await names(expected);
  }
  await page.goto(base + '/lora');
  await expect(cards).toHaveCount(7);
  await page.selectOption('#sort-select', 'name');
  await names(seed.map(x => x[0]));
  await expect(filter.locator('option')).toHaveCount(12);
  for (let n = 1; n <= 5; n++) await setFilter('eq-' + n, [seed[n - 1][0]]);
  for (let n = 1; n <= 5; n++) await setFilter('lte-' + n, seed.slice(0, n).map(x => x[0]));
  await setFilter('unrated', ['Workflow', 'Zeta']);
  console.log('PASS: all 5 exact and 5 inclusive upper-bound filters; unrated stays separate');
  await setFilter('lte-3', ['Alpha', 'Beta', 'Delta']);
  await page.locator('#favorite-filter').check(); await names(['Alpha', 'Delta']);
  await page.selectOption('#category-filter', 'character'); await names(['Delta']);
  await page.fill('#search-input', 'fixture Delta'); await names(['Delta']);
  await page.reload();
  await names(['Delta']);
  await expect(filter).toHaveValue('lte-3');
  await expect(page.locator('#favorite-filter')).toBeChecked();
  await expect(page.locator('#category-filter')).toHaveValue('character');
  await expect(page.locator('#search-input')).toHaveValue('fixture Delta');
  console.log('PASS: text, favorite and category filters combine and survive reload');

  await page.locator('#favorite-filter').uncheck();
  await page.selectOption('#category-filter', 'all');
  await page.fill('#search-input', '');
  await filter.selectOption('unrated');
  await page.selectOption('#family-filter', 'workflow'); await names(['Workflow']);
  await page.selectOption('#family-filter', 'all'); await names(['Workflow', 'Zeta']);
  await setFilter('eq-1', ['Alpha']);
  await page.goto(base + '/lora?search=Gamma');
  await names(['Gamma']);
  await expect(filter).toHaveValue('all');
  await page.goto(base + '/lora');
  await page.fill('#search-input', '');
  await setFilter('eq-3', ['Delta']);
  await cards.first().click();
  await page.locator('#detail-notes').fill('編集中のメモを維持');
  await page.locator('.card-rating [data-rating="5"]').click();
  await names([]);
  await expect(page.locator('#detail-notes')).toHaveValue('編集中のメモを維持');
  await expect(page.locator('#selection-count')).toContainText('表示外 1件');
  await expect(page.locator('#empty-state')).toContainText('星評価');
  console.log('PASS: model-type filtering and search links; rating changes remove excluded cards and preserve drafts/selection');

  await setFilter('unrated', ['Workflow', 'Zeta']);
  await page.locator('.card-rating').first().locator('[data-rating="1"]').click();
  await names(['Zeta']);
  await setFilter('eq-1', ['Alpha', 'Workflow']);
  await page.locator('.card-rating').first().locator('[data-rating="1"]').click();
  await names(['Workflow']);
  await setFilter('lte-2', ['Beta', 'Workflow']);
  await page.locator('.card-rating').first().locator('[data-rating="5"]').click();
  await names(['Workflow']);
  await setFilter('eq-4', ['Epsilon']);
  await page.route('**/api/lora/item', r => r.fulfill({ status: 500, json: { error: 'fixture save failure' } }));
  await page.locator('.card-rating [data-rating="1"]').click();
  await expect(page.locator('#toast')).toContainText('fixture save failure');
  await names(['Epsilon']);
  await expect(page.locator('.card-rating')).toHaveAttribute('data-rating', '4');
  await page.unroute('**/api/lora/item');
  console.log('PASS: setting, clearing and increasing ratings refilter immediately; failed saves do not remove cards');

  await page.evaluate(() => localStorage.setItem('lora-manager:filters:v1', JSON.stringify({ rating: 'lte-99', sort: 'name' })));
  await page.reload(); await expect(filter).toHaveValue('all'); await names(seed.map(x => x[0]));
  await page.evaluate(() => localStorage.setItem('lora-manager:filters:v1', '{"sort":"name"}'));
  await page.reload(); await expect(filter).toHaveValue('all'); await names(seed.map(x => x[0]));
  await page.evaluate(() => localStorage.setItem('lora-manager:filters:v1', '{broken'));
  await page.reload(); await expect(filter).toHaveValue('all'); await expect(cards).toHaveCount(7);
  await page.selectOption('#sort-select', 'name');
  await setFilter('lte-3', ['Workflow']);
  await filter.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, 'rating-filter-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await filter.scrollIntoViewIfNeeded();
  const fits = await filter.evaluate(el => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth; });
  assert.ok(fits);
  await page.screenshot({ path: path.join(output, 'rating-filter-mobile.png') });
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ ok: true, variants: 12, checks: ['all exact thresholds', 'all inclusive upper bounds', 'unrated exclusion', 'combined filters', 'reload', 'search-link reset', 'edit-and-refilter', 'failed saves', 'old/invalid preferences', 'narrow screen'], fixtureRoot }, null, 2));
  console.log('PASS: old/invalid preferences recover; 390px layout; no browser exceptions');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(async () => {
  await browser?.close();
  if (proc.exitCode === null) {
    const closed = new Promise(resolve => proc.once('exit', resolve));
    proc.stdin.end('\n'); await closed;
  }
});

