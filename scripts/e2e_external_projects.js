// End-to-end proof, run against a live stack: two red/blue projects that were
// started independently of this repository, connected over ASAP, fought through
// the browser UI, down to a saved report. It asserts the whole chain rather than
// any one service, because every piece of it has been broken at some point by a
// change that left the individual services healthy.
//
// It drives the real interface instead of the API on purpose: the point is that
// the frontend and backend agree. A battle that runs but never reaches the
// screen, or a screen that animates without a battle behind it, both pass an
// API-only test and both are broken.
//
// Prerequisites: the stack up, and two projects registered under the service ids
// the selects will show. Start each project yourself, then:
//   curl -X POST localhost:8800/api/services -H 'Content-Type: application/json' \
//     -d '{"id":"ext-red","name":"External Red (independent project)",
//          "url":"http://<host>:<port>","type":"red"}'
// and the same for blue. Then:
//   npm i playwright
//   CHROME=<chromium> OUT=<dir> ROUNDS=6 node scripts/e2e_external_projects.js
// Exit status is the number of failed assertions, so CI can gate on it.
const { chromium } = require('playwright');
const OUT = process.env.OUT;
const D = { w: 1280, h: 720 };
let canvas, failures = 0;
const dx = x => canvas.x + x * canvas.w / D.w, dy = y => canvas.y + y * canvas.h / D.h;
const modalSel = '.modal-backdrop > div';
const ok  = (name, cond, detail = '') => { console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (detail ? '  — ' + detail : '')); if (!cond) failures++; return cond; };
const api = (p, path) => p.evaluate(u => fetch(u).then(r => r.json()).catch(() => null), 'http://localhost:8800' + path);
async function shot(p, n) { await p.screenshot({ path: `${OUT}/${n}.png` }); }
async function shotEl(p, n) { const e = await p.$(modalSel); if (e) await e.screenshot({ path: `${OUT}/${n}.png` }); return !!e; }
async function clickDesign(p, x, y) {
  await p.mouse.move(dx(x) - 12, dy(y) - 12); await p.waitForTimeout(120);
  await p.mouse.move(dx(x), dy(y)); await p.waitForTimeout(220);
  await p.mouse.down(); await p.waitForTimeout(90); await p.mouse.up(); await p.waitForTimeout(1500);
}
async function shutDrawer(p) {
  for (let i = 0; i < 4; i++) {
    if (!/pick a day above/.test(await p.evaluate(() => document.body.innerText))) return;
    await p.evaluate(() => {
      const c = [...document.querySelectorAll('button')].find(e => e.innerText.trim() === '◀');
      if (c) return c.click();
      const t = [...document.querySelectorAll('button')].find(e => /BATTLES/.test(e.innerText)); t && t.click();
    });
    await p.waitForTimeout(1200);
  }
}

