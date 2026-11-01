// Browser smoke test against a running stack (docker compose or dev servers).
// Usage: BASE_URL=http://localhost:8080 E2E_EMAIL=admin@example.com E2E_PASSWORD=... node smoke.cjs
const { chromium } = require('playwright');
const BASE = process.env.BASE_URL || 'http://localhost:8080';
const path = require('path');
const assert = (c, m) => { if (!c) { console.error('FAIL:', m); process.exit(1); } };
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1400, height: 1000 } });
  const bad = [];
  p.on('response', r => { if (r.url().includes('/api/') && r.status() >= 400) console.log('resp', r.status(), r.url()); });
  p.on('pageerror', e => console.log('pageerror', e.message));
  p.on('response', r => { if (r.status() >= 500) bad.push(`${r.status()} ${r.url()}`); });
  await p.goto(BASE + '/');
  await p.getByLabel('Email').fill(process.env.E2E_EMAIL || 'admin@example.com');
  await p.getByLabel('Password').fill(process.env.E2E_PASSWORD);
  await p.getByRole('button', { name: 'Sign in' }).click();
  await p.waitForSelector('text=Fleet overview');
  // Session survives a reload (refresh cookie through nginx).
  await p.reload(); await p.waitForSelector('text=Fleet overview');
  // Register equipment matching the sample CSV serials, then upload.
  await p.goto(BASE + '/equipment');
  await p.getByRole('button', { name: 'Add equipment' }).click();
  await p.getByLabel('Name', { exact: true }).fill('E2E test pump');
  await p.getByLabel('Serial number').fill('E2E-' + Date.now());
  await p.locator('form label:has(span:text-is("Type")) select').selectOption({ label: 'Centrifugal pump' });
  await p.getByRole('button', { name: 'Save' }).click();
  await p.getByLabel('Search').fill('E2E test pump');
  await p.waitForSelector('a:has-text("E2E test pump")');
  console.log('equipment created via form: yes');
  await p.goto(BASE + '/ingestion');
  await p.getByLabel('CSV file').setInputFiles(path.join(__dirname, '..', 'data', 'sample', 'sample_readings.csv'));
  await p.getByRole('button', { name: 'Upload' }).click();
  await p.waitForSelector('td:has-text("completed")', { timeout: 60000 });
  const row = await p.locator('tbody tr').first().innerText();
  console.log('import row:', row.replace(/\s+/g, ' '));
  await p.goto(BASE + '/models');
  await p.waitForSelector('text=model comparison');
  await p.getByRole('button', { name: 'Sign out' }).click();
  await p.waitForSelector('text=Sign in to continue');
  await p.reload(); await p.waitForSelector('text=Sign in to continue');
  console.log('logout persists after reload: yes');
  assert(bad.length === 0, `5xx responses: ${bad}`);
  console.log('PASS: no 5xx responses');
  await b.close();
})();
