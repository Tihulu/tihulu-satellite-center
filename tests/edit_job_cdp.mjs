#!/usr/bin/env node
// Interactive DOM regression test for editing a pending native schedule from the
// armed-jobs list, driven in a real Chromium over the DevTools Protocol.
//
// It reproduces the Qt WebChannel bridge with a stub that PROXIES the status
// and edit commands to the app's real local companion server (no Qt, no
// installs), seeds a pending job into a throwaway data directory, then drives
// the real "Edit pending job" control exactly like an operator would.
//
//   node tests/edit_job_cdp.mjs [appRoot]
//
// Requirements: node >= 22 (global WebSocket) and chromium-browser on PATH.
// The companion's scheduler needs skyfield only if it must plan passes; this
// test seeds its own job, so set SAT_PYTHON to an interpreter with skyfield
// anyway (default /usr/bin/python3) to match the shipped app.
//
// Verified behaviour:
//   * the pending native job card offers "Edit pending job";
//   * submitting the form POSTs to /api/edit-job and the job's duration (and
//     only that) changes on the server;
//   * a start time the Station refuses (in the past) is reported in the card,
//     and the schedule on the server is unchanged.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import net from "node:net";
import { join } from "node:path";

const APP_ROOT = process.argv[2] || "/home/horseman/sat-scratch-700/fix/usr/share/tihulu-satellite-center";
// A random port can already be held by an unrelated browser or companion on this
// machine; connect-testing first keeps the harness on its own page.
async function freePort(from, span) {
  for (let attempt = 0; attempt < 80; attempt++) {
    const candidate = from + Math.floor(Math.random() * span);
    const taken = await new Promise((resolve) => {
      const socket = net.createConnection({ host: "127.0.0.1", port: candidate });
      const done = (value) => { socket.destroy(); resolve(value); };
      socket.setTimeout(400);
      socket.once("connect", () => done(true));
      socket.once("timeout", () => done(false));
      socket.once("error", () => done(false));
    });
    if (!taken) return candidate;
  }
  throw new Error("no free port found in range");
}
const PORT = await freePort(8841, 100);
const CDP = await freePort(9700, 200);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
let failures = 0;
let closeBrowser = async () => {};

const check = (name, ok, detail = "") => {
  log((ok ? "PASS " : "FAIL ") + name + (detail ? " :: " + detail : ""));
  if (!ok) failures++;
};

const cleanup = [];
const iso = (ms) => new Date(ms).toISOString();