(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const page = await b.newPage({ viewport: { width: 1024, height: 576 }, deviceScaleFactor: 2 });
  const consoleErrors = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', e => consoleErrors.push('pageerror: ' + e.message));

  await page.goto('http://localhost:3030', { waitUntil: 'networkidle' });
  await page.waitForTimeout(4000);
  canvas = await page.evaluate(() => { const c = document.querySelector('canvas'); const r = c.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  await shutDrawer(page);

  // 1 — the frontend sees the two external projects the backend registered
  const picked = await page.evaluate(() => {
    const sels = [...document.querySelectorAll('select')];
    const out = [];
    for (const s of sels) {
      const opt = [...s.options].find(o => /External (Red|Blue)/.test(o.text));
      if (opt) {
        s.value = opt.value;
        s.dispatchEvent(new Event('change', { bubbles: true }));
        out.push(opt.text + ' => ' + opt.value);
      }
    }
    return out;
  });
  ok('frontend lists both external projects', picked.length === 2, picked.join(' | '));
  await page.waitForTimeout(1500);

  // 2 — the readiness gate judges them on what they declared
  await page.locator('input[type=number]').first().fill(String(process.env.ROUNDS || 6)).catch(()=>{});
  const before = new Set(((await api(page, '/api/battles')) || []).map(x => x.session_id));
  await page.getByRole('button', { name: /LAUNCH/ }).click().catch(()=>{});
  await page.waitForFunction(() => /REVIEW REQUIRED|READY|BLOCKED/i.test(document.body.innerText), { timeout: 90000 }).catch(()=>{});
  await page.waitForTimeout(2500);
  const gate = await page.evaluate(s => document.querySelector(s)?.innerText || '', modalSel);
  ok('gate reports both sides online', (gate.match(/ONLINE/g) || []).length === 2);
  ok('gate does not mark them platform defaults', !/PLATFORM-DEFAULT/i.test(gate));
  ok('gate reads their declared capabilities', /attack generation/i.test(gate) && /input guard/i.test(gate) && /output guard/i.test(gate));
  ok('gate confirms every model reachable', /all reachable/i.test(gate));
  ok('gate offers exactly one evolution toggle', (gate.match(/loop/gi) || []).length === 1, JSON.stringify((gate.match(/.{0,40}loop.{0,40}/gi) || [])));
  await shotEl(page, 'e2e-1-gate');

  await page.evaluate(() => {
    const c = [...document.querySelectorAll('input[type=checkbox]')].find(c => /inner loop/i.test(c.closest('label')?.innerText || ''));
    if (c && !c.checked) c.click();
  });
  await page.waitForTimeout(600);
  await page.evaluate(() => { const x = [...document.querySelectorAll('button')].find(e => /CONFIRM/i.test(e.innerText)); if (x && !x.disabled) x.click(); });

  // 3 — the launch reached the backend
  let sid = '';
  for (let i = 0; i < 40 && !sid; i++) {
    await page.waitForTimeout(3000);
    const l = (await api(page, '/api/battles')) || [];
    const f = l.find(x => !before.has(x.session_id));
    if (f) sid = f.session_id;
  }
  ok('browser launch created a battle in the arena', !!sid, sid);
  const started = await api(page, '/api/battles/' + sid);
  ok('the battle runs the two external projects',
     started && started.red_service_id === 'ext-red' && started.blue_service_id === 'ext-blue',
     started ? `${started.red_service_id} vs ${started.blue_service_id}` : '');

  // 4 — live events actually drive the screen
  let sawPhase = false, sawRound = false, sawAgent = false;
  const DONE = ['complete', 'completed', 'stopped', 'failed'];
  let last = null;
  for (let i = 0; i < 400; i++) {
    const txt = await page.evaluate(() => document.body.innerText);
    if (/RECON|ATTACK|DEFENSE|TARGET|JUDGE|ROUND/.test(txt)) sawPhase = true;
    if (/\bR[1-9]\d*\b/.test(txt)) sawRound = true;
    if (/THINKING|SUCCESS|FAILED/.test(txt)) sawAgent = true;
    last = await api(page, '/api/battles/' + sid);
    if (last && DONE.includes(last.status)) break;
    if (i % 12 === 0) console.log('   ... round', last && last.current_round, last && last.status);
    await page.waitForTimeout(5000);
  }
  ok('phase indicator moved from live events', sawPhase);
  ok('round counter moved from live events', sawRound);
  ok('agent states moved from live events', sawAgent);
  ok('battle reached the end', last && DONE.includes(last.status), last ? last.status + ' r' + last.current_round : '');
  ok('every configured round was fought', last && last.current_round === last.max_rounds,
     last ? last.current_round + '/' + last.max_rounds : '');
  await shot(page, 'e2e-2-battle-end');

  // 5 — per-role reasoning is readable, and it came from these projects
  await clickDesign(page, 270, 420);
  const red = await page.evaluate(s => document.querySelector(s)?.innerText || '', modalSel);
  ok('red side streams its own reasoning', red.length > 200 && /R\d/.test(red));
  await shotEl(page, 'e2e-3-red-stream');
  await page.keyboard.press('Escape'); await page.waitForTimeout(900);

  await clickDesign(page, 447, 592);
  const jv = await page.evaluate(s => document.querySelector(s)?.innerText || '', modalSel);
  ok('judge console carries a verdict and its reason', /VERDICT|BLOCKED|WIN|BREACH/i.test(jv) && /REASONING|reason/i.test(jv));
  await shotEl(page, 'e2e-4-verdict');
  await page.keyboard.press('Escape'); await page.waitForTimeout(900);

  // 6 — the report, opened the way a user opens it
  let reportText = '';
  for (let i = 0; i < 30; i++) {
    await clickDesign(page, 1160, 640);
    if (await page.$(modalSel)) {
      reportText = await page.evaluate(s => document.querySelector(s).innerText, modalSel);
      if (!/NOT YET RECEIVED|PENDING/i.test(reportText)) break;
      await page.keyboard.press('Escape'); await page.waitForTimeout(700);
    }
    await page.waitForTimeout(6000);
  }
  ok('report opens from the printer', /BATTLE.REPORT/i.test(reportText));
  ok('report carries the rounds that were fought', new RegExp('Rounds:\\s*' + (last?.max_rounds ?? '')).test(reportText), (reportText.match(/Rounds:\s*\d+/) || [''])[0]);
  ok('report offers the downloads', /Report/.test(reportText) && /JSON/.test(reportText));
  ok('report never mentions the removed loop', !/generation|promot|rollback|self-improv/i.test(reportText));
  await shotEl(page, 'e2e-5-report');

  ok('no frontend console errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '));
  console.log(JSON.stringify({ session: sid, failures }));
  await b.close();
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
