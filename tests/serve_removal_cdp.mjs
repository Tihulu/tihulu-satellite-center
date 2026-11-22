#!/usr/bin/env node
// Interactive DOM test for the HTTP serve-mode removal surface: the reception
// calendar's per-card "Remove record" control and the confirmed
// "Clear finished records" button, driven through the REAL Station backend over
// the REAL /api/clear* routes (no stubbed transport, no fake schedule store).
//
//   node tests/serve_removal_cdp.mjs [appRoot]
//
//   appRoot   directory whose parent contains the `satellite_center` package
//             (default: ../usr/share/tihulu-satellite-center)
//
// Requirements: node >= 22 (global WebSocket) and chromium/chromium-browser on
// PATH. Nothing else is installed; the test spawns its own server (on the app's
// own default port, which is what enables the companion API in this page) and
// browser, each phase with its own disposable HOME and data directory.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const APP_ROOT = process.argv[2] || "../usr/share/tihulu-satellite-center";
// auto_reception_ui.mjs only enables the companion API when the page is served
// from the app's default port, so this surface can only be tested there.
const PORT = 8791;
const CDP = 9400 + Math.floor(Math.random() * 400);
const BASE = `http://127.0.0.1:${PORT}`;
const PAGE_URL = `${BASE}/browser/reception.html`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
let failures = 0;
const check = (name, ok, detail = "") => {
  log((ok ? "PASS " : "FAIL ") + name + (detail ? " :: " + detail : ""));
  if (!ok) failures++;
};
const cleanup = [];
const pad = (n) => String(n).padStart(2, "0");
// tomorrow, local time, with the machine's own UTC offset: always in the future
// (the real scheduler never fires it during the test) and always parsed as an
// aware datetime, exactly like the app's own schedule() writes run_at.
const localOffset = () => {
  const z = new Date().getTimezoneOffset();
  const s = z <= 0 ? "+" : "-"; const a = Math.abs(z);
  return `${s}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
};
const tomorrow = (h, m = 0) => {
  const d = new Date(); d.setDate(d.getDate() + 1);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(h)}:${pad(m)}:00${localOffset()}`;
};
const DAY = tomorrow(12).slice(0, 10);       // local calendar day of the seeds
const RECORD = (id, satellite, state, hour, minute) => ({
  id, satellite, state,
  run_at: tomorrow(hour, minute),
  capture_seconds: 90, estimated_bytes: 190000000,
  profile: "iss", mode: "iq_and_fm_wav", sample_rate: 1024000,
  aos: tomorrow(8, 30), los: tomorrow(8, 45),
  created_at: tomorrow(7, 0), result: null, message: null,
});

