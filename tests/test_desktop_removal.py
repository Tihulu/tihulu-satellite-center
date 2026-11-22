#!/usr/bin/env python3
"""Behavioural test for the Qt desktop fallback's scheduled-job removal surface.

The desktop-widgets mode gained "Remove selected record" and "Clear finished
records" with the independent-review fixes, but no test had ever run them: the
code only compiled. This drives the real window offscreen, presses the buttons
the way an operator would, and asserts BOTH the on-screen feedback and the
Station's resulting state (which is what the shipped app persists).

    SAT_APP_ROOT=<dir containing satellite_center> python3 tests/test_desktop_removal.py

Nothing is installed; the window uses a disposable data directory and the
offscreen Qt platform.
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = os.environ.get("SAT_APP_ROOT", "/usr/share/tihulu-satellite-center")
sys.path.insert(0, APP_ROOT)

from PyQt5.QtCore import Qt                      # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from satellite_center import __main__ as app_main  # noqa: E402
from satellite_center.desktop import SatelliteWindow  # noqa: E402

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)) if detail else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def pump(app, seconds):
    """Run the Qt event loop just enough for worker signals to be delivered."""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(app, predicate, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def job(job_id, state, hour, minute=0):
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1, hours=0)
    start = start.replace(hour=hour, minute=minute)
    return {"id": job_id, "satellite": "ISS (ZARYA)", "state": state,
            "run_at": app_main.stamp(start), "capture_seconds": 90,
            "estimated_bytes": 190000000, "profile": "iss", "mode": "iq_and_fm_wav",
            "sample_rate": 1024000, "created_at": app_main.stamp(), "result": None}


def ids(station):
    return [j["id"] for j in station.jobs]


def main():
    data = Path(tempfile.mkdtemp(prefix="sat-desktop-test-"))
    station = app_main.Station(data)
    app = QApplication(["desktop-removal-test"])
    window = SatelliteWindow(station, auto_poll=False)

    buttons = {}
    for button in window.findChildren(QPushButton):
        buttons.setdefault(button.text(), button)
    check("the Qt fallback exposes 'Remove selected record'",
          "Remove selected record" in buttons)
    check("the Qt fallback exposes 'Clear finished records'",
          "Clear finished records" in buttons)
    check("the existing job controls are untouched",
          "Cancel selected pending job" in buttons and "Edit selected automatic job" in buttons)

    def seed(jobs):
        station.jobs = [dict(j) for j in jobs]
        station.save_jobs()
        window.refresh_status()
        return wait_until(app, lambda: sorted(window._calendar_jobs and
                                              [j["id"] for j in window._calendar_jobs] or []) ==
                          sorted(j["id"] for j in jobs))

    # ---- a finished record is removable from the list ------------------------
    check("the seeded records reach the job list", seed([job("d1", "completed", 9),
                                                        job("d2", "pending", 16)]))
    check("the job list shows both records (cancelled hidden)",
          window.job_list.topLevelItemCount() == 2,
          window.job_list.topLevelItemCount())
    item = next(i for i in [window.job_list.topLevelItem(k)
                            for k in range(window.job_list.topLevelItemCount())]
                if i.data(0, Qt.UserRole) == "d1")
    window.job_list.setCurrentItem(item)
    buttons["Remove selected record"].click()
    check("removing a finished record drops it from the persisted store",
          wait_until(app, lambda: ids(station) == ["d2"]), ids(station))
    pump(app, 2)
    check("the job list follows the store",
          window.job_list.topLevelItemCount() == 1 and
          window.job_list.topLevelItem(0).data(0, Qt.UserRole) == "d2",
          window.job_list.topLevelItemCount())

    # ---- a live schedule is refused with the Station's own reason ------------
    window.job_list.setCurrentItem(window.job_list.topLevelItem(0))
    before = ids(station)
    buttons["Remove selected record"].click()
    pump(app, 2)
    check("a pending schedule is refused",
          window.job_action_note.text().startswith("A pending schedule cannot be removed"),
          window.job_action_note.text())
    check("the refused schedule stays in the store", ids(station) == before, ids(station))

    # ---- no selection gives a clear instruction, not a crash -----------------
    window.job_list.setCurrentItem(None)
    buttons["Remove selected record"].click()
    pump(app, 1)
    check("no selection is reported, not crashed",
          window.job_action_note.text().startswith("Select a finished record"), window.job_action_note.text())

    # ---- the bulk clear asks first, then clears only finished records --------
    check("re-seeding for the bulk case", seed([job("d2", "pending", 16),
                                               job("d3", "completed", 9)]))
    asked = []
    real_question = QMessageBox.question

    def fake_question(*args, **kwargs):
        asked.append(args[1] if len(args) > 1 else kwargs.get("title", ""))
        return next(answers)

    answers = iter([QMessageBox.No, QMessageBox.Yes])
    QMessageBox.question = fake_question
    buttons["Clear finished records"].click()
    pump(app, 2)
    check("the bulk clear asks before removing anything", len(asked) >= 1, asked)
    check("refusing the confirmation leaves both records",
          sorted(ids(station)) == ["d2", "d3"], ids(station))
    buttons["Clear finished records"].click()
    check("accepting clears only the finished record",
          wait_until(app, lambda: ids(station) == ["d2"]), ids(station))
    pump(app, 2)
    check("the bulk clear reports what it removed",
          window.job_action_note.text() == "Removed 1 finished record(s); 1 schedule(s) still listed.",
          window.job_action_note.text())
    check("the job list follows the store after the bulk clear",
          window.job_list.topLevelItemCount() == 1, window.job_list.topLevelItemCount())
    QMessageBox.question = real_question

    # ---- a Station error is surfaced, never swallowed ------------------------
    station.jobs = [dict(job("d4", "completed", 9))]
    window.refresh_status()
    pump(app, 2)
    window.job_list.setCurrentItem(window.job_list.topLevelItem(0))
    real_clear = station.clear_job
    station.clear_job = lambda job_id: (_ for _ in ()).throw(ValueError("Schedule not found"))
    # A failing task is reported through the app's own error path: the status bar
    # plus a modal QMessageBox.warning. The dialog is recorded instead of shown,
    # because nobody can dismiss it in the offscreen harness.
    warnings = []
    real_warning = QMessageBox.warning

    def fake_warning(*args, **kwargs):
        warnings.append(args[2] if len(args) > 2 else kwargs.get("text", ""))
        return QMessageBox.Ok

    QMessageBox.warning = fake_warning
    buttons["Remove selected record"].click()
    surfaced = wait_until(app, lambda: any("Schedule not found" in w for w in warnings)
                          or "Schedule not found" in window.statusBar().currentMessage())
    QMessageBox.warning = real_warning
    station.clear_job = real_clear
    check("a backend error reaches the operator", surfaced,
          "dialog=%r status=%r" % (warnings[:1], window.statusBar().currentMessage()))

    print("\n%s" % ("ALL CHECKS PASSED" if FAIL == 0 else "%d CHECK(S) FAILED" % FAIL))
    code = 1 if FAIL else 0
    try:
        window.close()
        station.close()
        app.quit()
    except Exception:
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
