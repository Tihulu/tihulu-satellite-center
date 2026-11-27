#!/usr/bin/env python3
'''Behavioural test for the Qt desktop app default recording folder controls.
Drives the real window offscreen the way an operator would: types a folder, presses
"Set as default folder", then "Clear default folder", and asserts BOTH what the
window shows and what the Station actually stores, plus a folder the Station refuses
being reported instead of silently applied.

    SAT_APP_ROOT=<dir containing satellite_center> python3 tests/test_desktop_default_folder.py

Nothing is installed; the window uses a disposable data directory and the offscreen Qt
platform. No receiver, USB device, service or owner file is touched.
'''
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = os.environ.get("SAT_APP_ROOT", "/usr/share/tihulu-satellite-center")
sys.path.insert(0, APP_ROOT)

from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton

from satellite_center import __main__ as app_main
from satellite_center.desktop import SatelliteWindow

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


def button(window, text):
    for candidate in window.findChildren(QPushButton):
        if candidate.text() == text:
            return candidate
    raise AssertionError("no button labelled " + repr(text))


def main():
    home = Path(tempfile.mkdtemp(prefix="sat-qt-default-home-"))
    patcher = mock.patch.object(Path, "home", classmethod(lambda cls: home))
    patcher.start()
    data = home / ".local" / "share" / "tihulu-satellite-center"
    station = app_main.Station(data)
    app = QApplication(["desktop-default-folder-test"])
    window = SatelliteWindow(station, auto_poll=False)

    set_button = button(window, "Set as default folder")
    clear_button = button(window, "Clear default folder")
    persisted = data / "default_recording_dir.json"

    check("the window starts with no default folder",
          station.get_default_recording_dir() is None
          and window.default_folder_display.text() == "No default folder (uses captures/)",
          window.default_folder_display.text())
    check("nothing is persisted before the operator chooses a folder", not persisted.exists())

    chosen = home / "sat-recordings"
    window.pass_output_dir.setText(str(chosen))
    set_button.click()
    pump(app, 2)
    check("setting a folder stores it on the Station",
          station.get_default_recording_dir() == chosen.resolve(strict=False),
          station.get_default_recording_dir())
    check("the window shows the chosen folder",
          str(chosen) in window.default_folder_display.text(),
          window.default_folder_display.text())
    check("the choice is persisted for the next start", persisted.is_file(), persisted)

    restarted = app_main.Station(data)
    check("a restarted Station keeps the folder",
          restarted.get_default_recording_dir() == chosen.resolve(strict=False),
          restarted.get_default_recording_dir())
    restarted.close()

    clear_button.click()
    pump(app, 2)
    check("clearing removes it from the Station", station.get_default_recording_dir() is None,
          station.get_default_recording_dir())
    check("clearing restores the captures/ message",
          window.default_folder_display.text() == "No default folder (uses captures/)",
          window.default_folder_display.text())
    check("clearing removes the persisted file", not persisted.exists())

    # An empty field means the default captures folder, not no folder at all.
    window.pass_output_dir.setText("")
    set_button.click()
    pump(app, 2)
    check("an empty field selects the captures folder",
          station.get_default_recording_dir() == station.archive.resolve(),
          station.get_default_recording_dir())

    # A folder the Station refuses must be reported, never silently applied.
    warnings = []
    real_warning = QMessageBox.warning
    QMessageBox.warning = lambda *a, **k: warnings.append(a[-1] if a else "")
    window.pass_output_dir.setText("/etc")
    set_button.click()
    pump(app, 3)
    QMessageBox.warning = real_warning
    check("a refused folder is reported with the Station reason",
          any("app data folder or your home directory" in str(w) for w in warnings), warnings)
    check("a refused folder leaves the stored default unchanged",
          station.get_default_recording_dir() == station.archive.resolve(),
          station.get_default_recording_dir())

    window.close()
    station.close()
    print("")
    print(str(PASS) + " passed, " + str(FAIL) + " failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
