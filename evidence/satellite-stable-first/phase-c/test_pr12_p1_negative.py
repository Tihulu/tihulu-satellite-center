"""P1-SAT12-01/02/03/04 negative fixtures against ACTUAL installer and helper.

Each test uses a disposable HOME, frozen actual preview10 package and library
functions; asserts HOLD and byte-for-byte survival of attacker-owned bytes.
No SDR, owner host, service management, GUI, sudo, network or installer release.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import installer as app
import race_hardening as security
from test_installer import ARCHIVE, MANIFEST


class TestFourIndependentP1Negatives(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="satellite-pr12-p1-negative-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "disposable-home"
        self.home.mkdir(mode=0o700)
        env = patch.dict(os.environ, {"HOME": str(self.home)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.p = app.paths()

    def install(self):
        return app._deploy(self.p, ARCHIVE, MANIFEST, app.PRODUCTION_PIN)

    def journal(self):
        with app._locked(self.p) as (owner, sess):
            pin = app.PRODUCTION_PIN
            inventory = {pin.release: app._approved_inventory(sess.p.root, pin)}
            app._jsonwrite(sess.p.root / app.UNINSTALL_JOURNAL,
                {"app": app.APP, "token": owner["token"],
                 "inventory": inventory, "previous_active": pin.release})

    def test_01_forged_uninstall_inventory_CANNOT_authorize_foreign_payload(self):
        self.install()
        self.journal()
        target = self.p.root / "releases" / app.PRODUCTION_PIN.release / "payload/usr/bin/tihulu-satellite-center"
        alien = b"P1-SAT12-01-FOREIGN-PAYLOAD-\x00bytes"
        target.write_bytes(alien)
        journal = self.p.root / app.UNINSTALL_JOURNAL
        forged = json.loads(journal.read_text())
        forged["inventory"][app.PRODUCTION_PIN.release]["usr/bin/tihulu-satellite-center"] = hashlib.sha256(alien).hexdigest()
        journal.write_bytes(app._canonical(forged))
        with self.assertRaisesRegex(app.Hold, "Forged uninstall journal"):
            app._uninstall(self.p)
        self.assertEqual(target.read_bytes(), alien)
        self.assertEqual(journal.read_bytes(), app._canonical(forged))

    def test_02_modified_INDEX_metadata_CANNOT_be_deleted(self):
        self.install()
        self.journal()
        target = self.p.root / "releases" / app.PRODUCTION_PIN.release / app.INDEX
        foreign = b"P1-SAT12-02-ALIEN-INDEX\x00\xff"
        target.write_bytes(foreign)
        with self.assertRaisesRegex(app.Hold, "Modified metadata|Modified/foreign|Mutated file"):
            app._uninstall(self.p)
        self.assertEqual(target.read_bytes(), foreign)

    def test_02b_modified_STAGING_metadata_CANNOT_be_deleted(self):
        self.install()
        with app._locked(self.p) as (owner, _):
            stage = self.p.root / "releases" / (".staging-" + "d"*32)
            stage.mkdir(mode=0o700)
            authorized = app._approved_inventory(self.p.root, app.PRODUCTION_PIN)
            app._jsonwrite(stage / "STAGING.json",
                app._stage_metadata(owner, app.PRODUCTION_PIN, authorized))
        index = stage / "STAGING.json"
        foreign = b"P1-SAT12-02-FOREIGN-STAGING"
        index.write_bytes(foreign)
        with self.assertRaises(app.Hold):
            app._recover(self.p)
        self.assertEqual(index.read_bytes(), foreign)

    def test_03_last_quarantine_swap_never_unlinks_foreign_bytes(self):
        self.install()
        alien = b"P1-SAT12-03-LATE-FOREIGN-AT-TOMB\x00"
        orig = security._noreplace
        injected = []
        def late(source_fd, from_name, dest_fd, to_name):
            if from_name.startswith(".satellite-quarantine-") and to_name.startswith("preserved-") and not injected:
                with os.fdopen(os.open(from_name, os.O_WRONLY | os.O_TRUNC,
                                       dir_fd=source_fd), "wb") as output:
                    output.write(alien)
                injected.append(True)
            return orig(source_fd, from_name, dest_fd, to_name)
        with patch.object(security, "_noreplace", side_effect=late):
            with self.assertRaisesRegex(app.Hold, "Late quarantine replacement"):
                app._uninstall(self.p)
        self.assertEqual(injected, [True])
        vaults = [self.p.root / ".satellite-preserved",
                  self.p.bin_dir / ".satellite-preserved"]
        self.assertTrue(any(d.is_dir() and any(
                            p.is_file() and p.read_bytes() == alien for p in d.iterdir())
                            for d in vaults),
                        "Late injected foreign bytes must be retained byte-for-byte")

    def test_04_ACTIVE_metadata_no_clobber_when_foreign_inserted_at_commit(self):
        alien = b"P1-SAT12-04-FOREIGN-ACTIVE\x01"
        orig = security._noreplace
        done = []
        def late(src_fd, src_name, dst_fd, dst_name):
            if dst_name == app.POINTER and src_name.startswith(".write-") and not done:
                fd = os.open(dst_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                             0o600, dir_fd=dst_fd)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(alien)
                done.append(True)
            return orig(src_fd, src_name, dst_fd, dst_name)
        with patch.object(security, "_noreplace", side_effect=late):
            with self.assertRaisesRegex(app.Hold, "Destination already exists"):
                self.install()
        self.assertEqual(done, [True])
        self.assertEqual((self.p.root / app.POINTER).read_bytes(), alien)

    def test_04b_existing_ACTIVE_swap_retained_in_history_and_restored(self):
        self.install()
        prior = (self.p.root / app.POINTER).read_bytes()
        alien = b"P1-SAT12-04-FOREIGN-EXISTING-ACTIVE"
        orig = security._noreplace
        done = []
        def late(src_fd, src_name, dst_fd, dst_name):
            if src_name == app.POINTER and dst_name.startswith(".history-") and not done:
                with os.fdopen(os.open(src_name, os.O_WRONLY | os.O_TRUNC,
                                       dir_fd=src_fd), "wb") as stream:
                    stream.write(alien)
                done.append(True)
            return orig(src_fd, src_name, dst_fd, dst_name)
        with app._locked(self.p) as (_, session):
            with patch.object(security, "_noreplace", side_effect=late):
                with self.assertRaisesRegex(app.Hold, "Metadata swapped after preflight"):
                    app._jsonwrite(session.p.root / app.POINTER,
                                   {"active": None, "previous": app.PRODUCTION_PIN.release})
        self.assertEqual(done, [True])
        self.assertEqual((self.p.root / app.POINTER).read_bytes(), alien)
        self.assertNotEqual(prior, alien)


if __name__ == "__main__":
    unittest.main()
