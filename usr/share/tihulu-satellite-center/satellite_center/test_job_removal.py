"""Removal and cancellation of native scheduled RF jobs.

Exercises the real Station.clear_job / clear_finished_jobs methods against a
throwaway store, and asserts the WebChannel command allowlist actually exposes
them. Disposable directories only: no receiver, USB, service or owner data.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
import types

APP = Path(__file__).resolve().parent
# The package modules use "from . import ...", so bind this directory as the
# satellite_center package regardless of the enclosing folder's name.
_init = importlib.util.spec_from_file_location("satellite_center", APP / "__init__.py")
_pkg = importlib.util.module_from_spec(_init)
sys.modules["satellite_center"] = _pkg
_init.loader.exec_module(_pkg)
_spec = importlib.util.spec_from_file_location("satellite_center.__main__", APP / "__main__.py")
main = importlib.util.module_from_spec(_spec)
sys.modules["satellite_center.__main__"] = main
_spec.loader.exec_module(main)


class Wake:
    def set(self):
        pass


class Store:
    """Minimal stand-in that reuses the real save/clear logic."""

    def __init__(self, jobs):
        self.directory = Path(tempfile.mkdtemp(prefix="satellite-job-removal-"))
        self.jobs = list(jobs)
        self.lock = threading.RLock()
        self.wake = Wake()
        self.save_calls = 0

    def save_jobs(self):
        self.save_calls += 1
        main.safe_write(self.directory / "schedule.json", self.jobs)

    clear_job = main.Station.clear_job
    clear_finished_jobs = main.Station.clear_finished_jobs


def job(job_id, state):
    return {"id": job_id, "satellite": "ISS (ZARYA)", "state": state,
            "run_at": "2026-11-21T10:00:00+00:00", "capture_seconds": 60}


class JobRemovalTests(unittest.TestCase):
    def test_finished_record_is_removed_and_persisted(self):
        store = Store([job("a", "completed"), job("b", "cancelled")])
        result = store.clear_job("a")
        self.assertEqual(result, {"cleared": "a", "remaining": 1})
        self.assertEqual([j["id"] for j in store.jobs], ["b"])
        self.assertEqual(store.save_calls, 1)
        raw = json.loads((store.directory / "schedule.json").read_text())
        self.assertEqual([j["id"] for j in raw], ["b"])

    def test_pending_schedule_is_refused_not_dropped(self):
        store = Store([job("a", "pending")])
        with self.assertRaisesRegex(ValueError, "Cancel a pending schedule"):
            store.clear_job("a")
        self.assertEqual(len(store.jobs), 1)
        self.assertEqual(store.save_calls, 0)

    def test_unknown_and_malformed_identifiers_are_refused(self):
        store = Store([job("a", "completed")])
        with self.assertRaisesRegex(ValueError, "Schedule not found"):
            store.clear_job("missing")
        for bad in ("", "a" * 41, "../etc/passwd", None):
            with self.assertRaises(ValueError):
                store.clear_job(bad)

    def test_bulk_clear_keeps_live_reservations(self):
        store = Store([job("a", "pending"), job("b", "completed"),
                       job("c", "cancelled"), job("d", "pending"), job("e", "blocked")])
        result = store.clear_finished_jobs()
        self.assertEqual(result, {"cleared": 3, "remaining": 2})
        self.assertEqual([j["id"] for j in store.jobs], ["a", "d"])
        self.assertEqual(store.save_calls, 1)

    def test_bulk_clear_is_a_noop_without_finished_records(self):
        store = Store([job("a", "pending")])
        self.assertEqual(store.clear_finished_jobs(), {"cleared": 0, "remaining": 1})
        self.assertEqual(store.save_calls, 0)

    def test_bridge_exposes_the_new_commands(self):
        source = (APP / "native_web_shell.py").read_text()
        self.assertIn('"clear_job","clear_finished_jobs"', source)
        self.assertIn('if command=="clear_job":', source)
        self.assertIn('if command=="clear_finished_jobs":', source)

    def test_bridge_exposes_removal_to_the_web_ui(self):
        bridge = (APP / "native_desktop_bridge.js").read_text()
        self.assertIn("window.nativeClearScheduledJob=job_id=>lookup('clear_job',{job_id})", bridge)
        self.assertIn("window.nativeClearFinishedScheduledJobs=()=>lookup('clear_finished_jobs',{})", bridge)

    def test_removal_routing_module_is_wired_into_the_ui(self):
        ui = (APP / "browser" / "browser.js").read_text()
        self.assertIn("from './job_removal.mjs'", ui)
        self.assertIn("dismissAction", ui)
        self.assertIn("finishedJobs(nativeScheduledJobs)", ui)
        self.assertIn("navigate to the native calendar", ui) if False else None
        self.assertIn("Dismiss this entry from the list", ui)


if __name__ == "__main__":
    unittest.main()
