"""Owner-selected per-job recording directories and their serving surfaces.

Exercises the real Station methods (validation, scheduling, the scheduler
loop, archive listing and the local HTTP routes) against throwaway data and
home directories. No receiver, USB device, service or owner file is touched;
hardware acquisition is stubbed at the documented seams only.
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
import wave
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

RTL_POWER_ROW = ("2026-11-23,12:00:00,137000000,138000000,1000,10,"
                 + ",".join("-20.5" for _ in range(40)))


class FakeProc:
    """Stand-in receiver child: writes canned survey rows, exits cleanly."""

    def __init__(self, sink):
        self._sink = sink
        self.pid = 999999
        self.returncode = 0

    def poll(self):
        if self._sink is not None:
            self._sink.seek(0)
            self._sink.write((RTL_POWER_ROW + "\n").encode())
            self._sink.flush()
            self._sink = None
        return 0

    def wait(self, timeout=None):
        return 0


class StationCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sat-dir-home-"))
        self.addCleanup(lambda: None)
        patcher = mock.patch.object(Path, "home", classmethod(lambda cls: self.home))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.data = self.home / ".local" / "share" / "tihulu-satellite-center"
        self.station = main.Station(self.data)
        self.station.receiver_enabled = True

    def pass_event(self, minutes_ahead=30, length=600):
        now = datetime.now(timezone.utc)
        return {"name": "ISS (ZARYA)", "family": "iss", "profiles": ["iss_sstv"],
                "aos": (now + timedelta(minutes=minutes_ahead)).isoformat(),
                "los": (now + timedelta(minutes=minutes_ahead, seconds=length)).isoformat()}

    def arm(self, output_dir=None, mode="survey", seconds=60):
        event = self.pass_event()
        self.station.predicted["pass-x"] = event
        aos = datetime.fromisoformat(event["aos"])
        run_at = (aos + timedelta(seconds=120)).isoformat()
        return self.station.schedule("pass-x", "iss_sstv", mode, run_at, seconds,
                                     output_dir=output_dir)


class ValidationTests(StationCase):
    def test_default_and_empty_values_mean_the_captures_folder(self):
        self.assertIsNone(self.station.resolve_recording_dir(None))
        self.assertIsNone(self.station.resolve_recording_dir(""))
        self.assertIsNone(self.station.resolve_recording_dir("   "))
        self.assertEqual(self.station.prepare_recording_dir(None), self.station.archive)

    def test_a_plain_name_stays_under_the_captures_folder(self):
        resolved = self.station.resolve_recording_dir("ISS-SSTV-2026-11")
        self.assertEqual(resolved, (self.station.archive / "ISS-SSTV-2026-11").resolve())
        nested = self.station.resolve_recording_dir("iss/nov")
        self.assertEqual(nested, (self.station.archive / "iss" / "nov").resolve())

    def test_absolute_paths_inside_the_app_data_folder_and_home_are_accepted(self):
        inside_data = self.station.resolve_recording_dir(str(self.data / "extra-captures"))
        self.assertEqual(inside_data, (self.data / "extra-captures").resolve())
        inside_home = self.station.resolve_recording_dir(str(self.home / "sat-recordings"))
        self.assertEqual(inside_home, (self.home / "sat-recordings").resolve())

    def test_paths_outside_the_allowed_roots_are_refused(self):
        for bad in ("/etc", "/var/log", "/usr/share", str(self.home.parent / "other")):
            with self.assertRaises(ValueError, msg=bad):
                self.station.resolve_recording_dir(bad)

    def test_traversal_components_are_refused(self):
        for bad in ("..", "../escape", "a/../../b", "captures/../../etc"):
            with self.assertRaises(ValueError, msg=bad):
                self.station.resolve_recording_dir(bad)

    def test_a_symlink_cannot_escape_the_allowed_roots(self):
        link = self.station.archive / "sneaky"
        link.symlink_to("/etc", target_is_directory=True)
        with self.assertRaises(ValueError):
            self.station.resolve_recording_dir(str(link))

    def test_a_regular_file_at_the_path_is_refused(self):
        self.station.prepare_recording_dir("folder")
        target = self.station.archive / "folder"
        target.rmdir()
        target.write_text("not a directory", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.station.resolve_recording_dir("folder")

    def test_odd_values_are_refused(self):
        for bad in (7, 4.5, ["x"], "x" * 201, "bad\x00name"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.station.resolve_recording_dir(bad)

    def test_prepare_creates_the_directory_with_owner_only_permissions(self):
        root = self.station.prepare_recording_dir("owner-only")
        self.assertTrue(root.is_dir())
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.station.prepare_recording_dir("owner-only"), root)


class ScheduleTests(StationCase):
    def test_scheduling_stores_the_folder_in_the_job_and_registry(self):
        job = self.arm(output_dir="ISS-SSTV-2026-11")
        self.assertEqual(job["output_dir"],
                         str((self.station.archive / "ISS-SSTV-2026-11").resolve()))
        registry = self.station._recording_registry()
        self.assertIn(job["output_dir"], registry)
        self.assertTrue((self.station.archive / "ISS-SSTV-2026-11" / "x").parent.is_dir())
        persisted = json.loads((self.data / "schedule.json").read_text())
        self.assertEqual(persisted[0]["output_dir"], job["output_dir"])

    def test_scheduling_without_a_folder_keeps_the_default(self):
        job = self.arm()
        self.assertIsNone(job["output_dir"])
        self.assertEqual(self.station._recording_registry(), [])

    def test_an_invalid_folder_is_refused_before_a_job_exists(self):
        with self.assertRaises(ValueError):
            self.arm(output_dir="/etc")
        self.assertEqual(self.station.jobs, [])
        self.assertEqual(self.station._recording_registry(), [])

    def test_chained_scheduling_passes_the_folder_to_every_part(self):
        event = self.pass_event(length=700)
        self.station.predicted["pass-x"] = event
        aos = datetime.fromisoformat(event["aos"])
        parts = self.station.schedule_chain("pass-x", "iss_sstv", "iq",
                                            (aos + timedelta(seconds=60)).isoformat(),
                                            400, output_dir="chain-parts")
        self.assertGreater(parts["scheduled_count"], 1)
        for job in parts["scheduled"]:
            self.assertIn("chain-parts", job["output_dir"])


class SchedulerTests(StationCase):
    def test_the_scheduler_records_into_the_job_folder(self):
        folder = self.station.prepare_recording_dir("scheduler-owned")
        wav = folder / "TIHULU_ISS-SSTV-CANDIDATE_20261123T120000Z_437550000Hz_SSTV-AUDIO.wav"
        with wave.open(str(wav), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(48000)
            out.writeframes(b"\x00\x00" * 4800)
        now = datetime.now(timezone.utc)
        job = {"id": "sched-1", "pass_id": "pass-x", "satellite": "ISS (ZARYA)",
               "profile": "iss_sstv", "mode": "sstv_audio", "aos": now.isoformat(),
               "los": (now + timedelta(minutes=10)).isoformat(),
               "run_at": (now + timedelta(seconds=2)).isoformat(),
               "capture_seconds": 60, "sample_rate": 1024000,
               "mission_id": "iss-sstv", "output_dir": str(folder),
               "state": "pending", "created_at": main.stamp(), "result": None}
        with self.station.lock:
            self.station.jobs.append(job)
        seen = {}

        def fake_capture(profile, mode, seconds, **kwargs):
            seen.update(kwargs)
            return {"file": wav.name, "wav": wav.name, "root": str(folder), "bytes": 9600}

        def fake_extract(path, **kwargs):
            seen["extract_path"] = Path(path)
            return {"status": "stubbed"}

        self.station.capture = fake_capture
        extractor = main.extract_robot36_wav
        main.extract_robot36_wav = fake_extract
        try:
            self.station.start_scheduler()
            deadline = time.time() + 20
            while time.time() < deadline:
                with self.station.lock:
                    if job.get("state") in ("completed", "blocked", "missed"):
                        break
                time.sleep(0.05)
            self.assertEqual(job.get("state"), "completed", job.get("result"))
            self.assertEqual(str(seen.get("output_dir")), str(folder))
            self.assertTrue(seen["extract_path"].is_relative_to(folder))
        finally:
            main.extract_robot36_wav = extractor


class ArchiveTests(StationCase):
    def test_records_and_find_cover_every_recording_folder(self):
        (self.station.archive / "default.json").write_text("{}", encoding="utf-8")
        folder = self.station.prepare_recording_dir("listed")
        (folder / "TIHULU_ISS_20261123T120000Z_437550000Hz_IQ.cu8").write_bytes(b"\x00" * 16)
        self.station.register_recording_dir(folder)
        rows = self.station.records()
        names = [row["file"] for row in rows]
        self.assertIn("default.json", names)
        self.assertIn("TIHULU_ISS_20261123T120000Z_437550000Hz_IQ.cu8", names)
        roots = {row["file"]: row["root"] for row in rows}
        self.assertEqual(roots["default.json"], str(self.station.archive))
        self.assertEqual(roots["TIHULU_ISS_20261123T120000Z_437550000Hz_IQ.cu8"], str(folder))
        root, path = self.station.find_recording("TIHULU_ISS_20261123T120000Z_437550000Hz_IQ.cu8")
        self.assertEqual(root, folder)
        self.assertTrue(path.is_file())
        self.assertEqual(self.station.find_recording("missing.cu8"), (None, None))

    def test_the_registry_survives_a_restart_and_listing_is_capped(self):
        folder = self.station.prepare_recording_dir("persisted")
        self.station.register_recording_dir(folder)
        for index in range(120):
            (folder / f"TIHULU_ISS_20261123T1200{index:02d}Z_437550000Hz_IQ.cu8").write_bytes(b"\x00")
        rows = self.station.records()
        self.assertEqual(len(rows), 100)
        restarted = main.Station(self.data)
        self.assertIn(Path(folder), restarted.recording_roots())


class CaptureTests(StationCase):
    def test_a_survey_capture_writes_into_the_selected_folder(self):
        folder = self.station.prepare_recording_dir("captured-here")
        # Never claim a receiver: stub acquisition exactly like the sibling tests,
        # so this runs on a machine with no RTL-SDR attached and cannot open the
        # operator tuner.
        self.station._launch_receiver = \
            lambda command, **kwargs: FakeProc(kwargs.get("stdout"))
        self.station._acquire = lambda: None
        self.station._release = lambda: None
        with mock.patch("shutil.which", return_value="/usr/bin/rtl_power"):
            result = self.station.capture("iss_sstv", "survey", 18, output_dir="captured-here")
        self.assertTrue(result["file"].endswith(".json"))
        self.assertEqual(result["root"], str(folder))
        self.assertTrue((folder / result["file"]).is_file())
        self.assertEqual(list(self.station.archive.glob("TIHULU_*")), [])
        self.assertEqual(result["status"], "relative_spectrum_measurement")
        self.assertIn(str(folder), self.station._recording_registry())

    def test_a_bad_folder_is_refused_before_hardware_is_touched(self):
        self.station._acquire = lambda: self.fail("receiver must not be claimed")
        with mock.patch("shutil.which", return_value="/usr/bin/rtl_power"):
            with self.assertRaises(ValueError):
                self.station.capture("iss_sstv", "survey", 18, output_dir="/etc")

    def test_manual_capture_without_a_folder_uses_the_default_archive(self):
        with mock.patch("shutil.which", return_value="/usr/bin/rtl_power"):
            self.station._launch_receiver = \
                lambda command, **kwargs: FakeProc(kwargs.get("stdout"))
            self.station._acquire = lambda: None
            self.station._release = lambda: None
            result = self.station.capture("iss_sstv", "survey", 18)
        self.assertEqual(result["root"], str(self.station.archive))
        self.assertTrue((self.station.archive / result["file"]).is_file())
        self.assertEqual(self.station._recording_registry(), [])


class HttpRouteTests(StationCase):
    def setUp(self):
        super().setUp()
        self.folder = self.station.prepare_recording_dir("served")
        self.station.register_recording_dir(self.folder)
        self.payload = b"CU8PLACEHOLDER" * 4
        (self.folder / "TIHULU_ISS-SSTV-CANDIDATE_20261123T120000Z_437550000Hz_IQ.cu8").write_bytes(self.payload)
        (self.folder / "TIHULU_ISS-SSTV-CANDIDATE_ROBOT36_01.png").write_bytes(b"\x89PNG fake")
        self.server = main.Server(("127.0.0.1", 0), self.station)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_port

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_the_archive_route_serves_files_from_a_per_job_folder(self):
        status, body = self.get("/api/archive/TIHULU_ISS-SSTV-CANDIDATE_20261123T120000Z_437550000Hz_IQ.cu8")
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)

    def test_the_robot36_image_route_finds_the_selected_folder(self):
        status, body = self.get("/api/image/TIHULU_ISS-SSTV-CANDIDATE_ROBOT36_01.png")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"\x89PNG fake")

    def test_missing_and_malformed_names_stay_refused(self):
        self.assertEqual(self.get("/api/archive/not-there.cu8")[0], 404)
        self.assertEqual(self.get("/api/archive/..%2f..%2fetc%2fpasswd")[0], 400)
        self.assertEqual(self.get("/api/archive/TIHULU_bad%20name.cu8")[0], 400)


class WiringTests(unittest.TestCase):
    """The feature must actually be reachable from every shipped surface."""

    def test_the_web_ui_carries_the_folder_field_and_drop_zones(self):
        ui = (APP / "browser" / "browser.js").read_text(encoding="utf-8")
        self.assertIn("from './recording_dir.mjs?v=", ui)
        self.assertIn("normalizeOutputDir(folder.value)", ui)
        self.assertIn("outputDir:normalizeOutputDir(folder.value)", ui)
        self.assertIn("initAnalysisDropZones()", ui)
        self.assertIn("dropPlanFor(zoneId,droppedFileSuffix(file.name))", ui)

    def test_the_module_import_uses_a_unique_cache_buster(self):
        import re
        ui = (APP / "browser" / "browser.js").read_text(encoding="utf-8")
        imports = re.findall(r"from ['\"]\./(\w+\.mjs)(\?v=[^'\"]*)?['\"]", ui)
        tags = [tag for name, tag in imports if name == "recording_dir.mjs"]
        self.assertEqual(tags, ["?v=20261123-per-job-dir-v1"])

    def test_the_desktop_bridge_and_shell_carry_the_folder(self):
        bridge = (APP / "native_desktop_bridge.js").read_text(encoding="utf-8")
        self.assertIn("recording_dir.mjs?v=20261123-per-job-dir-v1", bridge)
        self.assertIn("args.output_dir=outputDir", bridge)
        shell = (APP / "native_web_shell.py").read_text(encoding="utf-8")
        self.assertIn('output_dir=args.get("output_dir")', shell)

    def test_the_http_surface_accepts_the_folder(self):
        http = (APP / "__main__.py").read_text(encoding="utf-8")
        self.assertIn('output_dir=data.get("output_dir")', http)

    def test_the_qt_desktop_app_wires_the_picker_and_drop_target(self):
        desktop = (APP / "desktop.py").read_text(encoding="utf-8")
        self.assertIn("self._button(\"Choose folder…\",self._choose_recording_folder)", desktop)
        self.assertIn("output_dir=output_dir", desktop)
        self.assertIn("class RecordingDropArea(QScrollArea):", desktop)
        self.assertIn("self._window._open_dropped_recording(path)", desktop)

    def test_the_pure_drop_rules_module_exists(self):
        self.assertTrue((APP / "browser" / "recording_dir.mjs").is_file())


if __name__ == "__main__":
    unittest.main()
