"""Deterministic security interleavings, disposable HOME only.

No SDR, GUI, network, system installs, owner's HOME, or services.
These tests demonstrate specific race boundaries, not general same-UID security.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import installer as app
import race_hardening as hard
from test_installer import ARCHIVE, MANIFEST


class TestAdversarialSameUid(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="satellite-phase-c-attack-")
        self.addCleanup(self.t.cleanup)
        self.home = Path(self.t.name) / "h"
        self.home.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {"HOME": str(self.home)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.p = app.paths()

    def install(self):
        return app._deploy(self.p, ARCHIVE, MANIFEST, app.PRODUCTION_PIN)

    def test_01_swap_root_after_flock_cannot_redirect_to_foreign(self):
        self.install()
        foreign = self.home / "foreign"
        foreign.mkdir()
        sentinel = foreign / "KEEP_FOREIGN"
        sentinel.write_text("not owned")
        with app._locked(self.p) as (_, locked):
            original = self.p.root
            saved = original.with_name(original.name + "-renamed")
            original.rename(saved)
            original.symlink_to(foreign, target_is_directory=True)
            self.assertTrue((locked.p.root / "releases").is_dir(),
                            "pinned fd must still refer to the original inode")
            with self.assertRaisesRegex(hard.RaceHold, "replaced"):
                locked.check()
            self.assertEqual(sentinel.read_text(), "not owned")
            original.unlink()
            saved.rename(original)
        self.assertEqual(sentinel.read_text(), "not owned")

    def test_02_lock_inode_replacement_does_not_allow_two_writers(self):
        self.install()
        with app._locked(self.p) as (_, first):
            lock = self.p.root / app.LOCK
            lock.rename(lock.with_name(".old-lock-for-test"))
            lock.write_bytes(b"new lock inode")
            with self.assertRaisesRegex(app.Hold, "Another installer"):
                with app._locked(self.p):
                    self.fail("Overlapping exclusive critical sections")
            with self.assertRaisesRegex(hard.RaceHold, "lock inode replaced"):
                first.check()

    def test_03_foreign_release_file_inserted_after_preflight_is_preserved(self):
        self.install()
        original = hard.remove_known_tree
        placed = []
        def inject(root_fd, release, known):
            foreign = self.p.root / "releases" / release / "payload/FOREIGN_KEEP"
            foreign.write_bytes(b"late unrelated file")
            placed.append(foreign)
            return original(root_fd, release, known)
        with patch.object(hard, "remove_known_tree", side_effect=inject):
            with self.assertRaisesRegex(app.Hold, "Foreign file"):
                app._uninstall(self.p)
        self.assertEqual(placed[0].read_bytes(), b"late unrelated file")
        self.assertTrue((self.p.root / app.UNINSTALL_JOURNAL).exists())

    def test_04_foreign_nested_release_added_after_preflight_remains(self):
        self.install()
        original = hard.remove_known_tree
        placed = []
        def inject(root_fd, release, known):
            foreign = self.p.root / "releases" / release / "payload/usr/share/FOREIGN_KEEP"
            foreign.write_bytes(b"late nested file")
            placed.append(foreign)
            return original(root_fd, release, known)
        with patch.object(hard, "remove_known_tree", side_effect=inject):
            with self.assertRaisesRegex(app.Hold, "Foreign file"):
                app._uninstall(self.p)
        self.assertEqual(placed[0].read_bytes(), b"late nested file")

    def test_05_launcher_swap_after_preflight_refuses_without_overwrite(self):
        self.p.launcher.parent.mkdir(parents=True)
        orig = hard.register_no_clobber
        def inject(path, expected, **kw):
            if Path(path) == self.p.launcher:
                self.p.launcher.write_bytes(b"FOREIGN-LAUNCHER-KEEP")
            return orig(path, expected, **kw)
        with patch.object(hard, "register_no_clobber", side_effect=inject):
            with self.assertRaisesRegex(app.Hold, "Foreign launcher"):
                self.install()
        self.assertEqual(self.p.launcher.read_bytes(), b"FOREIGN-LAUNCHER-KEEP")

    def test_06_foreign_entry_inserted_between_missing_check_and_open_excl(self):
        self.p.launcher.parent.mkdir(parents=True)
        original = hard._read_owned
        injected = []
        def swap(parent, name, **kw):
            if name == self.p.launcher.name and not injected:
                injected.append(True)
                self.p.launcher.write_bytes(b"FOREIGN-KEEP")
                # Simulate FileNotFoundError from the just-completed old check.
                raise FileNotFoundError(name)
            return original(parent, name, **kw)
        with patch.object(hard, "_read_owned", side_effect=swap):
            with self.assertRaisesRegex(app.Hold, "Launcher inserted"):
                self.install()
        self.assertEqual(self.p.launcher.read_bytes(), b"FOREIGN-KEEP")

    def test_07_uninstall_rejects_launcher_substitution_after_preflight(self):
        self.install()
        original = hard.unregister_exact
        def inject(path, expected):
            if Path(path) == self.p.launcher:
                self.p.launcher.write_bytes(b"FOREIGN-LAUNCHER-PRESERVE")
            return original(path, expected)
        with patch.object(hard, "unregister_exact", side_effect=inject):
            with self.assertRaisesRegex(app.Hold, "Foreign launcher"):
                app._uninstall(self.p)
        self.assertEqual(self.p.launcher.read_bytes(), b"FOREIGN-LAUNCHER-PRESERVE")

    def test_08_swap_launcher_between_hash_check_and_rename_is_quarantined(self):
        self.install()
        original = hard._noreplace
        injected = []
        def late(from_fd, name, to_fd, dest):
            if name == self.p.launcher.name and dest.startswith(".satellite-quarantine-") and not injected:
                injected.append(True)
                self.p.launcher.write_bytes(b"FOREIGN-AFTER-VALIDATION")
            return original(from_fd, name, to_fd, dest)
        with patch.object(hard, "_noreplace", side_effect=late):
            with self.assertRaisesRegex(app.Hold, "Modified|substituted"):
                app._uninstall(self.p)
        # The foreign bytes may be restored to the original name or retained
        # in visible quarantine, but MUST NOT be destroyed.
        parent = self.p.launcher.parent
        candidates = [v for v in parent.iterdir() if v.name == self.p.launcher.name or v.name.startswith(".satellite-quarantine-")]
        self.assertTrue(any(v.read_bytes() == b"FOREIGN-AFTER-VALIDATION" for v in candidates))

    def test_09_recover_rejects_foreign_staging_inserted_after_preflight(self):
        self.install()
        with app._locked(self.p) as (owner, _):
            stage = self.p.root / "releases" / (".staging-" + "c" * 32)
            stage.mkdir(mode=0o700)
            names = MANIFEST.read_text().splitlines()
            app._jsonwrite(stage / "STAGING.json",
                app._stage_metadata(owner, app.PRODUCTION_PIN,
                                    app._approved_inventory(self.p.root, app.PRODUCTION_PIN)))
            # Candidate-tree fidelity: _extract creates every payload ancestor
            # per component at mode 0700, not via parents=True (whose effective
            # mode is 0o777 & ~umask, i.e. 0o775 under umask 0002).
            (stage / "payload").mkdir(mode=0o700)
            (stage / "payload/usr").mkdir(mode=0o700)
            (stage / "payload/usr/bin").mkdir(mode=0o700)
            import tarfile
            with tarfile.open(ARCHIVE, "r:gz") as source:
                genuine = source.extractfile("usr/bin/tihulu-satellite-center").read()
            (stage / "payload/usr/bin/tihulu-satellite-center").write_bytes(genuine)
        orig = hard.remove_known_tree
        placed = []
        def inject(root_fd, dirname, known):
            foreign = stage / "payload/FOREIGN_KEEP"
            foreign.write_bytes(b"late stage file")
            placed.append(foreign)
            return orig(root_fd, dirname, known)
        with patch.object(hard, "remove_known_tree", side_effect=inject):
            with self.assertRaisesRegex(app.Hold, "Foreign file"):
                app._recover(self.p)
        self.assertEqual(placed[0].read_bytes(), b"late stage file")

    def test_10_symlink_substitution_in_release_payload_refuses_descent(self):
        self.install()
        target = self.home / "foreign"
        target.mkdir()
        keep = target / "KEEP"
        keep.write_bytes(b"not ours")
        original = hard.remove_known_tree
        def inject(root_fd, dirname, known):
            payload = self.p.root / "releases" / dirname / "payload"
            backup = payload.with_name("payload-original")
            payload.rename(backup)
            payload.symlink_to(target, target_is_directory=True)
            return original(root_fd, dirname, known)
        with patch.object(hard, "remove_known_tree", side_effect=inject):
            with self.assertRaises((app.Hold, OSError)):
                app._uninstall(self.p)
        self.assertEqual(keep.read_bytes(), b"not ours")

    def test_11_foreign_after_sweep_before_delete_is_preserved(self):
        self.install()
        original = hard.remove_known_file
        injected = []
        def inject(root_fd, rel, digest, **kw):
            if rel.startswith("payload/") and not injected:
                late = self.p.root / "releases" / app.PRODUCTION_PIN.release / "FOREIGN_LAST_MOMENT"
                late.write_bytes(b"NO DELETE")
                injected.append(late)
            return original(root_fd, rel, digest, **kw)
        with patch.object(hard, "remove_known_file", side_effect=inject):
            with self.assertRaisesRegex(app.Hold, "Foreign insertion"):
                app._uninstall(self.p)
        self.assertEqual(injected[0].read_bytes(), b"NO DELETE")

    def test_12_no_wildcard_rmtree_in_security_cleanup(self):
        self.install()
        with patch.object(shutil, "rmtree", side_effect=AssertionError("unsafe recursive delete called")):
            self.assertTrue(app._uninstall(self.p)["removed_releases"])
        self.assertFalse(self.p.launcher.exists())


if __name__ == "__main__":
    unittest.main()
