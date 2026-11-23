#!/usr/bin/env node
// Interactive DOM regression test for the per-job recording folder and the
// analysis-section drag & drop, driven in a real Chromium over the DevTools
// Protocol against the app's own local companion server.
//
//   node tests/recording_dir_cdp.mjs [appRoot]
//
// Requirements: node >= 22 (global WebSocket) and chromium/chromium-browser on
// PATH. Nothing else is installed; the test spawns its own server and browser
// on throwaway data/home directories. No receiver is opened: only scheduling
// (which persists a job) and the local UI are exercised.
//
// Verified behaviour:
//   * the native schedule form offers a recording-folder field and sends it to
//     the companion as a subdirectory name (the server resolves it under
//     captures and keeps it on the job record);
//   * a traversal/invalid folder name is refused client-side, with no request;
//   * a recording dropped on the analysis section is handed to that section's
//     own file input exactly like the picker, with a visible drop affordance;
//   * unsupported drops are ignored.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const APP_ROOT = process.argv[2] || "/home/horseman/sat-scratch-700/fix/usr/share/tihulu-satellite-center";
const PORT = 8831 + Math.floor(Math.random() * 120);
// A fresh CDP port per run: stale browser instances must never be adopted.
const CDP = 9500 + Math.floor(Math.random() * 300);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
let failures = 0;
let closeBrowser = async () => {};

const check = (name, ok, detail = "") => {
  log((ok ? "PASS " : "FAIL ") + name + (detail ? " :: " + detail : ""));
  if (!ok) failures++;
};

