"""PR #13 P1 ancestor-swap and P2 crash-intent tests against REAL modules.

Only disposable HOME, pre-existing frozen preview10 archive, and test-owned
temporary directories/processes. No SDR/USB, services or owner installations.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import installer as app
import race_hardening as hard
from test_installer import ARCHIVE, MANIFEST


class TestPr13ScopedRemediation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="satellite-pr13-remediation-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {"HOME": str(self.home)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.p = app.paths()

    def install(self):
        return app._deploy(self.p, ARCHIVE, MANIFEST, app.PRODUCTION_PIN)

    def test_01_all_ancestors_no_follow_never_open_aliased_foreign_parent(self):
        owned = self.home / "owned"
        owned.mkdir(mode=0o700)
        foreign = self.home / "unrelated-application"
        foreign.mkdir(mode=0o700)
        sentinel = foreign / "EXISTING"
        sentinel.write_bytes(b"FOREIGN-BYTES-UNCHANGED")
        owned.rename(self.home / "moved-owned")
        owned.symlink_to(foreign, target_is_directory=True)
        with self.assertRaises((OSError, hard.RaceHold, app.Hold)):
            hard.register_no_clobber(owned / "launcher", b"SHOULD-NOT-CREATE")
        self.assertFalse((foreign / "launcher").exists())
        self.assertEqual(sentinel.read_bytes(), b"FOREIGN-BYTES-UNCHANGED")

    def test_02_launcher_intermediate_local_swap_before_bin_lookup(self):
        local = self.home / ".local"
        (local / "bin").mkdir(parents=True)
        foreign = self.home / "unrelated-application"
        (foreign / "bin").mkdir(parents=True)
        sentinel = foreign / "bin" / "EXISTING"
        sentinel.write_bytes(b"FOREIGN-LAUNCHER-BYTES")
        original = os.open
        did_swap = []
        def intercepted(path, flags, *args, **kwargs):
            if path == "bin" and kwargs.get("dir_fd") is not None and not did_swap:
                did_swap.append(True)
                local.rename(self.home / ".local-original")
                local.symlink_to(foreign, target_is_directory=True)
            return original(path, flags, *args, **kwargs)
        with patch.object(hard.os, "open", side_effect=intercepted):
            # Either safely creates under the pinned old inode or fails closed.
            try:
                hard.register_no_clobber(self.p.launcher, b"OWNED-ONLY", mode=0o700)
            except (hard.RaceHold, OSError):
                pass
        self.assertEqual(did_swap, [True])
        self.assertFalse((foreign / "bin" / self.p.launcher.name).exists())
        self.assertEqual(sentinel.read_bytes(), b"FOREIGN-LAUNCHER-BYTES")

    def test_03_extraction_intermediate_usr_swap_before_bin_lookup(self):
        foreign = self.home / "unrelated-application"
        (foreign / "bin").mkdir(parents=True)
        sentinel = foreign / "bin" / "EXISTING"
        sentinel.write_bytes(b"FOREIGN-PAYLOAD-BYTES")
        original = os.open
        did_swap = []
        with app._locked(self.p) as (owner, session):
            root = session.p.root
            def intercepted(path, flags, *args, **kwargs):
                if (path == "bin" and kwargs.get("dir_fd") is not None and
                        not did_swap):
                    stages = list((root / "releases").glob(".staging-*"))
                    if stages:
                        usr = stages[0] / "payload/usr"
                        if usr.is_dir() and not usr.is_symlink():
                            did_swap.append(True)
                            usr.rename(usr.with_name("usr-original"))
                            usr.symlink_to(foreign, target_is_directory=True)
                return original(path, flags, *args, **kwargs)
            with patch.object(app.os, "open", side_effect=intercepted):
                with self.assertRaises((app.Hold, hard.RaceHold, OSError)):
                    app._extract(root, {
                        "usr/bin/tihulu-satellite-center": b"OWNED-PAYLOAD-ONLY"
                    }, app.PRODUCTION_PIN, owner)
        self.assertEqual(did_swap, [True])
        self.assertFalse((foreign / "bin/tihulu-satellite-center").exists())
        self.assertEqual(sentinel.read_bytes(), b"FOREIGN-PAYLOAD-BYTES")

    def test_04_source_cache_intermediate_ancestor_symlink_refused(self):
        self.install()
        source = self.p.root / "sources"
        external = self.home / "unrelated-application"
        external.mkdir(mode=0o700)
        sentinel = external / "EXISTING"
        sentinel.write_bytes(b"FOREIGN-SOURCE-BYTES")
        with app._locked(self.p) as (_, session):
            # Simulate same-UID swap after the authorized root was locked:
            # a renamed source cache must never redirect the new file.
            source.rename(source.with_name("sources-original"))
            source.symlink_to(external, target_is_directory=True)
            with self.assertRaises((app.Hold, hard.RaceHold, OSError)):
                app._store_approved_source(session.p, ARCHIVE, app.PRODUCTION_PIN)
        self.assertFalse((external / (app.PRODUCTION_PIN.release + ".tar.gz")).exists())
        self.assertEqual(sentinel.read_bytes(), b"FOREIGN-SOURCE-BYTES")

    def _crash_after_history(self, *, sigkill=False):
        """Actual child imports original modules and dies at exact rename boundary."""
        payload = '''
import os, signal
import installer as app
import race_hardening as hard
from unittest.mock import patch
p = app.paths()
original = hard._noreplace
def crash(from_fd, name, to_fd, dest):
    outcome = original(from_fd, name, to_fd, dest)
    if name == "ACTIVE.json" and dest.startswith(".history-"):
        if SIGNAL_KILL:
            os.kill(os.getpid(), signal.SIGKILL)
        os._exit(137)
    return outcome
with app._locked(p) as (_, session):
    with patch.object(hard, "_noreplace", side_effect=crash):
        app._jsonwrite(session.p.root / "ACTIVE.json",
                       {"active": None, "previous": app.PRODUCTION_PIN.release})
'''.replace("SIGNAL_KILL", "True" if sigkill else "False")
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        done = subprocess.run([sys.executable, "-c", payload],
                              cwd=str(Path(__file__).resolve().parent),
                              env=env, check=False, timeout=15,
                              capture_output=True)
        self.assertEqual(done.returncode, -signal.SIGKILL if sigkill else 137,
                         done.stderr.decode(errors="replace"))
        self.assertFalse((self.p.root / app.POINTER).exists())
        self.assertTrue((self.p.root / hard.ACTIVE_INTENT).exists())

    def test_05_sigkill_durable_history_to_active_recovery(self):
        self.install()
        before = (self.p.root / app.POINTER).read_bytes()
        self._crash_after_history(sigkill=True)
        with self.assertRaisesRegex(app.Hold, "Pending ACTIVE"):
            with app._locked(self.p, shared=True) as (_, session):
                app._pointer(session.p)
        self.assertEqual(app._recover(self.p), 0)
        self.assertEqual((self.p.root / app.POINTER).read_bytes(), before)
        self.assertFalse((self.p.root / hard.ACTIVE_INTENT).exists())

    def test_06_os_exit_simulated_abrupt_interruption_recovered(self):
        self.install()
        before = (self.p.root / app.POINTER).read_bytes()
        self._crash_after_history(sigkill=False)
        app._recover(self.p)
        self.assertEqual((self.p.root / app.POINTER).read_bytes(), before)

    def test_07_tampered_intent_mac_refuses_history(self):
        self.install()
        self._crash_after_history(sigkill=True)
        intent = self.p.root / hard.ACTIVE_INTENT
        payload = json.loads(intent.read_text())
        payload["mac"] = "0" * 64
        forged = hard._canonical_json(payload)
        intent.write_bytes(forged)
        with self.assertRaisesRegex(app.Hold, "Unauthenticated"):
            app._recover(self.p)
        self.assertEqual(intent.read_bytes(), forged)
        self.assertFalse((self.p.root / app.POINTER).exists())

    def test_08_modified_history_never_restored(self):
        self.install()
        self._crash_after_history(sigkill=False)
        intent = json.loads((self.p.root / hard.ACTIVE_INTENT).read_text())
        history = self.p.root / intent["history"]
        foreign = b"FORGED-OLD-POINTER-PRESERVE"
        history.write_bytes(foreign)
        with self.assertRaisesRegex(app.Hold, "Untrusted/modified history"):
            app._recover(self.p)
        self.assertEqual(history.read_bytes(), foreign)
        self.assertFalse((self.p.root / app.POINTER).exists())

    def test_08b_source_pin_tamper_blocks_history_recovery(self):
        self.install()
        self._crash_after_history(sigkill=False)
        source = (self.p.root / "sources" /
                  (app.PRODUCTION_PIN.release + ".tar.gz"))
        source.write_bytes(b"FOREIGN-UNTRUSTED-ARCHIVE")
        intent = (self.p.root / hard.ACTIVE_INTENT).read_bytes()
        with self.assertRaisesRegex(app.Hold, "Trusted source archive|pinned source|Source"):
            app._recover(self.p)
        self.assertEqual(source.read_bytes(), b"FOREIGN-UNTRUSTED-ARCHIVE")
        self.assertEqual((self.p.root / hard.ACTIVE_INTENT).read_bytes(), intent)
        self.assertFalse((self.p.root / app.POINTER).exists())

    def test_08c_modified_history_rejected_even_if_next_pointer_exists(self):
        self.install()
        self._crash_after_history(sigkill=False)
        intent = json.loads((self.p.root / hard.ACTIVE_INTENT).read_text())
        history = self.p.root / intent["history"]
        foreign = b"FOREIGN-HISTORY-KEEP-EVEN-AFTER-COMMIT"
        history.write_bytes(foreign)
        # Simulate a completed next-pointer publication with expected digest.
        new = app._canonical({"active": None, "previous": app.PRODUCTION_PIN.release})
        self.assertEqual(__import__("hashlib").sha256(new).hexdigest(),
                         intent["new_sha256"])
        (self.p.root / app.POINTER).write_bytes(new)
        with self.assertRaisesRegex(app.Hold, "Untrusted/modified history"):
            app._recover(self.p)
        self.assertEqual(history.read_bytes(), foreign)
        self.assertEqual((self.p.root / app.POINTER).read_bytes(), new)

    def test_08d_history_mutation_after_validation_cannot_become_active(self):
        self.install()
        expected_old = (self.p.root / app.POINTER).read_bytes()
        self._crash_after_history(sigkill=False)
        intent = json.loads((self.p.root / hard.ACTIVE_INTENT).read_text())
        history = self.p.root / intent["history"]
        alien = b"LATE-FOREIGN-HISTORY-BYTES-RETAIN"
        original = hard._noreplace
        injected = []
        def inject(src_fd, src, dst_fd, dst):
            if src.startswith(".write-") and dst == app.POINTER and not injected:
                history.write_bytes(alien)
                injected.append(True)
            return original(src_fd, src, dst_fd, dst)
        with patch.object(hard, "_noreplace", side_effect=inject):
            with self.assertRaisesRegex(app.Hold, "Late ACTIVE/history substitution"):
                app._recover(self.p)
        self.assertEqual(injected, [True])
        self.assertEqual(history.read_bytes(), alien)
        # Recovery copied ONLY authenticated, SHA-source-validated old bytes.
        self.assertEqual((self.p.root / app.POINTER).read_bytes(), expected_old)
        self.assertTrue((self.p.root / hard.ACTIVE_INTENT).exists())

    def test_09_unsigned_history_name_alone_never_restores_pointer(self):
        self.install()
        pointer = self.p.root / app.POINTER
        history = self.p.root / (".history-" + "a" * 32)
        pointer.rename(history)
        # P2-SAT15-02 (Agent 02): an unjournaled orphan history pointer is an
        # explicit manual-review HOLD, never a silent "not installed". Recovery
        # must refuse to resurrect it and must preserve the foreign bytes.
        with self.assertRaisesRegex(app.Hold, "orphan|ACTIVE pointer missing"):
            app._recover(self.p)
        self.assertFalse(pointer.exists())
        self.assertTrue(history.exists())

    def test_10_successful_pointer_update_leaves_no_pending_intent(self):
        self.install()
        with app._locked(self.p) as (_, session):
            app._jsonwrite(session.p.root / app.POINTER,
                           {"active": None, "previous": app.PRODUCTION_PIN.release})
        self.assertFalse((self.p.root / hard.ACTIVE_INTENT).exists())
        self.assertEqual(app._recover(self.p), 0)


if __name__ == "__main__":
    unittest.main()
