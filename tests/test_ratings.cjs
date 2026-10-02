const { chromium, expect } = require('playwright/test');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const app = path.resolve(process.argv[2] || path.join(__dirname, '..'));
const artifactDir = path.resolve(process.env.RATING_ARTIFACT_DIR || path.join(__dirname, 'rating-artifacts'));
fs.mkdirSync(artifactDir, { recursive: true });
const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'lora-rating-e2e-'));
const python = process.env.PYTHON || 'python';
let proc, browser, base;
async function start() {
  proc = spawn(python, ['-X', 'utf8', path.join(__dirname, 'rating_e2e_server.py'), app, fixtureRoot], { windowsHide: true });
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
(async () => {
  await start();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const cards = page.locator('#library-grid button[data-path]');
  const groups = page.locator('#library-grid .card-rating');
  const settle = async () => { await expect(cards).toHaveCount(7); };
  const choose = async (index, value) => {
    await groups.nth(index).locator('[data-rating="' + value + '"]').click();
    await expect(groups.nth(index)).toHaveAttribute('aria-busy', 'false');
    await expect(groups.nth(index)).toHaveAttribute('data-rating', String(value));
    await expect(groups.nth(index).locator('.is-filled')).toHaveCount(value);
  };
  await page.goto(base + '/lora'); await settle();
  await page.selectOption('#sort-select', 'name'); await settle();
  await expect(groups).toHaveCount(7);
  await expect(page.locator('button button')).toHaveCount(0);
  await expect(groups.nth(0)).toHaveAttribute('data-rating', '0');
  await page.screenshot({ path: path.join(artifactDir, 'unrated.png'), fullPage: false });
  for (let value = 1; value <= 5; value++) await choose(0, value);
  assert.equal(await page.evaluate(() => state.bulkSelectedPaths.size), 0);
  assert.equal(await page.evaluate(() => state.selectedPath), '');
  console.log('PASS: all five ratings, no nested buttons, star clicks do not select cards');
  await page.reload(); await settle();
  await expect(groups.nth(0)).toHaveAttribute('data-rating', '5');
  console.log('PASS: saved rating survives browser reload');

  await cards.nth(0).click();
  await cards.nth(3).click({ modifiers: ['Shift'] });
  assert.equal(await page.evaluate(() => state.bulkSelectedPaths.size), 4);
  await page.locator('#detail-notes').fill('未保存のメモを保持');
  const selectedPath = await page.evaluate(() => state.selectedPath);
  await groups.nth(1).locator('[data-rating="3"]').click({ modifiers: ['Control'] });
  await expect(groups.nth(1)).toHaveAttribute('aria-busy', 'false');
  await expect(groups.nth(1)).toHaveAttribute('data-rating', '3');
  assert.equal(await page.evaluate(() => state.bulkSelectedPaths.size), 4);
  assert.equal(await page.evaluate(() => state.selectedPath), selectedPath);
  await expect(page.locator('#detail-notes')).toHaveValue('未保存のメモを保持');
  await page.evaluate(() => refreshLibraryState({ silent: true }));
  await expect(page.locator('#detail-notes')).toHaveValue('未保存のメモを保持');
  await expect(page.locator('#reference-panel')).toBeVisible();
  assert.equal(await page.locator('#reference-panel').evaluate(el => el.parentElement.id), 'library-grid');
  console.log('PASS: range selection, modifiers, draft notes and reference panel preserved');

  await page.locator('#detail-favorite').check();
  await page.locator('#detail-form').evaluate(el => el.requestSubmit());
  await expect(page.locator('#toast')).toHaveText('保存しました。');
  await expect(groups.nth(0)).toHaveAttribute('data-rating', '5');
  await page.reload(); await settle();
  await expect(groups.nth(0)).toHaveAttribute('data-rating', '5');

  await groups.nth(1).locator('[data-rating="3"]').press('Enter');
  await expect(groups.nth(1)).toHaveAttribute('data-rating', '0');
  await expect(groups.nth(1)).toHaveAttribute('aria-busy', 'false');
  await groups.nth(1).locator('[data-rating="2"]').press('Space');
  await expect(groups.nth(1)).toHaveAttribute('data-rating', '2');
  await expect(groups.nth(1)).toHaveAttribute('aria-busy', 'false');
  console.log('PASS: same-star clear, Enter/Space, metadata edits keep rating');

  let calls = 0;
  await page.route('**/api/lora/item', async route => {
    if (!('rating' in route.request().postDataJSON().metadata)) return route.continue();
    calls++;
    await new Promise(r => setTimeout(r, 250));
    await route.fulfill({ status: 500, json: { error: 'fixture database failure' } });
  });
  const savedPath = await cards.nth(0).getAttribute('data-path');
  await page.evaluate(p => { saveCardRating(p, 1); saveCardRating(p, 2); }, savedPath);
  await expect(groups.nth(0)).toHaveAttribute('aria-busy', 'true');
  await expect(groups.nth(0)).toHaveAttribute('aria-busy', 'false');
  await expect(groups.nth(0)).toHaveAttribute('data-rating', '5');
  await expect(page.locator('#toast')).toContainText('fixture database failure');
  assert.equal(calls, 1);
  await page.unroute('**/api/lora/item');
  for (const rating of [-1, 6, 1.5, true, '3', null]) {
    const response = await page.request.post(base + '/api/lora/item', { data: { path: savedPath, metadata: { rating } } });
    assert.equal(response.status(), 400);
  }
  console.log('PASS: failed writes keep previous rating, duplicate blocked, invalid API values rejected');
  for (const [i, rating] of [[2, 3], [3, 4], [4, 1], [5, 2], [6, 5]]) await choose(i, rating);
  await groups.nth(0).scrollIntoViewIfNeeded();
  await page.mouse.move(10, 10);
  await page.screenshot({ path: path.join(artifactDir, 'ratings-desktop.png'), fullPage: false });
  const geometry = await groups.nth(0).evaluate(el => {
    const star = el.getBoundingClientRect(), card = el.parentElement.querySelector('.lora-card').getBoundingClientRect();
    return { top: star.top - card.top, right: card.right - star.right, within: star.left >= card.left && star.bottom <= card.bottom };
  });
  assert.ok(geometry.top >= 0 && geometry.top <= 30 && geometry.right >= 0 && geometry.right <= 30 && geometry.within);
  await page.setViewportSize({ width: 390, height: 844 });
  await groups.nth(0).scrollIntoViewIfNeeded();
  await expect(groups.nth(0).locator('.rating-star')).toHaveCount(5);
  const clipped = await groups.nth(0).evaluate(el => { const r=el.getBoundingClientRect(); return r.left < 0 || r.right > innerWidth; });
  assert.equal(clipped, false);
  await page.screenshot({ path: path.join(artifactDir, 'ratings-mobile.png'), fullPage: false });
  console.log('PASS: top-right stars stay visible at desktop and 390px width');
  await page.close();
  await stop();
  await start();
  const after = await browser.newPage();
  await after.goto(base + '/lora');
  await expect(after.locator('.card-rating')).toHaveCount(7);
  const persisted = await after.evaluate(() => Object.fromEntries(state.items.map(i => [i.display_name, i.rating])));
  assert.deepEqual(persisted, { Alpha: 5, Beta: 2, Delta: 3, Epsilon: 4, Gamma: 1, Workflow: 2, Zeta: 5 });
  assert.deepEqual(errors, []);
  console.log('PASS: all ratings persist after real server process restart, no browser exceptions');
  fs.writeFileSync(path.join(artifactDir, 'result.json'), JSON.stringify({ ok: true, persisted, geometry, fixtureRoot }, null, 2));
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(async () => {
  await browser?.close(); await stop();
});

