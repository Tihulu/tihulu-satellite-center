#!/usr/bin/env python3
"""Behavioural test for the Qt desktop app's "Edit selected pending job".

Drives the real window offscreen: seeds a pending manual schedule, opens the
edit dialog the way an operator would and asserts BOTH the dialog's seeded
values reaching Station.edit_job and the dedicated feedback label for a
refused edit. Automatic missions keep their own editor and are routed to it.

    SAT_APP_ROOT=<dir containing satellite_center> python3 tests/test_desktop_edit_job.py

Nothing is installed; the window uses a disposable data directory and the
offscreen Qt platform. Station scheduling is stubbed; no receiver, USB device,
service or owner file is touched.
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

from PyQt5.QtCore import Qt                                          # noqa: E402
from PyQt5.QtWidgets import (QApplication, QDialog, QMessageBox,        # noqa: E402
                             QPushButton, QTreeWidgetItem)

from satellite_center import __main__ as app_main                    # noqa: E402
from satellite_center.desktop import SatelliteWindow                # noqa: E402

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


def main():
    data = Path(tempfile.mkdtemp(prefix="sat-edit-job-"))
    station = app_main.Station(data)
    app = QApplication(["desktop-edit-job-test"])
    window = SatelliteWindow(station, auto_poll=False)

    buttons = {}
    for button in window.findChildren(QPushButton):
        buttons.setdefault(button.text(), button)
    check("the calendar exposes 'Edit selected pending job'",
          "Edit selected pending job" in buttons)
    check("the automatic-mission editor is untouched",
          "Edit selected automatic job" in buttons)

    run_at = (datetime.now(timezone.utc) + timedelta(minutes=40)).replace(microsecond=0)
    station.jobs = [{"id": "e1e1e1e1e1e1", "satellite": "ISS (ZARYA)", "state": "pending",
                     "run_at": run_at.isoformat(), "capture_seconds": 60,
                     "aos": (run_at - timedelta(minutes=5)).isoformat(),
                     "los": (run_at + timedelta(minutes=15)).isoformat(),
                     "profile": "iss_sstv", "mode": "survey",
                     "sample_rate": 1024000, "estimated_bytes": 60000000,
                     "created_at": app_main.stamp(), "result": None}]
    station.save_jobs()
    window.refresh_status()
    pump(app, 2)
    item = next(i for i in [window.job_list.topLevelItem(k)
                            for k in range(window.job_list.topLevelItemCount())]
                if i.data(0, Qt.UserRole) == "e1e1e1e1e1e1")
    window.job_list.setCurrentItem(item)

    seen = {}

    def fake_edit(job_id, run_at_value, seconds):
        seen.update({"job_id": job_id, "run_at": run_at_value, "seconds": seconds})
        return dict(station.jobs[0], run_at=run_at_value, capture_seconds=seconds)

    station.edit_job = fake_edit
    real_exec = QDialog.exec_
    QDialog.exec_ = lambda self: QDialog.Accepted

    buttons["Edit selected pending job"].click()
    check("the dialog hands the seeded start and duration to the Station",
          wait_until(app, lambda: "seconds" in seen) and
          seen.get("seconds") == 60 and
          seen.get("job_id") == "e1e1e1e1e1e1", seen)
    if seen.get("run_at"):
        started = datetime.fromisoformat(seen["run_at"])
        check("the dialog's start round-trips to the job's UTC start",
              abs((started - run_at).total_seconds()) <= 1, seen["run_at"])
    pump(app, 2)
    check("the edit outcome is reported in the dedicated label",
          window.job_action_note.text().startswith("Edited ISS (ZARYA):"),
          window.job_action_note.text())

    # ---- a refused edit is reported, never swallowed ------------------------
    # The shared task-error path also raises a modal warning; record it instead
    # of blocking the offscreen harness (same approach as test_desktop_removal).
    warnings = []
    real_warning = QMessageBox.warning
    QMessageBox.warning = lambda *a, **k: warnings.append(a[-1] if a else "")

    def refusing_edit(job_id, run_at_value, seconds):
        raise ValueError("Edited start may not precede the predicted AOS")

    station.edit_job = refusing_edit
    window.job_list.setCurrentItem(window.job_list.topLevelItem(0))
    buttons["Edit selected pending job"].click()
    pump(app, 3)
    check("a refused edit is reported with the Station's reason",
          window.job_action_note.text().startswith("Edit refused:"),
          window.job_action_note.text())
    check("the refused schedule stays in the store",
          [j["id"] for j in station.jobs] == ["e1e1e1e1e1e1"])

    # ---- a finished record is not editable ------------------------------------
    station.jobs[0]["state"] = "completed"
    window.refresh_status()
    pump(app, 2)
    window.job_list.setCurrentItem(window.job_list.topLevelItem(0))
    seen.clear()
    buttons["Edit selected pending job"].click()
    pump(app, 2)
    check("a finished record is refused without touching the Station",
          not seen and window.job_action_note.text().startswith(
              "Only a pending schedule can be edited"),
          window.job_action_note.text())

    # ---- an automatic mission keeps its own editor ---------------------------
    station.jobs = [{"id": "m1m1m1m1m1m1", "satellite": "ISS (ZARYA)", "state": "pending",
                     "run_at": run_at.isoformat(), "capture_seconds": 60,
                     "aos": (run_at - timedelta(minutes=5)).isoformat(),
                     "los": (run_at + timedelta(minutes=15)).isoformat(),
                     "mission_id": "iss-sstv", "profile": "iss_sstv", "mode": "sstv_audio",
                     "sample_rate": 1024000, "estimated_bytes": 60000000,
                     "created_at": app_main.stamp(), "result": None}]
    station.save_jobs()
    window.refresh_status()
    pump(app, 2)
    window.job_list.setCurrentItem(window.job_list.topLevelItem(0))
    seen.clear()
    buttons["Edit selected pending job"].click()
    pump(app, 2)
    check("an automatic mission is routed to its own editor, not the manual one",
          not seen, seen)

    QDialog.exec_ = real_exec
    QMessageBox.warning = real_warning
    check("the refusal also reaches the operator's warning dialog",
          any("AOS" in str(w) for w in warnings), warnings)
    print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