let serverHandle = null;
const stopCurrent = async () => {
  if (serverHandle) { await stopServer(serverHandle); serverHandle = null; }
};
const startServer = async (jobs) => {
  const data = mkdtempSync(join(tmpdir(), "sat-serve-data-"));
  const home = mkdtempSync(join(tmpdir(), "sat-serve-home-"));
  writeFileSync(join(data, "schedule.json"), JSON.stringify(jobs, null, 2));
  const server = spawn("python3", ["-m", "satellite_center", "serve", "--host", "127.0.0.1",
    "--port", String(PORT)], {
    env: { ...process.env, PYTHONPATH: APP_ROOT, HOME: home, TMPDIR: data,
           TIHULU_SATELLITE_DATA: data, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["ignore", "ignore", "pipe"], detached: false,
  });
  cleanup.push(server, data, home);
  server.stderr.on("data", (d) => process.stderr.write("[serve] " + d));
  for (let i = 0; i < 180; i++) {            // cold numpy/skyfield imports are slow
    try { const r = await fetch(`${BASE}/api/status`); if (r.ok) { serverHandle = server; return server; } } catch {}
    await sleep(500);
  }
  throw new Error(`serve never answered on ${PORT} (appRoot=${APP_ROOT})`);
};
const stopServer = async (server) => {
  server.kill("SIGTERM");
  for (let i = 0; i < 60; i++) {
    try { await fetch(`${BASE}/api/status`); await sleep(500); } catch { return; }
  }
};

const run = async () => {
  // ---- one headless browser for every phase ---------------------------------
  const profile = mkdtempSync(join(tmpdir(), "sat-serve-prof-"));
  cleanup.push(profile);
  const browser = spawn("chromium-browser", [
    "--headless", "--no-sandbox", "--disable-gpu", "--no-first-run",
    "--no-default-browser-check", `--remote-debugging-port=${CDP}`,
    `--user-data-dir=${profile}`, PAGE_URL,
  ], { stdio: "ignore" });
  cleanup.push(browser);
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
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    }
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const n = ++id; pending.set(n, { res, rej }); ws.send(JSON.stringify({ id: n, method, params }));
  });
  const ev = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("page error: " + JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  };
  // The destructive bulk clear asks for confirmation; every phase auto-accepts it
  // (the operator's intended choice under test), and the questions themselves are
  // counted so the confirmation can be proven to have been asked.
  await send("Page.addScriptToEvaluateOnNewDocument", { source:
    "window.__confirmAsked = 0; window.confirm = () => { window.__confirmAsked++; return window.__confirmAnswer !== false; };" });
  await send("Page.enable");

  const navigate = async () => {
    await send("Page.navigate", { url: PAGE_URL });
    for (let i = 0; i < 120; i++) {          // module graph + first /api/status
      try {
        if (await ev("Boolean(document.getElementById('auto-calendar-grid')?.querySelector('.calendar-summary'))")) break;
      } catch {}
      await sleep(1000);
    }
    await ev(`(() => { const d = document.getElementById('auto-calendar-date');
      d.value = ${JSON.stringify(DAY)}; d.dispatchEvent(new Event('change')); return 'day-set'; })()`);
  };
  const cards = () => ev(`JSON.stringify([...document.querySelectorAll('#auto-calendar-grid .sat-calendar-job')].map(c => c.dataset.jobId))`);
  const buttonsOf = (jobId) => ev(`(() => { const card = [...document.querySelectorAll('#auto-calendar-grid .sat-calendar-job')].find(c => c.dataset.jobId === ${JSON.stringify(jobId)});
      return card ? [...card.querySelectorAll('button')].map(b => b.textContent.trim()).join(';') : 'no-card'; })()`);
  const message = () => ev(`document.getElementById('auto-mission-status').textContent.trim()`);
  const storeIds = () => ev(`(async () => { const s = await (await fetch('/api/status')).json(); return JSON.stringify((s.jobs||[]).map(j => j.id)); })()`);
  const api = async (path, data) => {
    const s = await (await fetch(`${BASE}/api/status`)).json();
    const r = await fetch(`${BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ csrf: s.csrf, ...data }) });
    return { ok: r.ok, body: await r.json() };
  };

  // ---- phase A: the bulk control confirms, then clears finished records ------
  await startServer([
    RECORD("s1", "ISS (ZARYA)", "completed", 9),
    RECORD("s2", "ISS (ZARYA)", "pending", 16),
    RECORD("s4", "METEOR-M 2", "cancelled", 8, 30),
  ]);
  await navigate();
  check("the reception calendar renders the seeded jobs",
        JSON.parse(await cards()).join(",") === "s1,s2", await cards());
  check("a cancelled record stays hidden from the calendar",
        !JSON.parse(await cards()).includes("s4"), await cards());
  check("the finished card offers a removal control",
        (await buttonsOf("s1")).includes("Remove record"), await buttonsOf("s1"));
  check("the pending card offers no removal control (cancel only)",
        (await buttonsOf("s2")).includes("Cancel job") && !(await buttonsOf("s2")).includes("Remove record"),
        await buttonsOf("s2"));
  check("the serve surface exposes the bulk clear control",
        await ev("Boolean(document.getElementById('auto-calendar-clear-finished'))"));
  // refusing the confirmation must change nothing
  await ev(`window.__confirmAnswer = false; window.__confirmAsked = 0;
    document.getElementById('auto-calendar-clear-finished').click(); 'clicked'`);
  await sleep(1200);
  check("the bulk clear asks before removing anything",
        (await ev("window.__confirmAsked")) >= 1, String(await ev("window.__confirmAsked")));
  check("refusing the confirmation leaves every record in place",
        JSON.parse(await cards()).join(",") === "s1,s2", await cards());
  // accepting it removes exactly the finished records
  await ev(`window.__confirmAnswer = true;
    document.getElementById('auto-calendar-clear-finished').click(); 'clicked'`);
  for (let i = 0; i < 30; i++) {
    if (!JSON.parse(await cards()).includes("s1")) break;
    await sleep(1000);
  }
  check("the bulk clear reports what it removed",
        (await message()) === "Cleared 2 finished record(s); 1 schedule(s) remain.",
        await message());
  check("the bulk clear drops finished records (incl. cancelled) and keeps the live one",
        JSON.parse(await cards()).join(",") === "s2", await cards());
  check("the store agrees with the calendar",
        JSON.parse(await storeIds()).join(",") === "s2", await storeIds());

  // ---- phase B: one finished record removed from the calendar ----------------
  await stopCurrent();
  await startServer([
    RECORD("s1", "ISS (ZARYA)", "completed", 9),
    RECORD("s2", "ISS (ZARYA)", "pending", 16),
  ]);
  await navigate();
  check("phase B calendar shows both records again",
        JSON.parse(await cards()).join(",") === "s1,s2", await cards());
  await ev(`(() => { const card = [...document.querySelectorAll('#auto-calendar-grid .sat-calendar-job')].find(c => c.dataset.jobId === 's1');
    const btn = card && card.querySelector('button[aria-label="Remove this job from the list and calendar"]');
    if (!btn) return 'no-remove-control'; btn.click(); return 'clicked'; })()`);
  for (let i = 0; i < 30; i++) {
    if (!JSON.parse(await cards()).includes("s1")) break;
    await sleep(1000);
  }
  check("the calendar removal reports the removed record",
        (await message()) === "Removed the finished record for ISS (ZARYA).", await message());
  check("the record leaves the calendar and the store, the live schedule stays",
        JSON.parse(await cards()).join(",") === "s2" && JSON.parse(await storeIds()).join(",") === "s2",
        `${await cards()} / ${await storeIds()}`);

  // ---- phase C: the real backend refuses live schedules over HTTP ------------
  const refused = await api("/api/clear-job", { job_id: "s2" });
  check("the HTTP route refuses a pending schedule",
        refused.ok === false && /A pending schedule cannot be cleared/.test(String(refused.body.error || "")),
        JSON.stringify(refused.body));
  const bulk = await api("/api/clear-finished-jobs", {});
  check("the bulk route keeps live schedules",
        bulk.ok === true && bulk.body.cleared === 0 && bulk.body.remaining === 1,
        JSON.stringify(bulk.body));
  check("the refused call left the pending record on the calendar",
        JSON.parse(await cards()).join(",") === "s2", await cards());

  // ---- phase D: every terminal state is removable through the HTTP surface ---
  await stopCurrent();
  await startServer([
    RECORD("t1", "METEOR-M 2", "interrupted", 10),
    RECORD("t2", "METEOR-M 2", "cancelled", 8, 30),
    RECORD("p1", "ISS (ZARYA)", "pending", 16),
  ]);
  const r1 = await api("/api/clear-job", { job_id: "t1" });
  check("an interrupted record is removable",
        r1.ok === true && r1.body.cleared === "t1" && r1.body.remaining === 2,
        JSON.stringify(r1.body));
  const r2 = await api("/api/clear-job", { job_id: "t2" });
  check("a cancelled record is removable",
        r2.ok === true && r2.body.cleared === "t2" && r2.body.remaining === 1,
        JSON.stringify(r2.body));
  const r3 = await api("/api/clear-job", { job_id: "p1" });
  check("the pending schedule is still refused",
        r3.ok === false && /A pending schedule cannot be cleared/.test(String(r3.body.error || "")),
        JSON.stringify(r3.body));
  const r4 = await api("/api/clear-finished-jobs", {});
  check("the bulk route finds nothing left to clear",
        r4.ok === true && r4.body.cleared === 0 && r4.body.remaining === 1,
        JSON.stringify(r4.body));
  const left = await (await fetch(`${BASE}/api/status`)).json();
  check("only the live schedule remains in the store",
        (left.jobs || []).map((j) => j.id).join(",") === "p1",
        (left.jobs || []).map((j) => j.id).join(","));

  log(`\n${failures === 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED"}`);
  return failures;
};

run()
  .then((f) => { for (const c of cleanup) { try { c.kill ? c.kill() : rmSync(c, { recursive: true, force: true }); } catch {} } process.exit(f === 0 ? 0 : 1); })
  .catch((e) => { console.error("HARNESS ERROR:", e.message); for (const c of cleanup) { try { c.kill ? c.kill() : rmSync(c, { recursive: true, force: true }); } catch {} } process.exit(2); });