const run = async () => {
  const data = mkdtempSync(join(tmpdir(), "sat-edit-cdp-"));
  const home = mkdtempSync(join(tmpdir(), "sat-edit-cdp-home-"));
  cleanup.push(data, home);
  // A pending job seeded straight into the companion's own store, so the card
  // under test is the app's real data, not a stubbed list.
  const runAt = Date.now() + 90 * 60 * 1000;
  writeFileSync(join(data, "schedule.json"), JSON.stringify([{
    id: "c0ffee1234ab", pass_id: "25544", satellite: "ISS (ZARYA)", family: "iss",
    profile: "iss_sstv", mode: "survey",
    aos: iso(runAt - 5 * 60 * 1000), los: iso(runAt + 10 * 60 * 1000),
    run_at: iso(runAt), capture_seconds: 60, sample_rate: 1024000,
    estimated_bytes: 60000000, state: "pending",
    created_at: iso(Date.now()), result: null,
  }]));
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
  const profile = mkdtempSync(join(tmpdir(), "sat-edit-cdp-prof-"));
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
      target = list.find((t) => t.type === "page" && t.url.indexOf(":" + PORT + "/") >= 0) || null;
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

  // ---- the Qt WebChannel stub, proxied to the real companion ---------------
  const stub = `
    const __c = document.createElement.bind(document);
    document.createElement = function (tag, ...rest) {
      const el = __c(tag, ...rest);
      if (String(tag).toLowerCase() === "script") {
        let _s = "";
        Object.defineProperty(el, "src", { configurable: true, get: () => _s,
          set: (v) => { _s = v; if (String(v).startsWith("qrc:")) setTimeout(() => { if (typeof el.onload === "function") el.onload(); }, 100); } });
      }
      return el;
    };
    window.qt = { webChannelTransport: { send: () => {}, onmessage: null } };
    window.QWebChannel = function (transport, cb) {
      const receiver = { reply: { connect: () => {} }, __cb: null,
        request: (rid, command, args) => {
          let a = {}; try { a = JSON.parse(args); } catch {}
          const ok = (result) => receiver.__cb && receiver.__cb(rid, JSON.stringify({ ok: true, result }));
          const fail = (error) => receiver.__cb && receiver.__cb(rid, JSON.stringify({ ok: false, error }));
          if (command === "status") {
            // Proxy the real companion status so the cards are its real jobs.
            fetch("/api/status", { cache: "no-store" }).then(r => r.json())
              .then(s => ok(s)).catch(e => fail(String(e)));
            return;
          }
          if (command === "plan_candidates") return ok({ candidates: [] });
          if (command === "edit_job") {
            fetch("/api/status", { cache: "no-store" }).then(r => r.json()).then(s =>
              fetch("/api/edit-job", { method: "POST", cache: "no-store",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ job_id: a.job_id, run_at: a.run_at,
                  duration_seconds: a.duration_seconds, csrf: s.csrf }) })
                .then(r => r.json().then(j => r.ok ? ok(j) : fail(j.error || String(r.status))))
                .catch(e => fail(String(e))));
            return;
          }
          if (command === "cancel_schedule" || command === "clear_job" ||
              command === "clear_finished_jobs") return ok({});
          return ok({});
        } };
      receiver.reply.connect = (fn) => { receiver.__cb = fn; };
      cb({ objects: { nativeReceiver: receiver } });
    };
  `;
  await ev(stub);
  const bridgeSource = await (await import("node:fs/promises")).readFile(
    join(APP_ROOT, "satellite_center", "native_desktop_bridge.js"), "utf8");
  await ev(bridgeSource);
  await sleep(3000);
  check("the native bridge is initialised in the page",
        await ev("Boolean(window.nativeWebBridgeReady)"));

  // ---- 1) the pending card offers the edit control --------------------------
  const card = await ev(`(() => {
    const el = document.querySelector('#jobs .scheduled-job');
    if (!el) return null;
    const edit = [...el.querySelectorAll('button')].find(b => /Edit pending job/.test(b.textContent));
    return JSON.stringify({
      title: el.querySelector('h4') ? el.querySelector('h4').textContent : null,
      hasEdit: Boolean(edit),
      buttons: [...el.querySelectorAll('button')].map(b => b.textContent.trim())
    });
  })()`);
  const cardState = card ? JSON.parse(card) : null;
  check("the seeded pending job renders as a native card", Boolean(cardState), String(card));
  check("the pending card offers 'Edit pending job'",
        Boolean(cardState && cardState.hasEdit), cardState && cardState.buttons.join("|"));

  // ---- 2) editing the duration lands on the server --------------------------
  const edited = await ev(`(async () => {
    const card = document.querySelector('#jobs .scheduled-job');
    const edit = [...card.querySelectorAll('button')].find(b => /Edit pending job/.test(b.textContent));
    edit.click();
    const form = card.querySelector('form.native-job-edit');
    if (!form) return 'no form';
    const seconds = form.querySelector('input[type="number"]');
    seconds.value = '90';
    const note = form.querySelector('p[role="status"]');
    form.querySelector('button[type="submit"]').click();
    for (let i = 0; i < 40; i++) {
      if (note && /Edited:|Edit refused:/.test(note.textContent)) break;
      await new Promise(r => setTimeout(r, 500));
    }
    return String(note && note.textContent || 'no note');
  })()`);
  check("submitting the form reports the edit", /Edited:/.test(edited), edited.slice(0, 140));
  const status = await (await fetch(`http://127.0.0.1:${PORT}/api/status`)).json();
  const job = (status.jobs || []).find(j => j.id === "c0ffee1234ab");
  check("the server stored the edited duration",
        Boolean(job) && job.capture_seconds === 90, job ? job.capture_seconds : "missing");
  check("the edit left the rest of the schedule alone",
        Boolean(job) && job.mode === "survey" && job.profile === "iss_sstv" &&
        job.sample_rate === 1024000 && job.state === "pending",
        job ? JSON.stringify(job).slice(0, 120) : "missing");

  // ---- 3) a start the Station refuses is reported, the job is untouched ------
  const refused = await ev(`(async () => {
    const card = document.querySelector('#jobs .scheduled-job');
    const edit = [...card.querySelectorAll('button')].find(b => /Edit pending job/.test(b.textContent));
    edit.click();
    const form = card.querySelector('form.native-job-edit');
    const time = form.querySelector('input[type="datetime-local"]');
    const yesterday = new Date(Date.now() - 24 * 3600 * 1000);
    const pad = n => String(n).padStart(2, "0");
    time.value = yesterday.getFullYear() + "-" + pad(yesterday.getMonth() + 1) + "-" +
      pad(yesterday.getDate()) + "T" + pad(yesterday.getHours()) + ":" + pad(yesterday.getMinutes());
    const note = form.querySelector('p[role="status"]');
    form.querySelector('button[type="submit"]').click();
    for (let i = 0; i < 40; i++) {
      if (note && /Edit refused:/.test(note.textContent)) break;
      await new Promise(r => setTimeout(r, 500));
    }
    return String(note && note.textContent || 'no note');
  })()`);
  check("a start in the past is refused and reported",
        /Edit refused:/.test(refused), refused.slice(0, 140));
  const after = await (await fetch(`http://127.0.0.1:${PORT}/api/status`)).json();
  const jobAfter = (after.jobs || []).find(j => j.id === "c0ffee1234ab");
  check("the refused edit left the schedule unchanged",
        Boolean(jobAfter) && jobAfter.capture_seconds === 90 &&
        jobAfter.run_at === job.run_at, jobAfter ? jobAfter.run_at : "missing");

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
