"""Global default recording folder: idempotent paths and the native desktop UI.

Covers the two owner-visible defects found while repairing the rev.12 package:
an already-resolved recording path was rejected by the 200-character sanity
limit because scheduling resolved it twice, and the HTTP route answered an
invalid folder by calling Station.reply(), which does not exist, so the browser
received no response at all. No receiver, USB device, service or owner file is
touched; hardware acquisition is stubbed at the documented seams only.
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


class DefaultFolderCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sat-default-home-"))
        patcher = mock.patch.object(Path, "home", classmethod(lambda cls: self.home))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.data = self.home / ".local" / "share" / "tihulu-satellite-center"
        self.station = main.Station(self.data)
        self.station.receiver_enabled = True
        self.addCleanup(self.station.close)

    def pass_event(self, minutes_ahead=30, length=900):
        now = datetime.now(timezone.utc)
        return {"name": "ISS (ZARYA)", "family": "iss", "profiles": ["iss_sstv"],
                "aos": (now + timedelta(minutes=minutes_ahead)).isoformat(),
                "los": (now + timedelta(minutes=minutes_ahead, seconds=length)).isoformat()}

    def arm(self, target, output_dir=None, offset=31):
        now = datetime.now(timezone.utc)
        target.predicted["pass-x"] = self.pass_event()
        return target.schedule("pass-x", "iss_sstv", "survey",
                               (now + timedelta(minutes=offset)).isoformat(), 60,
                               output_dir=output_dir)

    def test_a_relative_default_stays_under_the_captures_folder(self):
        # Setting a default only records it; the folder itself is created when a
        # capture is armed, on the filesystem that will actually hold the file.
        self.station.set_default_recording_dir("ISS-DEFAULT")
        self.assertEqual(self.station.get_default_recording_dir(),
                         (self.station.archive / "ISS-DEFAULT").resolve())
        self.assertFalse((self.station.archive / "ISS-DEFAULT").exists())
        job = self.arm(self.station)
        self.assertTrue(str(job["output_dir"]).endswith("ISS-DEFAULT"), job["output_dir"])
        self.assertTrue((self.station.archive / "ISS-DEFAULT").is_dir())

    def test_the_default_survives_a_restart_and_clearing(self):
        self.station.set_default_recording_dir("ISS-DEFAULT")
        restarted = main.Station(self.data)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.get_default_recording_dir(),
                         (self.station.archive / "ISS-DEFAULT").resolve())
        restarted.set_default_recording_dir(None)
        self.assertIsNone(restarted.get_default_recording_dir())
        self.assertFalse((self.data / "default_recording_dir.json").exists())
        self.assertEqual(restarted.prepare_recording_dir(None), restarted.archive)

    def test_an_escape_is_still_refused(self):
        for bad in ("../escape", "/etc", "/usr/lib", str(Path.home() / ".." / "etc")):
            with self.assertRaises(ValueError, msg=bad):
                self.station.set_default_recording_dir(bad)
        self.assertIsNone(self.station.get_default_recording_dir())

    def test_re_resolving_an_already_resolved_path_is_idempotent(self):
        # schedule() resolves once and prepare_recording_dir() resolves again; the
        # sanity limits belong to the operator input, not to the absolute form.
        deep = self.station.archive.joinpath(*("level-%02d" % i for i in range(14)))
        self.assertGreater(len(str(deep.resolve(strict=False))), 200)
        first = self.station.resolve_recording_dir(deep)
        self.assertEqual(first, deep.resolve(strict=False))
        again = self.station.resolve_recording_dir(first)
        self.assertEqual(again, first)
        self.assertEqual(self.station.prepare_recording_dir(first), first)
        # A long *operator* name is still refused: only re-resolution is forgiven.
        with self.assertRaises(ValueError):
            self.station.resolve_recording_dir("x" * 201)

    def test_a_deep_relative_default_still_schedules_and_captures(self):
        # What an operator actually types: a nested name under captures. Its
        # resolved absolute form is far past the 200-character sanity limit, and
        # the second resolution during scheduling must still accept it.
        nested = "/".join("level-%02d" % i for i in range(14))
        self.station.set_default_recording_dir(nested)
        resolved = (self.station.archive / nested).resolve(strict=False)
        self.assertGreater(len(str(resolved)), 200)
        job = self.arm(self.station, offset=34)
        self.assertEqual(job["output_dir"], str(resolved))
        self.assertTrue(resolved.is_dir())


class DefaultFolderHttpCase(DefaultFolderCase):
    def setUp(self):
        super().setUp()
        self.server = main.Server(("127.0.0.1", 0), self.station)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, route, payload):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{route}",
                                         data=json.dumps(payload).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"{}")
            except json.JSONDecodeError:
                return error.code, {}

    def test_setting_the_default_over_http_works(self):
        code, body = self.post("/api/set-default-folder",
                               {"folder": "ISS-DEFAULT", "csrf": self.server.csrf})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["ok"])
        self.assertTrue(body["folder"].endswith("ISS-DEFAULT"))

    def test_an_invalid_folder_answers_with_a_reason(self):
        # Regression: this route used to call Station.reply(), which does not
        # exist, so the connection was closed with no response at all.
        self.post("/api/set-default-folder",
                  {"folder": "ISS-DEFAULT", "csrf": self.server.csrf})
        code, body = self.post("/api/set-default-folder",
                               {"folder": "../escape", "csrf": self.server.csrf})
        self.assertEqual(code, 400, (code, body))
        self.assertIn("error", body)
        self.assertIn("..", body["error"])
        code, body = self.post("/api/set-default-folder",
                               {"folder": "/etc", "csrf": self.server.csrf})
        self.assertEqual(code, 400, (code, body))
        code, body = self.post("/api/clear-default-folder", {"csrf": self.server.csrf})
        self.assertEqual(code, 200, body)

    def test_a_missing_csrf_token_is_refused(self):
        code, _ = self.post("/api/set-default-folder", {"folder": "ISS-DEFAULT"})
        self.assertEqual(code, 403)


class NativeSurfaceCase(unittest.TestCase):
    def test_the_native_shell_allowlists_the_default_folder_commands(self):
        shell = (APP / "native_web_shell.py").read_text(encoding="utf-8")
        self.assertIn("set_default_folder", shell)
        self.assertIn("clear_default_folder", shell)
        self.assertIn("st.set_default_recording_dir(args.get(", shell)
        bridge = (APP / "native_desktop_bridge.js").read_text(encoding="utf-8")
        self.assertIn("window.nativeSetDefaultFolder=", bridge)
        self.assertIn("window.nativeClearDefaultFolder=", bridge)

    def test_the_page_shows_the_controls_on_every_local_surface(self):
        page = (APP / "browser" / "browser.js").read_text(encoding="utf-8")
        self.assertIn("default-folder-controls", page)
        self.assertIn("default-folder-input", page)
        self.assertIn("Set as default folder", page)
        self.assertIn("Clear default folder", page)
        # The label must read the nested field, not a non-existent top-level one.
        self.assertIn("state.hardware.default_recording_folder", page)
        self.assertNotIn("status.default_recording_folder", page)
        # Mounted from module scope, so the desktop shell shows them without an
        # open pass card, and only once.
        self.assertIn("defaultFolderControlsMounted", page)
        self.assertIn("tihulu-native-bridge-ready", page)




class PersistedDefaultCase(unittest.TestCase):
    """A persisted default is re-validated on load, never trusted blindly."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sat-persist-home-"))
        patcher = mock.patch.object(Path, "home", classmethod(lambda cls: self.home))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.data = self.home / ".local" / "share" / "tihulu-satellite-center"
        self.file = self.data / "default_recording_dir.json"

    def load(self, contents):
        self.data.mkdir(parents=True, exist_ok=True)
        # set_default_recording_dir/safe_write persist a JSON string, and
        # safe_read parses JSON, so the file must hold a quoted string.
        self.file.write_text(json.dumps(contents), encoding="utf-8")
        station = main.Station(self.data)
        self.addCleanup(station.close)
        return station

    def test_a_persisted_path_outside_the_allowed_roots_is_ignored(self):
        station = self.load("/etc")
        self.assertIsNone(station.get_default_recording_dir())
        self.assertEqual(station.prepare_recording_dir(None), station.archive)

    def test_a_persisted_traversal_path_is_ignored(self):
        self.assertIsNone(self.load("../escape").get_default_recording_dir())

    def test_an_overlong_or_empty_persisted_value_is_ignored(self):
        self.assertIsNone(self.load("").get_default_recording_dir())
        self.assertIsNone(self.load("x" * 250).get_default_recording_dir())

    def test_a_persisted_path_that_is_a_file_is_ignored(self):
        victim = self.home / "not-a-folder"
        victim.write_text("not a directory", encoding="utf-8")
        self.assertIsNone(self.load(str(victim)).get_default_recording_dir())

    def test_a_persisted_path_elsewhere_in_home_is_still_used(self):
        chosen = self.home / "sat-recordings"
        station = self.load(str(chosen))
        self.assertEqual(station.get_default_recording_dir(), chosen.resolve(strict=False))

    def test_a_persisted_relative_name_is_still_resolved_under_captures(self):
        station = self.load("ISS-DEFAULT")
        expected = (station.archive / "ISS-DEFAULT").resolve()
        self.assertEqual(station.get_default_recording_dir(), expected)
        # Reading a default does not create the folder; it is created when a
        # capture is prepared, exactly like a freshly chosen one.
        self.assertEqual(station.prepare_recording_dir(None), expected)
        self.assertTrue(expected.is_dir())


class BrowserOnlySurfaceCase(unittest.TestCase):
    def test_the_browser_only_page_says_the_default_folder_is_unavailable(self):
        page = (APP / "browser" / "browser.js").read_text(encoding="utf-8")
        self.assertIn("default-folder-browser-note", page)
        self.assertIn("defaultFolderTransport", page)
        self.assertIn("a browser page cannot choose a folder on this computer", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
