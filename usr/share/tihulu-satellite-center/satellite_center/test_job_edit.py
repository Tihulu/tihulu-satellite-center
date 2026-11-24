"""Editing pending scheduled RF captures in place.

Exercises the real Station.edit_job against a throwaway data directory: only a
pending schedule may change, the edit must stay inside its own predicted pass,
the single-receiver reservation and the disk budget are re-checked, and a
failed save rolls the record back. No receiver, USB device, service or owner
file is touched; hardware acquisition is stubbed at the documented seams only.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parent
_init = importlib.util.spec_from_file_location("satellite_center", APP / "__init__.py")
_pkg = importlib.util.module_from_spec(_init)
sys.modules["satellite_center"] = _pkg
_init.loader.exec_module(_pkg)
_spec = importlib.util.spec_from_file_location("satellite_center.__main__", APP / "__main__.py")
main = importlib.util.module_from_spec(_spec)
sys.modules["satellite_center.__main__"] = main
_spec.loader.exec_module(main)


class EditCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sat-edit-home-"))
        patcher = mock.patch.object(Path, "home", classmethod(lambda cls: self.home))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.data = self.home / ".local" / "share" / "tihulu-satellite-center"
        self.station = main.Station(self.data)
        self.station.receiver_enabled = True

    def pass_event(self, minutes_ahead=30, length=900):
        now = datetime.now(timezone.utc)
        return {"name": "ISS (ZARYA)", "family": "iss", "profiles": ["iss_sstv"],
                "aos": (now + timedelta(minutes=minutes_ahead)).isoformat(),
                "los": (now + timedelta(minutes=minutes_ahead, seconds=length)).isoformat()}

    def arm(self, mode="survey", seconds=60):
        event = self.pass_event()
        self.station.predicted["pass-x"] = event
        aos = datetime.fromisoformat(event["aos"])
        run_at = (aos + timedelta(seconds=120)).isoformat()
        return self.station.schedule("pass-x", "iss_sstv", mode, run_at, seconds)

    def job(self, **overrides):
        base = {"id": "aaaaaaaaaaaa", "pass_id": "pass-x", "satellite": "ISS (ZARYA)",
                "family": "iss", "profile": "iss_sstv", "mode": "survey",
                "aos": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                "los": (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat(),
                "run_at": (datetime.now(timezone.utc) + timedelta(minutes=31)).isoformat(),
                "capture_seconds": 60, "sample_rate": 1024000,
                "estimated_bytes": 60000000, "state": "pending",
                "created_at": main.stamp(), "result": None}
        base.update(overrides)
        return base


class ValidationTests(EditCase):
    def test_only_a_pending_schedule_may_be_edited(self):
        for state in ("running", "completed", "cancelled", "blocked", "interrupted", "missed", "error"):
            self.station.jobs = [self.job(id="ffffffffffff", state=state)]
            with self.assertRaises(ValueError, msg=state):
                self.station.edit_job("ffffffffffff",
                                      (datetime.now(timezone.utc) + timedelta(minutes=32)).isoformat(), 60)
            self.assertEqual(self.station.jobs[0]["state"], state)
            self.assertEqual(self.station.jobs[0]["capture_seconds"], 60)

    def test_unknown_and_malformed_identifiers_are_refused(self):
        self.station.jobs = [self.job()]
        with self.assertRaises(ValueError):
            self.station.edit_job("nope", None, 60)
        with self.assertRaises(ValueError):
            self.station.edit_job("zz", None, 60)
        with self.assertRaises(ValueError):
            self.station.edit_job("aaaaaaaaaaaa", None, None)
        self.assertEqual(self.station.jobs[0]["capture_seconds"], 60)

    def test_bad_start_times_are_refused(self):
        job = self.job()
        self.station.jobs = [job]
        aos = datetime.fromisoformat(job["aos"])
        los = datetime.fromisoformat(job["los"])
        for bad, why in ((aos - timedelta(seconds=30), "before AOS"),
                         (los + timedelta(seconds=30), "after LOS"),
                         (datetime.now(timezone.utc) - timedelta(minutes=5), "in the past")):
            with self.assertRaises(ValueError, msg=why):
                self.station.edit_job(job["id"], bad.isoformat(), None)
        with self.assertRaises(ValueError):
            self.station.edit_job(job["id"], "not-a-time", None)
        with self.assertRaises(ValueError):
            self.station.edit_job(job["id"], (aos + timedelta(minutes=1)).isoformat(timespec="seconds")[:-6] + "", None)
        self.assertEqual(self.station.jobs[0]["run_at"], job["run_at"])

    def test_bad_durations_are_refused(self):
        job = self.job()
        self.station.jobs = [job]
        for bad in (7, 901, 0, -30):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.station.edit_job(job["id"], None, bad)
        with self.assertRaises(ValueError):
            self.station.edit_job(job["id"], None, "60")

    def test_an_edited_recording_must_fit_before_LOS(self):
        # A 45-minute pass edited to a 20-minute duration ending after LOS.
        job = self.job(run_at=(datetime.fromisoformat(self.job()["los"]) - timedelta(minutes=5)).isoformat(),
                       capture_seconds=60)
        self.station.jobs = [job]
        with self.assertRaises(ValueError):
            self.station.edit_job(job["id"], None, 900)


class ApplyTests(EditCase):
    def test_a_pending_job_edits_and_persists(self):
        job = self.arm()
        new_start = datetime.fromisoformat(job["run_at"]) + timedelta(seconds=90)
        updated = self.station.edit_job(job["id"], new_start.isoformat(), 120)
        self.assertEqual(updated["capture_seconds"], 120)
        self.assertEqual(updated["run_at"], main.stamp(new_start))
        # A survey estimate is a flat 1 MB budget; the field is still recomputed.
        self.assertEqual(updated["estimated_bytes"], 1_000_000)
        persisted = json.loads((self.data / "schedule.json").read_text())
        self.assertEqual(persisted[0]["capture_seconds"], 120)
        # untouched fields survive the edit
        for key in ("profile", "mode", "sample_rate", "output_dir", "pass_id"):
            self.assertEqual(persisted[0][key], job[key])

    def test_a_survey_may_not_be_edited_past_180_seconds(self):
        job = self.arm(mode="survey", seconds=60)
        with self.assertRaises(ValueError):
            self.station.edit_job(job["id"], None, 240)
        self.assertEqual(self.station.jobs[0]["capture_seconds"], 60)

    def test_an_overlapping_edit_is_refused(self):
        self.arm()
        first = self.station.jobs[0]
        event = self.pass_event(minutes_ahead=90)
        self.station.predicted["pass-y"] = event
        aos = datetime.fromisoformat(event["aos"])
        second = self.station.schedule("pass-y", "iss_sstv", "survey",
                                       (aos + timedelta(seconds=120)).isoformat(), 60)
        # Move the second job onto the first one's reserved window.
        with self.assertRaises(ValueError):
            self.station.edit_job(second["id"], first["run_at"], 60)
        self.assertEqual([j["run_at"] for j in self.station.jobs],
                         [first["run_at"], second["run_at"]])

    def test_a_failed_save_rolls_the_record_back(self):
        job = self.arm()
        new_start = datetime.fromisoformat(job["run_at"]) + timedelta(seconds=90)
        self.station.save_jobs = lambda: (_ for _ in ()).throw(OSError("disk full"))
        with self.assertRaises(OSError):
            self.station.edit_job(job["id"], new_start.isoformat(), 120)
        self.assertEqual(self.station.jobs[0]["run_at"], job["run_at"])
        self.assertEqual(self.station.jobs[0]["capture_seconds"], 60)
        self.assertEqual(self.station.jobs[0]["estimated_bytes"], job["estimated_bytes"])
        self.station.save_jobs = main.Station.save_jobs.__get__(self.station)

    def test_the_edited_start_keeps_the_job_inside_its_own_pass(self):
        job = self.arm(seconds=120)
        aos = datetime.fromisoformat(job["aos"])
        new_start = aos + timedelta(seconds=60)
        updated = self.station.edit_job(job["id"], new_start.isoformat(), 120)
        los = datetime.fromisoformat(updated["los"])
        self.assertGreaterEqual(datetime.fromisoformat(updated["run_at"]), aos)
        self.assertLess(datetime.fromisoformat(updated["run_at"]) +
                        timedelta(seconds=updated["capture_seconds"] + main.END_MARGIN_SECONDS), los)


class HttpTests(EditCase):
    def setUp(self):
        super().setUp()
        self.job = self.arm()
        self.server = main.Server(("127.0.0.1", 0), self.station)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_port

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()

    def csrf(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/status", timeout=10) as r:
            return json.loads(r.read())["csrf"]

    def post(self, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/edit-job",
            data=json.dumps({**payload, "csrf": self.csrf()}).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_the_route_edits_a_pending_job(self):
        new_start = datetime.fromisoformat(self.job["run_at"]) + timedelta(seconds=120)
        status, body = self.post({"job_id": self.job["id"],
                                  "run_at": new_start.isoformat(),
                                  "duration_seconds": 90})
        self.assertEqual(status, 200)
        self.assertEqual(body["capture_seconds"], 90)
        self.assertEqual(body["run_at"], main.stamp(new_start))

    def test_the_route_refuses_an_unedited_field_and_a_bad_target(self):
        status, _ = self.post({"job_id": self.job["id"]})
        self.assertEqual(status, 400)
        status, _ = self.post({"job_id": "deadbeef1234", "duration_seconds": 90})
        self.assertEqual(status, 400)


class WiringTests(unittest.TestCase):
    def test_the_desktop_app_exposes_the_manual_editor(self):
        desktop = (APP / "desktop.py").read_text(encoding="utf-8")
        self.assertIn('self._button("Edit selected pending job",self.edit_manual_job)', desktop)
        self.assertIn("def edit_manual_job(self):", desktop)
        self.assertIn('self._task("Edit scheduled pass",', desktop)
        # the pre-existing automatic-mission editor is untouched
        self.assertIn('self._button("Edit selected automatic job",self.edit_auto_job)', desktop)
        self.assertIn('elif key=="Edit scheduled pass":', desktop)

    def test_the_web_ui_carries_the_edit_form(self):
        ui = (APP / "browser" / "browser.js").read_text(encoding="utf-8")
        self.assertIn("async function editNativeJob(", ui)
        self.assertIn("'/api/edit-job'", ui)
        self.assertIn("function renderNativeJobEditForm(", ui)
        self.assertIn('edit.textContent=\'Edit pending job\'', ui)
        self.assertIn("renderNativeJobEditForm(job)", ui)

    def test_the_bridge_and_shell_expose_the_command(self):
        bridge = (APP / "native_desktop_bridge.js").read_text(encoding="utf-8")
        self.assertIn("window.nativeEditScheduledJob=", bridge)
        self.assertIn("lookup('edit_job'", bridge)
        shell = (APP / "native_web_shell.py").read_text(encoding="utf-8")
        self.assertIn('"edit_job"', shell)
        self.assertIn('st.edit_job(args.get("job_id")', shell)
        http = (APP / "__main__.py").read_text(encoding="utf-8")
        self.assertIn('route == "/api/edit-job"', http)
        self.assertIn('app.edit_job(data.get("job_id")', http)


if __name__ == "__main__":
    unittest.main()
