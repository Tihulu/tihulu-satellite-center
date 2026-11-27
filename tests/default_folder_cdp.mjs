#!/usr/bin/env node
// Interactive DOM regression test for the global default recording folder, driven
// in a real Chromium over the DevTools Protocol against the app own local
// companion server (no Qt, no installs, throwaway data and home directories).
//
// It drives the real controls the operator sees - the default-folder input plus
// Set as default folder and Clear default folder - and asserts both what the page
// reports and what the companion itself says, so a UI-only change cannot pass
// while the persisted value is wrong.
//
//   node tests/default_folder_cdp.mjs [appRoot]
//
// Requirements: node >= 22 (global WebSocket) and chromium-browser on PATH.
// SAT_PYTHON selects the companion interpreter (default /usr/bin/python3).
//
// Verified behaviour:
//   * the companion page shows the default-folder controls, not a note;
//   * a valid folder is applied, the page reports the resolved path and the
//     companion reports it too;
//   * an escaping folder is refused with the Station reason and the stored
//     default is unchanged;
//   * clearing removes the persisted value and returns captures to the default.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import net from "node:net";
import { join } from "node:path";

const APP_ROOT = process.argv[2] || "/home/horseman/sat-scratch-700/fix/usr/share/tihulu-satellite-center";
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
const PORT = await freePort(8861, 80);
const CDP = await freePort(9750, 150);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
let failures = 0;
const check = (name, ok, detail = "") => {
  log((ok ? "PASS " : "FAIL ") + name + (detail ? " :: " + String(detail).slice(0, 200) : ""));
  if (!ok) failures++;
};
const cleanup = [];
let aborted = false;
const run = async () => {
  const data = mkdtempSync(join(tmpdir(), "sat-default-folder-cdp-"));
  const home = mkdtempSync(join(tmpdir(), "sat-default-folder-cdp-home-"));
  cleanup.push(data, home);
  const SERVER_PYTHON = process.env.SAT_PYTHON || "/usr/bin/python3";
  const server = spawn(SERVER_PYTHON, ["-m", "satellite_center", "serve", "--host", "127.0.0.1",
    "--port", String(PORT)], {
    env: { ...process.env, PYTHONPATH: APP_ROOT, HOME: home, TMPDIR: data,
           TIHULU_SATELLITE_DATA: data, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["ignore", "ignore", "pipe"],
  });
  cleanup.push(server);
  server.stderr.on("data", (d) => process.stderr.write("[serve] " + d));
  let up = false;
  for (let i = 0; i < 180; i++) {
    try { const r = await fetch("http://127.0.0.1:" + PORT + "/browser/index.html"); if (r.ok) { up = true; break; } } catch {}
    await sleep(500);
  }
  if (!up) throw new Error("serve never answered on port " + PORT + " (appRoot=" + APP_ROOT + ")");
  const status = async () => (await (await fetch("http://127.0.0.1:" + PORT + "/api/status")).json());
  // The label is written by the page after its request settles, so wait for it.
  const labelText = async () => {
    const value = await evaluate(`document.getElementById("default-folder-label") ? document.getElementById("default-folder-label").textContent : ""`);
    return String(value || "");
  };
  const waitForLabel = async (needle, attempts = 40) => {
    for (let i = 0; i < attempts; i++) {
      const text = await labelText();
      if (text.indexOf(needle) >= 0) return text;
      await sleep(300);
    }
    return labelText();
  };
  const stored = () => {
    const file = join(data, "default_recording_dir.json");
    return existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : null;
  };
  check("the companion starts with no default recording folder",
        (await status()).hardware.default_recording_folder === null);

  const profile = mkdtempSync(join(tmpdir(), "sat-default-folder-cdp-prof-"));
  cleanup.push(profile);
  const browser = spawn("chromium-browser", [
    "--headless", "--no-sandbox", "--disable-gpu",
    "--remote-debugging-port=" + CDP, "--user-data-dir=" + profile,
    "http://127.0.0.1:" + PORT + "/browser/index.html",
  ], { stdio: "ignore" });
  cleanup.push(browser);
  await sleep(6000);
  let target = null;
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch("http://127.0.0.1:" + CDP + "/json/list")).json();
      target = list.find((t) => t.type === "page" && t.url.indexOf(":" + PORT + "/browser/index.html") >= 0) || null;
      if (target) break;
    } catch {}
    await sleep(1000);
  }
  if (!target) throw new Error("no chromium page target on " + CDP + " after 60 s");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const packet = JSON.parse(event.data);
    if (packet.method === "Runtime.exceptionThrown") log("PAGE EXCEPTION " + JSON.stringify(packet.params).slice(0, 400));
    if (!packet.id || !pending.has(packet.id)) return;
    const entry = pending.get(packet.id);
    pending.delete(packet.id);
    clearTimeout(entry.timer);
    if (packet.error) entry.reject(new Error(packet.method + " " + JSON.stringify(packet.error)));
    else entry.resolve(packet.result);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error("CDP timeout " + method)); }, 20000);
    pending.set(id, { resolve, reject, timer });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const reply = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (reply.exceptionDetails) throw new Error("page error: " + JSON.stringify(reply.exceptionDetails).slice(0, 300));
    return reply.result.value;
  };
  await send("Runtime.enable");
  for (let i = 0; i < 60; i++) {
    const seen = await evaluate(`(() => ({ controls: Boolean(document.getElementById("default-folder-controls")), note: Boolean(document.getElementById("default-folder-browser-note")), input: Boolean(document.getElementById("default-folder-input")) }))()`);
    if (seen.controls) break;
    await sleep(500);
  }
  const surface = await evaluate(`(() => ({ controls: Boolean(document.getElementById("default-folder-controls")), input: Boolean(document.getElementById("default-folder-input")), note: Boolean(document.getElementById("default-folder-browser-note")), buttons: [...document.querySelectorAll("#default-folder-controls button")].map(b => b.textContent) }))()`);
  check("the companion page offers the default-folder controls, not a note",
        surface.controls && surface.input && !surface.note, JSON.stringify(surface));
  check("both controls are present",
        surface.buttons.indexOf("Set as default folder") >= 0
        && surface.buttons.indexOf("Clear default folder") >= 0, surface.buttons.join("|"));

  await evaluate(`document.getElementById("default-folder-input").value = "ISS-DEFAULT"`);
  await evaluate(`[...document.querySelectorAll("#default-folder-controls button")].find(b => b.textContent === "Set as default folder").click()`);
  let applied = null;
  for (let i = 0; i < 40; i++) {
    applied = (await status()).hardware.default_recording_folder;
    if (applied) break;
    await sleep(300);
  }
  check("setting a folder applies it on the companion",
        typeof applied === "string" && applied.indexOf("captures") >= 0 && applied.indexOf("ISS-DEFAULT") > 0, applied);
  check("the page label reports the applied folder",
        (await waitForLabel("ISS-DEFAULT")).indexOf("ISS-DEFAULT") > 0);
  check("the value is persisted for the next start", stored() === applied, stored());

  await evaluate(`document.getElementById("default-folder-input").value = "../escape"`);
  await evaluate(`[...document.querySelectorAll("#default-folder-controls button")].find(b => b.textContent === "Set as default folder").click()`);
  const refused = await waitForLabel("Default folder not changed");
  check("an escaping folder is refused with the Station reason",
        refused.indexOf("Default folder not changed") >= 0 && refused.length > 40, refused);
  check("the refused folder leaves the stored default unchanged", stored() === applied, stored());

  await evaluate(`[...document.querySelectorAll("#default-folder-controls button")].find(b => b.textContent === "Clear default folder").click()`);
  let cleared = "pending";
  for (let i = 0; i < 40; i++) {
    cleared = (await status()).hardware.default_recording_folder;
    if (cleared === null) break;
    await sleep(300);
  }
  check("clearing removes the default on the companion", cleared === null, String(cleared));
  check("the page label returns to the captures folder",
        (await waitForLabel("No default recording folder")).indexOf("No default recording folder") >= 0);
  await ws.close();
  return failures ? 1 : 0;
};
const finish = async () => {
  for (const item of cleanup) {
    try { item.kill ? item.kill("SIGKILL") : rmSync(item, { recursive: true, force: true }); } catch {}
  }
  log(aborted ? "HARNESS ABORTED before the checks completed" : (failures ? failures + " CHECK(S) FAILED" : "ALL CHECKS PASSED"));
  return failures ? 1 : 0;
};
run().then(async (code) => { await finish(); process.exit(code); })
  .catch(async (error) => { aborted = true; log("HARNESS ERROR: " + String(error && error.message || error)); await finish(); process.exit(1); });
