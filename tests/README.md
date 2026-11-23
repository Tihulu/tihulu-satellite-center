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

## `recording_dir_cdp.mjs` — per-job recording folder and analysis drag & drop

Interactive DOM test, in a **real Chromium** over the DevTools Protocol, against the app's
own local companion server (`satellite_center serve` on a throwaway data directory). It
verifies the two owner-requested features end to end:

* the native schedule form offers a **recording folder** field, and the companion stores
  the chosen subdirectory on the job record (`output_dir`, resolved under `captures/`);
* an invalid folder name (`../escape`, spaces, overlong) is refused client-side, before
  any request leaves the page;
* a recording **dropped onto an analysis section** is handed to that section's own file
  input exactly like the picker (CU8 → `#iq-file` + quick analysis, WAV/CU8/PNG on the
  images section → their decode inputs), with a visible `drop-active` affordance while
  dragging, and unsupported drops are ignored entirely.

### Run

    node tests/recording_dir_cdp.mjs [appRoot]

* Requires Node >= 22 (global WebSocket), `chromium-browser` (or `chromium`) on PATH and
  reachable CelesTrak (the browser pass planner downloads public TLEs). Scheduling needs
  the optional `skyfield` dependency on the server side: set `SAT_PYTHON` to an
  interpreter that has it (default `/usr/bin/python3`).
* The port is picked randomly per run, so stale browser or server instances are never
  adopted.

### Expected result

    PASS the page exposes both analysis sections
    PASS predicted pass cards render (TLE reachable) :: 15
    PASS an ISS pass card is available for scheduling
    PASS the ISS card carries TLE geometry
    PASS the schedule form offers a recording-folder field (native companion)
    PASS scheduling with a folder is accepted by the companion
    PASS the chosen folder is stored on the job record :: .../captures/ISS-SSTV-2026-11
    PASS a traversal folder name is refused client-side
    PASS dragging a supported recording highlights the section
    PASS a dropped CU8 is handed to the analyzer's own file input
    PASS the highlight clears after the drop
    PASS a WAV dropped on the images section reaches the SSTV input
    PASS an unsupported drop is ignored entirely
    ALL CHECKS PASSED

### Notes

* No hardware: only scheduling (which persists a job) and the local UI are exercised.
* The owner's real installation is never touched — the server runs on a throwaway
  `TIHULU_SATELLITE_DATA` directory inside a temporary HOME, on a random port.

## `test_desktop_drop.py` — Qt analysis drop target and folder picker

    SAT_APP_ROOT=<dir containing satellite_center> python3 tests/test_desktop_drop.py

Drives the real Qt window offscreen (`QT_QPA_PLATFORM=offscreen`, disposable data
directory) and asserts both surfaces of the desktop app:

* dropping a real local `.cu8` / `.wav` onto the analysis section selects it and opens the
  matching step (analyzer for CU8, decoder for WAV); unsupported files are ignored, and
  `dragEnterEvent` only accepts recordings;
* **Arm selected RF capture** passes the chosen folder (or nothing, for the default) to
  `Station.schedule` / `Station.schedule_chain`, the operator sees the folder in the
  feedback label, and the job list carries a **Recording folder** column.

The QDropEvent payload must outlive the call (PyQt does not give the event ownership of
the QMimeData), which the harness handles explicitly. Station scheduling is stubbed; no
receiver, USB or service is touched. Expected: `13 passed, 0 failed`.
