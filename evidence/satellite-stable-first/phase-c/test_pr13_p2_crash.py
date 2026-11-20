"""SIGKILL/restart/foreign-history/full-vault/ENOSPC pointer crash regression.

Uses actual installer.py/race_hardening.py with disposable HOME and frozen
read-only archive. The only extra release is a synthetic in-process test pin.
No owner machine, SDR, apt, service, privileged operations or public release.
"""
from __future__ import annotations

import errno
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import installer as app
import race_hardening as hard
from test_installer import ARCHIVE, MANIFEST


class TestPointerCrashRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="satellite-p2-pointer-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {"HOME": str(self.home)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.p = app.paths()

    def install(self):
        return app._deploy(self.p, ARCHIVE, MANIFEST, app.PRODUCTION_PIN)

    def second_release(self):
        self.install()
        files = {
            "usr/bin/tihulu-satellite-center": b"synthetic v11 executable",
            "usr/share/tihulu-satellite-center/satellite_center/__init__.py":
                b"__version__='SECOND TEST'",
        }
        artifact = Path(self.temp.name) / "v11.tar.gz"
        manifest = Path(self.temp.name) / "v11.txt"
        manifest.write_text("\n".join(files) + "\n")
        with tarfile.open(artifact, "w:gz") as tar:
            for name, value in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(value)
                tar.addfile(member, io.BytesIO(value))
        self.pin = app.Pin("0.3.0~preview11",
                           hashlib.sha256(artifact.read_bytes()).hexdigest(), 2)
        pins = patch.dict(app.APPROVED_PINS, {self.pin.release: self.pin})
        pins.start()
        self.addCleanup(pins.stop)
        app._deploy(self.p, artifact, manifest, self.pin)
        self.old_active = self.p.root / app.POINTER
        self.old_bytes = self.old_active.read_bytes()
        self.assertEqual(json.loads(self.old_bytes)["active"], self.pin.release)
        return self.old_bytes

    def killed_rollback_at(self, phase):
        """Real OS SIGKILL inside a forked disposable process after a precise fs op."""
        self.second_release()
        ready = os.fork()
        if ready == 0:
            old = hard._noreplace
            def intercept(from_fd, name, to_fd, target):
                if (phase == "before_history"
                        and name == app.POINTER
                        and target.startswith(".history-pointer-")):
                    os.kill(os.getpid(), signal.SIGKILL)
                if (phase == "history_gap"
                        and name.startswith(".write-pointer-")
                        and target == app.POINTER):
                    os.kill(os.getpid(), signal.SIGKILL)
                return old(from_fd, name, to_fd, target)
            hard._noreplace = intercept
            if phase == "after_publish":
                saved = hard.unregister_exact
                def cleanup(target, expected):
                    if str(target).endswith("/" + app.POINTER_TXN):
                        os.kill(os.getpid(), signal.SIGKILL)
                    return saved(target, expected)
                hard.unregister_exact = cleanup
            try:
                app._rollback(self.p)
            except BaseException:
                os._exit(77)
            os._exit(78)
        pid, status = os.waitpid(ready, 0)
        self.assertEqual(pid, ready)
        self.assertTrue(os.WIFSIGNALED(status), f"expected kill, got {status}")
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
        return self.old_bytes

    def transaction(self):
        return app._json(self.p.root / app.POINTER_TXN)

    def test_01_SIGKILL_history_gap_restores_original_bytes(self):
        original = self.killed_rollback_at("history_gap")
        tx = self.transaction()
        history = self.p.root / tx["history"]
        self.assertEqual(history.read_bytes(), original)
        self.assertFalse(self.old_active.exists())
        with self.assertRaisesRegex(app.Hold, "Pending ACTIVE pointer"):
            with app._locked(self.p, shared=True) as (_, session):
                app._pointer(session.p)
        self.assertEqual(app._recover(self.p), 1)
        self.assertEqual(self.old_active.read_bytes(), original)
        self.assertFalse((self.p.root / app.POINTER_TXN).exists())
        self.assertEqual(app._recover(self.p), 0)

    def test_02_SIGKILL_before_history_does_not_change_ACTIVE(self):
        original = self.killed_rollback_at("before_history")
        self.assertEqual(self.old_active.read_bytes(), original)
        self.assertEqual(app._recover(self.p), 1)
        self.assertEqual(self.old_active.read_bytes(), original)
        self.assertEqual(app._recover(self.p), 0)

    def test_03_SIGKILL_after_publication_reconciles_new_ACTIVE(self):
        original = self.killed_rollback_at("after_publish")
        tx = self.transaction()
        committed = app._canonical(tx["new"])
        self.assertEqual(self.old_active.read_bytes(), committed)
        self.assertNotEqual(committed, original)
        self.assertEqual(app._recover(self.p), 1)
        self.assertEqual(self.old_active.read_bytes(), committed)
        self.assertEqual(app._recover(self.p), 0)

    def test_04_tampered_transaction_does_not_recover(self):
        original = self.killed_rollback_at("history_gap")
        tx = self.transaction()
        path = self.p.root / app.POINTER_TXN
        forged = path.read_bytes().replace(b'"schema":1', b'"schema":9')
        path.write_bytes(forged)
        with self.assertRaisesRegex(app.Hold, "Forged|noncanonical"):
            app._recover(self.p)
        self.assertEqual((self.p.root / tx["history"]).read_bytes(), original)
        self.assertEqual(path.read_bytes(), forged)
        self.assertFalse(self.old_active.exists())

    def test_05_unverified_history_not_auto_restored(self):
        self.killed_rollback_at("history_gap")
        tx = self.transaction()
        fake = b"FOREIGN-HISTORY-SHOULD-NOT-BECOME-ACTIVE\x00"
        history = self.p.root / tx["history"]
        history.write_bytes(fake)
        with self.assertRaisesRegex(app.Hold, "Foreign history"):
            app._recover(self.p)
        self.assertEqual(history.read_bytes(), fake)
        self.assertFalse(self.old_active.exists())
        self.assertEqual((self.p.root / app.POINTER_TXN).read_bytes(), app._canonical(tx))

    def test_06_orphan_history_from_legacy_PR13_is_HOLD_not_uninstalled(self):
        original = self.killed_rollback_at("history_gap")
        tx = self.transaction()
        # Simulates PR #13 crash with unjournaled .history-pointer file.
        intent = self.p.root / app.POINTER_TXN
        intent.rename(Path(self.temp.name) / "UNTRUSTED-INTENT")
        with self.assertRaisesRegex(app.Hold, "ACTIVE pointer missing|orphan"):
            app._recover(self.p)
        self.assertEqual((self.p.root / tx["history"]).read_bytes(), original)
        self.assertFalse(self.old_active.exists())

    def test_07_foreign_ACTIVE_insert_after_crash_preserved(self):
        original = self.killed_rollback_at("history_gap")
        tx = self.transaction()
        foreign = b"FOREIGN-ACTIVE-INSERTED-AFTER-CRASH\xff"
        self.old_active.write_bytes(foreign)
        with self.assertRaisesRegex(app.Hold, "Foreign ACTIVE"):
            app._recover(self.p)
        self.assertEqual(self.old_active.read_bytes(), foreign)
        self.assertEqual((self.p.root / tx["history"]).read_bytes(), original)

    def test_08_restore_blocked_by_full_vault_no_auto_delete(self):
        original = self.killed_rollback_at("history_gap")
        vault = self.p.root / ".satellite-preserved"
        vault.mkdir(exist_ok=True)
        existing = len(list(vault.iterdir()))
        self.assertLess(existing, 4096)
        for i in range(4096 - existing):
            (vault / f"b{i:04d}").write_bytes(b"x")
        with self.assertRaisesRegex(app.Hold, "vault full|Preservation vault"):
            app._recover(self.p)
        self.assertEqual(self.old_active.read_bytes(), original,
                         "Restore occurs safely before intent-retention HOLD")
        self.assertTrue((self.p.root / app.POINTER_TXN).exists())
        self.assertEqual(len(list(vault.iterdir())), 4096)
        # Free capacity ONLY in disposable test; production performs no cleanup.
        (vault / "b0000").unlink()
        self.assertEqual(app._recover(self.p), 1)
        self.assertEqual(self.old_active.read_bytes(), original)

    def test_09_full_vault_prevents_start_of_new_transaction(self):
        original = self.second_release()
        vault = self.p.root / ".satellite-preserved"
        vault.mkdir(exist_ok=True)
        existing = len(list(vault.iterdir()))
        self.assertLess(existing, 4096)
        for i in range(4096 - existing):
            (vault / f"b{i:04d}").write_bytes(b"foreign-vault-data")
        with self.assertRaisesRegex(app.Hold, "vault unavailable/full"):
            app._rollback(self.p)
        self.assertEqual(self.old_active.read_bytes(), original)
        self.assertFalse((self.p.root / app.POINTER_TXN).exists())
        self.assertEqual((vault / "b0000").read_bytes(), b"foreign-vault-data")

    def test_10_ENOSPC_intent_creation_leaves_previous_pointer_intact(self):
        original = self.second_release()
        old_open = app.os.open
        def disk_full(path, *args, **kwargs):
            if path == app.POINTER_TXN:
                raise OSError(errno.ENOSPC, "synthetic CI-only disk full")
            return old_open(path, *args, **kwargs)
        with patch.object(app.os, "open", side_effect=disk_full):
            with self.assertRaisesRegex(app.Hold, "transaction persistence"):
                app._rollback(self.p)
        self.assertEqual(self.old_active.read_bytes(), original)
        self.assertFalse((self.p.root / app.POINTER_TXN).exists())

    def test_11_invalid_candidate_after_SIGKILL_keeps_all_bytes(self):
        original = self.killed_rollback_at("history_gap")
        tx = self.transaction()
        staged = self.p.root / tx["candidate"]
        alien = b"FOREIGN-STAGED-NEW-POINTER\x00"
        staged.write_bytes(alien)
        with self.assertRaisesRegex(app.Hold, "Foreign candidate"):
            app._recover(self.p)
        self.assertEqual(staged.read_bytes(), alien)
        self.assertEqual((self.p.root / tx["history"]).read_bytes(), original)

    def test_12_real_process_restart_reconciles_first_install_kill(self):
        # Unlike fork tests, this uses an independently restarted Python CLI.
        child = StringRaw = """
import os, signal
from pathlib import Path
import installer as app
import race_hardening as hard
old = hard._noreplace
def inject(from_fd, name, to_fd, target):
    if name.startswith('.write-pointer-') and target == app.POINTER:
        os.kill(os.getpid(), signal.SIGKILL)
    return old(from_fd, name, to_fd, target)
hard._noreplace = inject
app._deploy(app.paths(), Path(%r), Path(%r), app.PRODUCTION_PIN)
""" % (str(ARCHIVE), str(MANIFEST))
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PYTHONPATH"] = str(Path(app.__file__).parent)
        child_run = subprocess.run([sys.executable, "-c", child],
                                   env=env, capture_output=True, text=True)
        self.assertEqual(child_run.returncode, -signal.SIGKILL, child_run.stderr)
        self.assertTrue((self.p.root / app.POINTER_TXN).exists())
        self.assertFalse((self.p.root / app.POINTER).exists())
        restored = subprocess.run([sys.executable, str(Path(app.__file__)), "recover"],
                                  env=env, capture_output=True, text=True)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads((self.p.root / app.POINTER).read_bytes())["active"],
                         app.PRODUCTION_PIN.release)
        status = subprocess.run([sys.executable, str(Path(app.__file__)), "status"],
                                env=env, capture_output=True, text=True)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertTrue(json.loads(status.stdout)["installed"])
        repeated = subprocess.run([sys.executable, str(Path(app.__file__)), "recover"],
                                  env=env, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)

    def test_13_forged_owner_token_intent_rejected_byte_for_byte(self):
        original = self.killed_rollback_at("history_gap")
        tx = self.transaction()
        forged = {**tx, "owner_token": "0" * 32}
        path = self.p.root / app.POINTER_TXN
        path.write_bytes(app._canonical(forged))
        with self.assertRaisesRegex(app.Hold, "Forged|noncanonical"):
            app._recover(self.p)
        self.assertEqual(path.read_bytes(), app._canonical(forged))
        self.assertEqual((self.p.root / tx["history"]).read_bytes(), original)

    def test_14_successful_recovery_preserves_foreign_neighbor(self):
        original = self.killed_rollback_at("history_gap")
        foreign = self.p.root / ".satellite-preserved" / "NEIGHBOR-FOREIGN"
        foreign.parent.mkdir(exist_ok=True)
        foreign.write_bytes(b"NEVER-TOUCH-EXISTING-FOREIGN")
        self.assertEqual(app._recover(self.p), 1)
        self.assertEqual(self.old_active.read_bytes(), original)
        self.assertEqual(foreign.read_bytes(), b"NEVER-TOUCH-EXISTING-FOREIGN")

    def test_15_legacy_orphan_history_without_releases_is_hold(self):
        """P2-SAT15-02 completeness: a legacy `.history-*` orphan with NO approved
        releases must not be reported as 'not installed'."""
        self.install()
        pointer = self.p.root / app.POINTER
        orphan = self.p.root / (".history-" + "a" * 32)
        # Remove every approved release through the real retention path so the
        # 'approved releases exist' branch cannot fire.
        with app._locked(self.p) as (owner, session):
            for item in list((session.p.root / "releases").iterdir()):
                pin = app._installed_pin(item.name)
                approved = app._approved_inventory(session.p.root, pin)
                known = {"payload/" + rel: digest for rel, digest in approved.items()}
                known["INDEX.json"] = hashlib.sha256(
                    app._canonical(app._release_index(pin, approved))).hexdigest()
                hard.remove_known_tree(session.root_fd, item.name, known)
        pointer.rename(orphan)
        with self.assertRaisesRegex(app.Hold, "orphan"):
            with app._locked(self.p, shared=True) as (_, session):
                app._pointer(session.p)
        self.assertFalse(pointer.exists())
        self.assertTrue(orphan.exists())

    def test_16_stranded_quarantine_tombstone_is_reconciled_not_held(self):
        """A SIGKILL inside remove_known_file strands `.satellite-quarantine-*` in the
        candidate root; recover must retain it in the vault instead of HOLDing forever
        on this code's own transient name. A wrong-length lookalike stays foreign."""
        self.install()
        tomb = self.p.root / (".satellite-quarantine-" + "b" * 32)
        tomb.write_bytes(b"STRANDED-TOMBSTONE-BYTES-PRESERVE")
        foreign = self.p.root / (".satellite-quarantine-" + "z" * 31)
        foreign.write_bytes(b"WRONG-LENGTH-NAME-IS-FOREIGN")
        try:
            with self.assertRaisesRegex(app.Hold, "Foreign files in candidate root"):
                app._recover(self.p)
        finally:
            foreign.unlink()
        self.assertEqual(app._recover(self.p), 1)
        self.assertFalse(tomb.exists())
        vault = self.p.root / ".satellite-preserved"
        self.assertTrue(any(
            f.read_bytes() == b"STRANDED-TOMBSTONE-BYTES-PRESERVE"
            for f in vault.iterdir()))


if __name__ == "__main__":
    unittest.main()
