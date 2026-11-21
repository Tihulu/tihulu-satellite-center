# Integration tests

## `job_removal_cdp.mjs` — interactive DOM test for scheduled-job removal

Verifies, in a **real Chromium** over the DevTools Protocol, that the native-mode
scheduled-job removal UI behaves as the owner requested: an x on each armed-job card and
a "Clear N finished job(s)" control, with a removal in either place also removing the
record from the native calendar, while `pending` schedules are never silently dropped.

The test reproduces the Qt WebChannel bridge with a stub (no Qt, no installs), drives the
real `native_desktop_bridge.js` and `browser.js`, seeds a canned schedule store, clicks the
controls and asserts both the UI state and the exact backend commands (`clear_job` with the
record id, `clear_finished_jobs`).

### Run

    node tests/job_removal_cdp.mjs [appRoot]

* `appRoot` — a directory that contains the `satellite_center` package; defaults to
  `../usr/share/tihulu-satellite-center`.
* Requires Node >= 22 (global WebSocket) and `chromium-browser` (or `chromium`) on PATH.
  The script starts its own `satellite_center serve` instance and its own browser on
  localhost, and cleans both up on exit.

### Expected result

    PASS bridge initialised in the page
    PASS all three records render
    PASS bulk control counts only finished records :: Clear 2 finished jobs
    PASS every card has a dismiss control :: 3
    PASS pending card also offers cancel
    PASS x click accepted
    PASS x removes the record from the list
    PASS x removes the record from the calendar
    PASS x calls clear_job with the exact id
    PASS pending record untouched
    PASS bulk clear accepted
    PASS bulk clear removes every finished record
    PASS bulk clear leaves pending alone
    PASS bulk clear calls clear_finished_jobs
    PASS clear control disappears when nothing finished remains
    ALL CHECKS PASSED

### Notes

* Interactive-DOM harness, so it is intentionally NOT part of the pure-unit suites in
  `browser/*.test.mjs` (those need no browser).
* No hardware: the stubbed `status`/`clear_job`/`clear_finished_jobs` commands are
  answered locally; no SDR, USB or service is opened.
* The owner's real installation is never touched — the server runs on a throwaway
  `TIHULU_SATELLITE_DATA` directory inside a temporary HOME.
