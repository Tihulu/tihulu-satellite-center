"""Disposable HOME-only tests; no SDR, owner's PC, network, GUI or services."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import installer as app


EVIDENCE = Path(__file__).resolve().parents[1] / "runs/20261112T190206Z-ff7172"
ARCHIVE = EVIDENCE / "artifacts/installed-package-files.tar.gz"
MANIFEST = EVIDENCE / "files.txt"


class TestCandidate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="satellite-phase-c-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "disposable-home"
        self.home.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {"HOME": str(self.home)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.p = app.paths()

    def install(self):
        return app._deploy(self.p, ARCHIVE, MANIFEST, app.PRODUCTION_PIN)

    def test_01_clean_install_real_frozen_archive(self):
        result = self.install()
        self.assertEqual(result["active"], app.PRODUCTION_PIN.release)
        self.assertTrue(self.p.launcher.exists())
        self.assertTrue(self.p.desktop.exists())
        self.assertIn(b"exec /usr/bin/python3", self.p.launcher.read_bytes())
        self.assertEqual(self.p.root.stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.p.root / "manager.py").is_file())
        with app._locked(self.p, shared=True):
            app._index_for(self.p.root / "releases" / app.PRODUCTION_PIN.release, app.PRODUCTION_PIN)

    def test_02_repeat_and_update_are_pinned_idempotent(self):
        self.install()
        first = self.p.launcher.read_bytes()
        self.assertEqual(self.install()["active"], app.PRODUCTION_PIN.release)
        self.assertEqual(first, self.p.launcher.read_bytes())
        self.assertEqual(len(list((self.p.root / "releases").iterdir())), 1)

    def test_03_rollback_no_previous_release_is_hold(self):
        self.install()
        with self.assertRaisesRegex(app.Hold, "No previous"):
            app._rollback(self.p)

    def test_04_uninstall_preserves_runtime_and_separate_user_web(self):
        old = self.home / ".local/bin/tihulu-satellite"
        old.parent.mkdir(parents=True)
        old.write_text("legacy user-web, preserve!")
        web = self.home / ".local/share/tihulu-satellite-center-app/recordings/keep.wav"
        web.parent.mkdir(parents=True)
        web.write_bytes(b"legacy recordings")
        self.install()
        isolated = self.p.runtime_data
        isolated.mkdir()
        (isolated / "user-recording.wav").write_bytes(b"must keep")
        self.assertTrue(app._uninstall(self.p)["runtime_preserved"])
        self.assertFalse(self.p.launcher.exists())
        self.assertFalse(self.p.desktop.exists())
        self.assertEqual(list((self.p.root / "releases").iterdir()), [])
        self.assertEqual(old.read_text(), "legacy user-web, preserve!")
        self.assertEqual(web.read_bytes(), b"legacy recordings")
        self.assertEqual((isolated / "user-recording.wav").read_bytes(), b"must keep")

    def test_05_uninstall_idempotent(self):
        self.install()
        app._uninstall(self.p)
        self.assertTrue(app._uninstall(self.p)["removed_releases"])

    def test_06_foreign_launcher_collision_refuses_all(self):
        self.p.launcher.parent.mkdir(parents=True)
        self.p.launcher.write_text("foreign app", encoding="utf-8")
        with self.assertRaisesRegex(app.Hold, "Foreign launcher"):
            self.install()
        self.assertEqual(self.p.launcher.read_text(), "foreign app")

    def test_07_foreign_desktop_collision_refuses(self):
        self.p.desktop.parent.mkdir(parents=True)
        self.p.desktop.write_text("foreign desktop", encoding="utf-8")
        with self.assertRaisesRegex(app.Hold, "Foreign launcher"):
            self.install()
        self.assertEqual(self.p.desktop.read_text(), "foreign desktop")

    def test_08_archive_hash_mismatch_refuses(self):
        altered = Path(self.temp.name) / "altered.tar.gz"
        altered.write_bytes(ARCHIVE.read_bytes() + b"changed")
        with self.assertRaisesRegex(app.Hold, "not the explicitly approved"):
            app._deploy(self.p, altered, MANIFEST, app.PRODUCTION_PIN)
        self.assertFalse(self.p.launcher.exists())

    def test_09_installed_release_mutation_holds_uninstall(self):
        self.install()
        p = self.p.root / "releases" / app.PRODUCTION_PIN.release / "payload/usr/bin/tihulu-satellite-center"
        p.write_bytes(b"changed")
        with self.assertRaisesRegex(app.Hold, "integrity mismatch"):
            app._uninstall(self.p)
        self.assertTrue(p.exists())

    def test_10_symlinked_root_refused(self):
        foreign = self.home / "foreign"
        foreign.mkdir()
        self.p.root.parent.mkdir(parents=True)
        self.p.root.symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(app.Hold, "Symlink"):
            self.install()
        self.assertEqual(list(foreign.iterdir()), [])

    def test_11_owner_marker_mismatch_refused(self):
        with app._locked(self.p):
            pass
        marker = self.p.root / app.MARKER
        doc = json.loads(marker.read_text())
        doc["token"] = "wrong"
        marker.write_text(json.dumps(doc))
        with self.assertRaisesRegex(app.Hold, "marker"):
            self.install()

    def test_12_concurrent_exclusive_lock_is_hold(self):
        self.install()
        with app._locked(self.p):
            with self.assertRaisesRegex(app.Hold, "Another installer"):
                self.install()

    def test_13_corrupt_active_pointer_is_hold(self):
        self.install()
        (self.p.root / app.POINTER).write_text('{"active":"../../etc","previous":null}')
        with self.assertRaisesRegex(app.Hold, "pointer"):
            app._uninstall(self.p)

    def test_14_recovery_of_owned_interrupted_staging(self):
        self.install()
        with app._locked(self.p) as (owner, _):
            r = self.p.root / "releases"
            stage = r / (".staging-" + "a" * 32)
            stage.mkdir(mode=0o700)
            app._jsonwrite(stage / "STAGING.json",
                app._stage_metadata(owner, app.PRODUCTION_PIN,
                                   app._approved_inventory(self.p.root, app.PRODUCTION_PIN)))
            (stage / "payload").mkdir(mode=0o700)
            (stage / "payload/usr").mkdir(mode=0o700)
            (stage / "payload/usr/bin").mkdir(mode=0o700)
            with tarfile.open(ARCHIVE, "r:gz") as archive:
                genuine = archive.extractfile("usr/bin/tihulu-satellite-center").read()
            (stage / "payload/usr/bin/tihulu-satellite-center").write_bytes(genuine)
        self.assertEqual(app._recover(self.p), 1)
        self.assertFalse(stage.exists())
        self.assertEqual(app._recover(self.p), 0)

    def test_15_recovery_refuses_foreign_in_staging(self):
        self.install()
        with app._locked(self.p) as (owner, _):
            stage = self.p.root / "releases" / (".staging-" + "b" * 32)
            stage.mkdir(mode=0o700)
            app._jsonwrite(stage / "STAGING.json",
                app._stage_metadata(owner, app.PRODUCTION_PIN,
                                   app._approved_inventory(self.p.root, app.PRODUCTION_PIN)))
            (stage / "foreign.txt").write_text("preserve")
        with self.assertRaisesRegex(app.Hold, "Foreign interrupted"):
            app._recover(self.p)
        self.assertEqual((stage / "foreign.txt").read_text(), "preserve")

    def test_16_manager_mismatch_refuses(self):
        self.install()
        (self.p.root / "manager.py").write_text("hijacked")
        with self.assertRaisesRegex(app.Hold, "manager differs"):
            self.install()

    def test_17_launch_exec_environment_is_isolated_without_executing(self):
        self.install()
        self.assertFalse(self.p.runtime_home.exists())
        marker = RuntimeError("mock execve - no GUI or device opened")
        with patch.object(app.os, "execve", side_effect=marker) as mocked:
            with self.assertRaisesRegex(RuntimeError, "mock execve"):
                app._launch(self.p, ["desktop"])
        cmd = mocked.call_args.args
        self.assertEqual(cmd[0], "/usr/bin/python3")
        self.assertEqual(cmd[1][-1], "desktop")
        self.assertEqual(cmd[2]["HOME"], str(self.p.runtime_home))
        self.assertEqual(cmd[2]["TIHULU_SATELLITE_DATA"], str(self.p.runtime_data))
        self.assertNotIn("tihulu-satellite-center-app", cmd[2]["PYTHONPATH"])
        # Inherited injection vectors must never reach the child...
        for hostile in ("LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONHOME",
                        "PYTHONSTARTUP", "PYTHONWARNINGS"):
            self.assertNotIn(hostile, cmd[2])
        # ...and the current working directory must not shadow the pinned payload...
        self.assertIn("-P", cmd[1])
        # ...and no bytecode cache may be written into the pinned payload.
        self.assertEqual(cmd[2].get("PYTHONDONTWRITEBYTECODE"), "1")

    def test_17b_launch_refuses_swapped_manager_module(self):
        self.install()
        (self.p.root / "manager.py").write_text("hijacked manager")
        with self.assertRaisesRegex(app.Hold, "Installed manager differs"):
            with patch.object(app.os, "execve", side_effect=RuntimeError("must not exec")):
                app._launch(self.p, ["desktop"])

    def test_17c_launch_drops_injected_env_variables(self):
        self.install()
        injected = patch.dict(os.environ, {
            "LD_PRELOAD": "/tmp/evil.so", "LD_LIBRARY_PATH": "/tmp/evil",
            "PYTHONHOME": "/tmp/evil", "PYTHONSTARTUP": "/tmp/evil.py",
            "PYTHONWARNINGS": "ignore"}, clear=False)
        injected.start()
        self.addCleanup(injected.stop)
        with patch.object(app.os, "execve", side_effect=RuntimeError("mock execve")):
            with self.assertRaises(RuntimeError):
                app._launch(self.p, ["desktop"])

    def test_18_foreign_file_in_release_holds_uninstall(self):
        self.install()
        child = self.p.root / "releases" / app.PRODUCTION_PIN.release / "payload/foreign.txt"
        child.write_text("not candidate owned")
        with self.assertRaisesRegex(app.Hold, "Unexpected installed file"):
            app._uninstall(self.p)
        self.assertEqual(child.read_text(), "not candidate owned")

    def test_19_missing_manifest_item_holds(self):
        short = Path(self.temp.name) / "short.txt"
        short.write_text("\n".join(MANIFEST.read_text().splitlines()[:-1]) + "\n")
        with self.assertRaisesRegex(app.Hold, "fixed archive inventory"):
            app._deploy(self.p, ARCHIVE, short, app.PRODUCTION_PIN)

    def test_20_two_approved_test_pins_and_real_pointer_rollback(self):
        self.install()
        # Test-only in-process pin injection; CLI always uses frozen preview10.
        files = {
            "usr/bin/tihulu-satellite-center": b"synthetic launcher",
            "usr/share/tihulu-satellite-center/satellite_center/__init__.py": b"__version__='TEST'",
        }
        archive = Path(self.temp.name) / "v11.tar.gz"
        manifest = Path(self.temp.name) / "v11.txt"
        manifest.write_text("\n".join(files) + "\n")
        with tarfile.open(archive, "w:gz") as t:
            for name, value in files.items():
                meta = tarfile.TarInfo(name)
                meta.size = len(value)
                t.addfile(meta, io.BytesIO(value))
        pin = app.Pin("0.3.0~preview11", hashlib.sha256(archive.read_bytes()).hexdigest(), 2)
        with patch.dict(app.APPROVED_PINS, {pin.release: pin}):
            state = app._deploy(self.p, archive, manifest, pin)
            self.assertEqual(state["active"], pin.release)
            self.assertEqual(state["previous"], app.PRODUCTION_PIN.release)
            self.assertEqual(app._rollback(self.p), app.PRODUCTION_PIN.release)
            with app._locked(self.p, shared=True):
                self.assertEqual(app._pointer(self.p)["active"], app.PRODUCTION_PIN.release)
            app._uninstall(self.p)

    def test_21_negative_tar_member_with_test_only_pin(self):
        payload = Path(self.temp.name) / "evil.tar.gz"
        names = ["usr/bin/tihulu-satellite-center", "usr/share/file"]
        with tarfile.open(payload, "w:gz") as t:
            a = tarfile.TarInfo(names[0])
            a.type = tarfile.SYMTYPE
            a.linkname = "/etc/passwd"
            t.addfile(a)
            b = tarfile.TarInfo(names[1])
            b.size = 1
            t.addfile(b, io.BytesIO(b"x"))
        manifest = Path(self.temp.name) / "evil.txt"
        manifest.write_text("\n".join(names) + "\n")
        pin = app.Pin("0.3.0~preview99", hashlib.sha256(payload.read_bytes()).hexdigest(), 2)
        with self.assertRaisesRegex(app.Hold, "Unsafe/duplicate"):
            app._read_archive(payload, manifest, pin)


    def _write_uninstall_journal(self):
        with app._locked(self.p) as (owner, _):
            installed = self.p.root / "releases" / app.PRODUCTION_PIN.release
            idx = app._index_for(installed, app.PRODUCTION_PIN)
            app._jsonwrite(self.p.root / app.UNINSTALL_JOURNAL,
                {"app": app.APP, "token": owner["token"],
                 "inventory": {app.PRODUCTION_PIN.release: idx["files"]},
                 "previous_active": app.PRODUCTION_PIN.release})

    def test_22_resume_interrupted_partial_uninstall(self):
        self.install()
        self._write_uninstall_journal()
        (self.p.root / app.POINTER).unlink()
        self.p.launcher.unlink()
        directory = self.p.root / "releases" / app.PRODUCTION_PIN.release
        (directory / app.INDEX).unlink()
        (directory / "payload/usr/bin/tihulu-satellite-center").unlink()
        self.assertTrue(app._uninstall(self.p)["removed_releases"])
        self.assertFalse((self.p.root / app.UNINSTALL_JOURNAL).exists())
        self.assertEqual(list((self.p.root / "releases").iterdir()), [])

    def test_23_recover_atomic_promotion_marker(self):
        self.install()
        with app._locked(self.p) as (owner, _):
            r = self.p.root / "releases" / app.PRODUCTION_PIN.release
            files = app._index_for(r, app.PRODUCTION_PIN)["files"]
            app._jsonwrite(r / "STAGING.json",
                {"app": app.APP, "token": owner["token"],
                 "release": app.PRODUCTION_PIN.release, "names": sorted(files),
                 "hashes": files})
        self.assertEqual(app._recover(self.p), 1)
        self.assertFalse((r / "STAGING.json").exists())
        with app._locked(self.p, shared=True):
            app._index_for(r, app.PRODUCTION_PIN)

    def test_24_resume_uninstall_refuses_modified_file(self):
        self.install()
        self._write_uninstall_journal()
        original = self.p.root / "releases" / app.PRODUCTION_PIN.release / "payload/usr/bin/tihulu-satellite-center"
        original.write_bytes(b"foreign change")
        with self.assertRaisesRegex(app.Hold, "Modified/foreign|Mutated file"):
            app._uninstall(self.p)
        self.assertEqual(original.read_bytes(), b"foreign change")

    def test_25_pending_uninstall_blocks_launch(self):
        self.install()
        self._write_uninstall_journal()
        with self.assertRaisesRegex(app.Hold, "Interrupted uninstall"):
            app._launch(self.p, ["desktop"])

    def test_26_rollback_keeps_launcher_and_desktop_bytes_unchanged(self):
        self.install()
        payload = {"usr/bin/tihulu-satellite-center": b"test exe",
                   "usr/share/file": b"test data"}
        artifact = Path(self.temp.name) / "second.tar.gz"
        listing = Path(self.temp.name) / "second.txt"
        listing.write_text("\n".join(payload) + "\n")
        with tarfile.open(artifact, "w:gz") as tar:
            for name, value in payload.items():
                m = tarfile.TarInfo(name)
                m.size = len(value)
                tar.addfile(m, io.BytesIO(value))
        new_pin = app.Pin("0.3.0~preview12", hashlib.sha256(artifact.read_bytes()).hexdigest(), 2)
        before = (self.p.launcher.read_bytes(), self.p.desktop.read_bytes())
        with patch.dict(app.APPROVED_PINS, {new_pin.release: new_pin}):
            app._deploy(self.p, artifact, listing, new_pin)
            self.assertEqual(before, (self.p.launcher.read_bytes(), self.p.desktop.read_bytes()))
            self.assertEqual(app._rollback(self.p), app.PRODUCTION_PIN.release)
            self.assertEqual(before, (self.p.launcher.read_bytes(), self.p.desktop.read_bytes()))

    def test_27_shared_launch_lock_blocks_uninstall(self):
        self.install()
        with app._locked(self.p, shared=True):
            with self.assertRaisesRegex(app.Hold, "Another installer"):
                app._uninstall(self.p)


    def test_28_status_before_install_does_not_write_home(self):
        import contextlib
        from io import StringIO
        self.assertFalse((self.home / ".local").exists())
        with contextlib.redirect_stdout(StringIO()) as capture:
            self.assertEqual(app.main(["status"]), 0)
        self.assertFalse((self.home / ".local").exists())
        self.assertFalse(json.loads(capture.getvalue())["installed"])

    def test_29_doctor_is_passive_and_reports_dependency_keys(self):
        import contextlib
        from io import StringIO
        with contextlib.redirect_stdout(StringIO()) as capture:
            self.assertEqual(app.main(["doctor"]), 0)
        data = json.loads(capture.getvalue())
        self.assertIn("qtwebengine_available", data)
        self.assertIn("skyfield_available", data)
        self.assertFalse((self.home / ".local").exists())


if __name__ == "__main__":
    unittest.main()
