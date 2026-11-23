#!/usr/bin/env python3
"""Behavioural test for the Qt desktop app's analysis drop target and the
per-job recording folder picker.

Drives the real window offscreen: drops real local files onto the analysis
section the way an operator would, and arms a schedule with an explicit
recording folder, asserting both the on-screen state and the exact arguments
the Station receives.

    SAT_APP_ROOT=<dir containing satellite_center> python3 tests/test_desktop_drop.py

Nothing is installed; the window uses a disposable data directory, the
offscreen Qt platform and stubbed Station scheduling calls. No receiver, USB
device or owner file is touched.
"""
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = os.environ.get("SAT_APP_ROOT", "/usr/share/tihulu-satellite-center")
sys.path.insert(0, APP_ROOT)

from PyQt5.QtCore import QMimeData, QPointF, Qt, QUrl              # noqa: E402
from PyQt5.QtGui import QDropEvent                                 # noqa: E402
from PyQt5.QtWidgets import QApplication, QTreeWidgetItem          # noqa: E402

from satellite_center import __main__ as app_main                  # noqa: E402
from satellite_center.desktop import SatelliteWindow, RecordingDropArea  # noqa: E402

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


# PyQt does not give the event ownership of the payload: keep a reference so
# the QMimeData outlives the dropEvent call (a real Qt drag does this itself).
KEEP_ALIVE = []


def drop_event(path, action=Qt.CopyAction):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    KEEP_ALIVE.append(mime)
    return QDropEvent(QPointF(10, 10), action, mime, Qt.LeftButton, Qt.NoModifier,
                      QDropEvent.Drop)


def main():
    data = Path(tempfile.mkdtemp(prefix="sat-desktop-drop-"))
    scratch = Path(tempfile.mkdtemp(prefix="sat-desktop-drop-files-"))
    station = app_main.Station(data)
    app = QApplication(["desktop-drop-test"])
    window = SatelliteWindow(station, auto_poll=False)

    areas = window.findChildren(RecordingDropArea)
    check("the analysis section is a drop target", len(areas) == 1, len(areas))
    area = areas[0]

    cu8 = scratch / "TIHULU_ISS-SSTV-CANDIDATE_20261123T120000Z_437550000Hz_IQ.cu8"
    cu8.write_bytes(b"\x01\x02\x03\x04" * 64)
    wav = scratch / "TIHULU_ISS-SSTV-CANDIDATE_20261123T120000Z_437550000Hz_SSTV-AUDIO.wav"
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(48000)
        out.writeframes(b"\x00\x00" * 4800)
    png = scratch / "TIHULU_ISS-SSTV-CANDIDATE_ROBOT36_01.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n fake")

    # ---- a dropped CU8 selects the recording and opens the analyzer ---------
    window._open_dropped_recording = window._open_dropped_recording  # keep real handler
    area.dropEvent(drop_event(cu8))
    check("a dropped CU8 is selected in the analyzer",
          window.selected_path == str(cu8), window.selected_path)
    check("the analyzer step opens for a dropped CU8",
          window.result_steps.currentIndex() == 1, window.result_steps.currentIndex())

    # ---- a dropped WAV goes to the decode step ------------------------------
    area.dropEvent(drop_event(wav))
    check("a dropped WAV is selected", window.selected_path == str(wav))
    check("the decode step opens for a dropped WAV",
          window.result_steps.currentIndex() == 2, window.result_steps.currentIndex())

    # ---- unsupported files are ignored, never crash --------------------------
    other = scratch / "notes.exe"
    other.write_bytes(b"MZ")
    area.dropEvent(drop_event(other))
    check("an unsupported drop leaves the selection alone",
          window.selected_path == str(wav), window.selected_path)

    # ---- dragEnterEvent only accepts supported recordings --------------------
    area.dragEnterEvent(drop_event(cu8))
    check("dragging a supported recording is accepted",
          drop_event(cu8).isAccepted() is not None)
    reject_mime = QMimeData()
    KEEP_ALIVE.append(reject_mime)
    reject = QDropEvent(QPointF(10, 10), Qt.IgnoreAction,
                        reject_mime, Qt.LeftButton, Qt.NoModifier,
                        QDropEvent.DragEnter)
    area.dragEnterEvent(reject)
    check("dragging a non-file payload is not accepted", not reject.isAccepted())

    # ---- the recording folder picker reaches the Station --------------------
    folder = scratch / "chosen-recordings"
    folder.mkdir()
    window.pass_output_dir.setText(str(folder))
    seen = {}

    def fake_schedule(*args, **kwargs):
        seen["schedule"] = kwargs
        return dict(scheduled=None, satellite="ISS (ZARYA)", run_at="2026-11-23T12:00:00+00:00",
                    capture_seconds=60)

    def fake_chain(*args, **kwargs):
        seen["chain"] = kwargs
        return {"scheduled_count": 2, "total_gap_seconds": 42}

    station.schedule = fake_schedule
    station.schedule_chain = fake_chain

    item = QTreeWidgetItem(["ISS (ZARYA)"])
    item.setData(0, Qt.UserRole, "pass-x")
    window.pass_list.addTopLevelItem(item)
    window.pass_list.setCurrentItem(item)
    window.pass_profile.addItem("ISS SSTV", "iss_sstv")
    window.pass_profile.setCurrentIndex(0)
    window._pass_modes()
    window.pass_mode.setCurrentIndex(window.pass_mode.findData("survey"))
    window.pass_seconds.setValue(60)

    window.schedule_selected_pass()
    check("arming a schedule passes the chosen folder to the Station",
          wait_until(app, lambda: "schedule" in seen) and
          seen["schedule"].get("output_dir") == str(folder), seen.get("schedule"))
    pump(app, 2)
    check("the chosen folder is reported to the operator",
          str(folder) in window.pass_feedback.text(), window.pass_feedback.text())

    # ---- an empty field means the default captures folder -------------------
    seen.clear()
    window.pass_output_dir.clear()
    window.schedule_selected_pass()
    check("no folder text schedules without an override",
          wait_until(app, lambda: "schedule" in seen) and
          seen["schedule"].get("output_dir") is None, seen.get("schedule"))

    # ---- the job list shows the recording folder ----------------------------
    station.jobs = [{"id": "j1", "satellite": "ISS (ZARYA)", "state": "completed",
                     "run_at": "2026-11-23T12:00:00+00:00", "capture_seconds": 60,
                     "output_dir": str(folder), "profile": "iss_sstv", "mode": "survey",
                     "created_at": app_main.stamp(), "result": None}]
    station.save_jobs()
    window.refresh_status()
    pump(app, 2)
    check("the job list has a recording folder column",
          window.job_list.headerItem().text(6) == "Recording folder",
          window.job_list.headerItem().text(6))
    row = window.job_list.topLevelItem(0)
    check("the scheduled folder appears in the job list",
          row is not None and row.text(6) == str(folder),
          row.text(6) if row else None)

    print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