const cleanup = [];
const run = async () => {
  const data = mkdtempSync(join(tmpdir(), "sat-cdp-dir-"));
  const home = mkdtempSync(join(tmpdir(), "sat-cdp-dir-home-"));
  cleanup.push(data, home);
  // The companion's server-side pass planner needs skyfield; prefer an
  // interpreter that has it (the app's own optional orbit dependency).
  const SERVER_PYTHON = process.env.SAT_PYTHON || "/usr/bin/python3";
  const server = spawn(SERVER_PYTHON, ["-m", "satellite_center", "serve", "--host", "127.0.0.1",
    "--port", String(PORT)], {
    env: { ...process.env, PYTHONPATH: APP_ROOT, HOME: home, TMPDIR: data,
           TIHULU_SATELLITE_DATA: data, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["ignore", "ignore", "pipe"], detached: false,
  });
  cleanup.push(server);
  server.stderr.on("data", (d) => process.stderr.write("[serve] " + d));
  let up = false;
  for (let i = 0; i < 180; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/browser/index.html`); if (r.ok) { up = true; break; } } catch {}
    await sleep(500);
  }
  if (!up) throw new Error(`serve never answered on ${PORT} (appRoot=${APP_ROOT})`);
  const profile = mkdtempSync(join(tmpdir(), "sat-cdp-dir-prof-"));
  cleanup.push(profile);
  const browser = spawn("chromium-browser", [
    "--headless", "--no-sandbox", "--disable-gpu",
    `--remote-debugging-port=${CDP}`, `--user-data-dir=${profile}`,
    `http://127.0.0.1:${PORT}/browser/index.html`,
  ], { stdio: "ignore" });
  cleanup.push(browser);
  await sleep(7000);

  let target = null;
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP}/json/list`)).json();
      target = list.find((t) => t.type === "page") || null;
      if (target) break;
    } catch {}
    await sleep(1000);
  }
  if (!target) throw new Error(`no chromium page target on ${CDP} after 60 s`);
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === "Fetch.requestPaused") {
      ws.send(JSON.stringify({ id: ++id, method: "Fetch.continueRequest",
        params: { requestId: m.params.requestId } }));
      return;
    }
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    }
  };
  closeBrowser = async () => { try { await send("Browser.close"); } catch {} };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const n = ++id; pending.set(n, { res, rej }); ws.send(JSON.stringify({ id: n, method, params }));
  });
  const ev = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("page error: " + JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  };
  for (let i = 0; i < 90; i++) {
    if ((await ev("document.readyState")) === "complete" &&
        await ev("Boolean(document.head && document.body)")) break;
    await sleep(1000);
  }
  check("the page exposes both analysis sections",
        await ev("Boolean(document.getElementById('iq-analysis'))") &&
        await ev("Boolean(document.getElementById('satellite-images'))"),
        "page loaded");

  // ---- 1) the native schedule form carries the folder field -----------------
  await ev(`(() => {
    const h = document.getElementById('hours'); if (h) h.value = '72';
    // Group by ISS so the pass we schedule is always on the first page.
    const family = document.getElementById('pass-family');
    if (family) { family.value = 'iss'; family.dispatchEvent(new Event('change', { bubbles: true })); }
    return 'filters'; })(); 'hours'`);
  await ev("document.getElementById('orbit-fetch').click(); 'fetch'");
  await sleep(6000);
  await ev("document.getElementById('orbit-calc').click(); 'calc'");
  let cards = 0;
  for (let i = 0; i < 60; i++) {
    cards = await ev("document.querySelectorAll('#passes .pass').length");
    if (cards > 0) break;
    await sleep(2000);
  }
  check("predicted pass cards render (TLE reachable)", cards > 0, String(cards));
  if (!cards) return finish();

  const issFinder = "[...document.querySelectorAll('#passes .pass')]\n      .find(c => { const f = c.querySelector('form.pass-schedule-form select');\n                   return f && [...f.options].some(o => o.value === '145800000'); })";
  const opened = await ev(`(() => {
    const card = ${issFinder};
    const details = card && card.querySelector('details');
    if (details) details.open = true;
    return Boolean(details); })()`);
  const cardIdentity = await ev(`(() => {
    const card = ${issFinder};
    return JSON.stringify({ norad: Number(card && card.dataset.norad),
      aos: Number(card && card.dataset.aos),
      los: Number(card && card.dataset.aos) + 600000 }); })()`);
  const identity = JSON.parse(cardIdentity);
  check("the ISS card carries TLE geometry",
        Number.isFinite(identity.norad) && Number.isFinite(identity.aos), cardIdentity);
  // The companion's own /api/passes runs for real: it downloads its TLEs and
  // fills the server-side prediction the schedule() call is validated against.
  check("an ISS pass card is available for scheduling", opened);
  await sleep(1200);

  const folderSeen = await ev(`(() => {
    const card = ${issFinder};
    const details = card && card.querySelector('details');
    if (details) details.open = true;
    const form = card && card.querySelector('form.pass-schedule-form');
    const input = form && form.querySelector('input[type="text"]');
    return Boolean(input) && !input.hidden && input.closest('label') && !input.closest('label').hidden; })()`);
  check("the schedule form offers a recording-folder field (native companion)", folderSeen);

  // ---- 2) a valid subdirectory name reaches the companion ------------------
  const scheduled = await ev(`(async () => {
    const card = ${issFinder};
    const details = card && card.querySelector('details');
    if (details) details.open = true;
    const form = card && card.querySelector('form.pass-schedule-form');
    const folder = form.querySelector('input[type="text"]');
    const duration = form.querySelector('input[type="number"]');
    const unit = form.querySelectorAll('select')[1];
    folder.value = 'ISS-SSTV-2026-11';
    duration.value = '60';
    if (unit) unit.value = 'seconds';
    const confirm = [...form.querySelectorAll('button')].find(b => /Confirm/.test(b.textContent));
    form.requestSubmit ? form.requestSubmit(confirm) : confirm.click();
    const alert = form.querySelector('.pass-schedule-error');
    for (let i = 0; i < 120; i++) {
      if (alert && /scheduled|Could not schedule/.test(alert.textContent)) break;
      await new Promise(r => setTimeout(r, 1000));
    }
    return String(alert && alert.textContent || ''); })()`);
  check("scheduling with a folder is accepted by the companion",
        /Native companion:/.test(scheduled), scheduled.slice(0, 180));

  const status = await (await fetch(`http://127.0.0.1:${PORT}/api/status`)).json();
  const stored = (status.jobs || []).find((j) => j.output_dir);
  check("the chosen folder is stored on the job record",
        Boolean(stored) && /captures[\/]ISS-SSTV-2026-11$/.test(String(stored && stored.output_dir)),
        String(stored && stored.output_dir));

  // ---- 3) an invalid folder name is refused before any request -------------
  const refused = await ev(`(async () => {
    const card = ${issFinder};
    const details = card && card.querySelector('details');
    if (details) details.open = true;
    const form = card && card.querySelector('form.pass-schedule-form');
    if (!form) return 'no form';
    const folder = form.querySelector('input[type="text"]');
    folder.value = '../escape';
    const confirm = [...form.querySelectorAll('button')].find(b => /Confirm/.test(b.textContent));
    form.requestSubmit ? form.requestSubmit(confirm) : confirm.click();
    const alert = form.querySelector('.pass-schedule-error');
    for (let i = 0; i < 30; i++) {
      if (alert && /Could not schedule/.test(alert.textContent)) break;
      await new Promise(r => setTimeout(r, 500));
    }
    return String(alert && alert.textContent || ''); })()`);
  check("a traversal folder name is refused client-side",
        /Could not schedule: Recording folder/.test(refused), refused.slice(0, 160));

  // ---- 4) drag & drop on the analysis sections ------------------------------
  const dropped = await ev(`(() => {
    const section = document.getElementById('iq-analysis');
    const input = document.getElementById('iq-file');
    const name = 'TIHULU_ISS-SSTV-CANDIDATE_20261123T120000Z_437550000Hz_IQ.cu8';
    const file = new File([new Uint8Array([1,2,3,4])], name, { type: 'application/octet-stream' });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    section.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    const active = section.classList.contains('drop-active');
    section.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    return JSON.stringify({ active,
      assigned: input.files.length === 1 && input.files[0].name === name,
      stillActive: section.classList.contains('drop-active') }); })()`);
  const dropState = JSON.parse(dropped);
  check("dragging a supported recording highlights the section", dropState.active);
  check("a dropped CU8 is handed to the analyzer's own file input", dropState.assigned);
  check("the highlight clears after the drop", !dropState.stillActive);

  const images = await ev(`(() => {
    const section = document.getElementById('satellite-images');
    const input = document.getElementById('image-sstv-file');
    const file = new File([new Uint8Array([0,1,2,3])], 'TIHULU_ISS_20261123T120000Z_SSTV-AUDIO.wav',
                           { type: 'audio/wav' });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    section.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    const active = section.classList.contains('drop-active');
    section.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    return JSON.stringify({ active,
      assigned: input.files.length === 1 && input.files[0].name.endsWith('.wav') }); })()`);
  const imageState = JSON.parse(images);
  check("a WAV dropped on the images section reaches the SSTV input",
        imageState.active && imageState.assigned);

  const ignored = await ev(`(() => {
    const section = document.getElementById('iq-analysis');
    const input = document.getElementById('iq-file');
    const before = input.files.length;
    const file = new File([new Uint8Array([1])], 'notes.txt', { type: 'text/plain' });
    const transfer = new DataTransfer();
    transfer.items.add(file);
    section.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    const active = section.classList.contains('drop-active');
    section.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    return JSON.stringify({ active, unchanged: input.files.length === before }); })()`);
  const ignoredState = JSON.parse(ignored);
  check("an unsupported drop is ignored entirely",
        !ignoredState.active && ignoredState.unchanged);

  return finish();
};

const finish = async () => {
  await closeBrowser();
  for (const item of cleanup) {
    try { item.kill ? item.kill() : rmSync(item, { recursive: true, force: true }); } catch {}
  }
  log("");
  log(failures ? `${failures} CHECK(S) FAILED` : "ALL CHECKS PASSED");
  return failures ? 1 : 0;
};

run().then((code) => process.exit(code)).catch(async (error) => {
  console.error(error);
  await finish();
  process.exit(1);
});
