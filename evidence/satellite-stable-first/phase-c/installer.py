#!/usr/bin/env python3
"""Private Satellite stable-first candidate: user-scoped, pin-only side-by-side installer.

No sudo, apt, device probes, SDR access, service management, or downloads.
Never run on the owner's machine without explicit approval. Tests use disposable HOME.
This release channel accepts only the frozen preview10 digest. Independent QA HOLD.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace

import race_hardening as security
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import uuid

APP = "tihulu-satellite-stable-first"
PIN_VERSION = "0.3.0~preview10"
PIN_SHA = "291b88a05ec21e3becab01bca2c5c6311fba2a29673bb18317a8376570380c0a"
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_UNPACKED = 32 * 1024 * 1024
MAX_SINGLE_FILE = 16 * 1024 * 1024
MAX_METADATA = 128 * 1024
RELEASE_RE = re.compile(r"^preview[0-9]+-[0-9a-f]{12}$")
STAGE_RE = re.compile(r"^\.staging-[0-9a-f]{32}$")
ENTRY = "usr/share/tihulu-satellite-center"
MARKER = "OWNER.json"
INDEX = "INDEX.json"
POINTER = "ACTIVE.json"
LOCK = ".lock"
UNINSTALL_JOURNAL = "UNINSTALL.json"
POINTER_TXN = "POINTER_TXN.json"


class Hold(RuntimeError):
    """Safety refusal; leave all existing user files in place."""


@dataclass(frozen=True)
class Pin:
    version: str
    sha256: str
    count: int = 86

    @property
    def release(self):
        tag = self.version.split("~")[-1]
        if not re.fullmatch(r"preview[0-9]+", tag):
            raise Hold("Unsupported version identifier")
        return tag + "-" + self.sha256[:12]


PRODUCTION_PIN = Pin(PIN_VERSION, PIN_SHA)
# Closed allowlist. The tests temporarily add mock digests in their own Python process.
APPROVED_PINS = {PRODUCTION_PIN.release: PRODUCTION_PIN}


@dataclass(frozen=True)
class Paths:
    home: Path
    base: Path
    root: Path
    bin_dir: Path
    launcher: Path
    desktop_dir: Path
    desktop: Path
    runtime_data: Path
    runtime_home: Path
    canonical_root: Path | None = None


def paths() -> Paths:
    if os.getuid() == 0:
        raise Hold("Refuse root: this is a user-scoped installer")
    home = Path.home().absolute()
    if not home.is_absolute():
        raise Hold("HOME must be absolute")
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local/state")
    if not data.is_absolute() or not state.is_absolute():
        raise Hold("XDG paths must be absolute")
    for candidate in (data, state):
        if candidate == home or home not in candidate.parents:
            raise Hold("XDG paths must be strictly below HOME")
    root = data / APP
    return Paths(home, data, root, home / ".local/bin", home / ".local/bin" / APP,
                 data / "applications", data / "applications" / (APP + ".desktop"),
                 data / (APP + "-runtime"), state / (APP + "-home"))


def _stat(path: Path):
    """lstat the leaf using a descriptor pinned through every ancestor."""
    path = Path(path).absolute()
    try:
        parent = security.pinned_dir_fd(path.parent)
    except FileNotFoundError:
        return None
    try:
        try:
            return os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None
    finally:
        os.close(parent)


def _dir(path: Path, *, create=False, private=False):
    """Never follow any ancestor symlink, including after preflight."""
    path = Path(path).absolute()
    try:
        fd = security.pinned_dir_fd(path, create=create, private=private)
        os.close(fd)
    except security.RaceHold as error:
        raise Hold(str(error)) from error
    return path


def _file(path: Path, *, limit=MAX_METADATA):
    s = _stat(path)
    if (s is None or not stat.S_ISREG(s.st_mode) or
            s.st_uid != os.getuid() or s.st_nlink != 1 or s.st_size > limit):
        raise Hold(f"Not an owned regular file: {path}")
    return path


def _read(path: Path, limit=MAX_METADATA) -> bytes:
    # Path.absolute (not resolve) retains symlink rejection on each component.
    path = Path(path).absolute()
    parent = security.pinned_dir_fd(path.parent)
    try:
        fd = os.open(path.name, security.O_FILE, dir_fd=parent)
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                    or st.st_nlink != 1 or st.st_size > limit):
                raise Hold("Unsafe opened input file")
            value = b""
            while len(value) <= limit:
                chunk = os.read(fd, min(1024 * 1024, limit + 1 - len(value)))
                if not chunk:
                    break
                value += chunk
            if len(value) != st.st_size or len(value) > limit:
                raise Hold("Input size/identity changed during descriptor read")
            return value
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _json(path: Path):
    try:
        return json.loads(_read(path).decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise Hold(f"Malformed metadata: {path}") from error


def _syncdir(path: Path):
    fd = security.pinned_dir_fd(path)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic(path: Path, payload: bytes, *, mode=0o600):
    """No overwriting rename: metadata updates retain old bytes in history.

    A changed canonical ACTIVE pointer goes through the durable, HMAC-bound
    crash-intent publication so a SIGKILL at the history rename is recoverable.
    """
    try:
        security.publish_metadata_no_clobber(
            path, payload, mode=mode,
            pinned_sha=PIN_SHA if Path(path).name == POINTER else None)
    except security.RaceHold as error:
        raise Hold(str(error)) from error


def _canonical(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")



def _jsonwrite(path: Path, obj):
    _atomic(path, _canonical(obj))


def _ensure_root(p: Paths):
    _dir(p.home)
    _dir(p.base, create=True)
    if _stat(p.root) is None:
        p.root.mkdir(mode=0o700)
    _dir(p.root, private=True)
    mark = p.root / MARKER
    if _stat(mark) is None:
        if list(p.root.iterdir()):
            raise Hold("Existing unmarked candidate root: foreign content")
        _jsonwrite(mark, {"app": APP, "uid": os.getuid(), "schema": 1,
                          "token": uuid.uuid4().hex})
    owner = _json(mark)
    if not isinstance(owner, dict) or set(owner) != {"app", "uid", "schema", "token"}:
        raise Hold("Invalid root marker")
    if owner["app"] != APP or owner["uid"] != os.getuid() or owner["schema"] != 1 or not re.fullmatch("[a-f0-9]{32}", str(owner["token"])):
        raise Hold("Unrecognized candidate owner marker")
    known = {MARKER, LOCK, POINTER, POINTER_TXN, UNINSTALL_JOURNAL, security.ACTIVE_INTENT, "releases", "sources", ".satellite-preserved", "manager.py", "race_hardening.py"}
    for entry in p.root.iterdir():
        if entry.name not in known and not re.fullmatch(r"\.write-[0-9a-f]{32}", entry.name):
            raise Hold(f"Foreign root entry: {entry.name}")
    return owner


@contextmanager
def _locked(p: Paths, shared=False):
    """Lock pinned base and root directory inodes, independent of .lock swaps."""
    _dir(p.home)
    _dir(p.base, create=True)
    try:
        with security.PinnedRoot(p.base, APP, shared=shared) as session:
            pinned = replace(p, root=session.alias, canonical_root=p.root)
            marker = pinned.root / MARKER
            if _stat(marker) is None:
                if set(os.listdir(session.root_fd)) != {LOCK}:
                    raise Hold("Foreign nonempty root cannot be claimed")
                raw = json.dumps({"app": APP, "uid": os.getuid(), "schema": 1,
                                  "token": uuid.uuid4().hex}, sort_keys=True).encode()
                fd = os.open(MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=session.root_fd)
                with os.fdopen(fd, "wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                os.fsync(session.root_fd)
            owner = _json(marker)
            if not isinstance(owner, dict) or set(owner) != {"app", "uid", "schema", "token"}:
                raise Hold("Invalid root marker")
            if (owner["app"] != APP or owner["uid"] != os.getuid()
                    or owner["schema"] != 1
                    or not re.fullmatch("[a-f0-9]{32}", str(owner["token"]))):
                raise Hold("Unrecognized root owner marker")
            allowed = {MARKER, LOCK, POINTER, POINTER_TXN, UNINSTALL_JOURNAL,
                       security.ACTIVE_INTENT, "releases", "sources",
                       ".satellite-preserved", "manager.py", "race_hardening.py"}
            # `.satellite-quarantine-*` is this code's own transient tombstone name
            # during a vault move; a SIGKILL between its two renames can strand it, so
            # recover must be able to reconcile it (never HOLD the root forever). Its
            # bytes stay unauthenticated: retained in the vault, never deleted.
            unexpected = {n for n in os.listdir(session.root_fd)
                          if n not in allowed and not re.fullmatch(
                              r"\.history-[0-9a-f]{32}|\.write-[0-9a-f]{32}|\.history-pointer-[0-9a-f]{32}|\.write-pointer-[0-9a-f]{32}|\.satellite-quarantine-[0-9a-f]{32}", n)}
            if unexpected:
                raise Hold("Foreign files in candidate root")
            session.p = pinned
            session.check()
            yield owner, session
    except security.RaceHold as error:
        raise Hold(str(error)) from error
    except (NotADirectoryError, IsADirectoryError) as error:
        raise Hold("Symlink or non-directory candidate root refused") from error


def _safe_name(name: str) -> bool:
    if not isinstance(name, str) or not name or len(name) > 400 or name.startswith("/") or "\\" in name or "\0" in name:
        return False
    parts = name.split("/")
    return all(part and part not in (".", "..") for part in parts) and len(parts) >= 3 and (
        name.startswith("usr/bin/") or name.startswith("usr/share/"))


def _read_archive(archive: Path, manifest: Path, pin: Pin):
    raw = _read(archive, limit=MAX_ARCHIVE)
    if hashlib.sha256(raw).hexdigest() != pin.sha256:
        raise Hold("Archive is not the explicitly approved pinned version")
    names = _read(manifest, limit=64 * 1024).decode("utf-8").splitlines()
    if len(names) != pin.count or len(set(names)) != len(names) or not all(_safe_name(n) for n in names):
        raise Hold("Invalid fixed archive inventory")
    seen, total, contents = set(), 0, {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar:
                name = member.name
                if not _safe_name(name) or not member.isfile() or name in seen or name not in names:
                    raise Hold("Unsafe/duplicate/unlisted tar entry")
                if not 0 <= member.size <= MAX_SINGLE_FILE:
                    raise Hold("Oversized tar entry")
                total += member.size
                if total > MAX_UNPACKED:
                    raise Hold("Unpacked archive exceeds bound")
                reader = tar.extractfile(member)
                if reader is None:
                    raise Hold("Missing tar data stream")
                content = reader.read(MAX_SINGLE_FILE + 1)
                if len(content) != member.size:
                    raise Hold("Truncated tar member")
                contents[name] = content
                seen.add(name)
    except (OSError, tarfile.TarError) as error:
        raise Hold("Unreadable archive") from error
    if seen != set(names):
        raise Hold("Missing archived files")
    return contents



def _store_approved_source(p: Paths, archive: Path, pin: Pin):
    """Cache only SHA-pinned source bytes. Journal never defines delete targets."""
    raw = _read(archive, limit=MAX_ARCHIVE)
    if hashlib.sha256(raw).hexdigest() != pin.sha256:
        raise Hold("Source cache failed fixed digest verification")
    source_dir = p.root / "sources"
    _dir(source_dir, create=True, private=True)
    destination = source_dir / (pin.release + ".tar.gz")
    parent = security.pinned_dir_fd(source_dir, private=True)
    try:
        try:
            fd = os.open(destination.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        except FileExistsError:
            fd = None
        if fd is not None:
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.fsync(parent)
    finally:
        os.close(parent)
    if hashlib.sha256(_read(destination, limit=MAX_ARCHIVE)).hexdigest() != pin.sha256:
        raise Hold("Approved source archive cache differs from immutable pinned digest")


def _approved_inventory(root: Path, pin: Pin):
    """Authenticates *every* deletable payload path and hash via pinned archive SHA.

    This is independent of mutable UNINSTALL.json, INDEX.json or STAGING.json.
    No same-UID-controlled manifest or owner token grants deletion authority.
    """
    archive = root / "sources" / (pin.release + ".tar.gz")
    raw = _read(archive, limit=MAX_ARCHIVE)
    if hashlib.sha256(raw).hexdigest() != pin.sha256:
        raise Hold("Trusted source archive missing or wrong SHA-256")
    seen, total, result = set(), 0, {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar:
                name = member.name
                if not _safe_name(name) or not member.isfile() or name in seen:
                    raise Hold("Unsafe pinned source inventory")
                if not 0 <= member.size <= MAX_SINGLE_FILE:
                    raise Hold("Oversized pinned inventory member")
                total += member.size
                if total > MAX_UNPACKED:
                    raise Hold("Pinned inventory exceeds unpack limit")
                stream = tar.extractfile(member)
                if stream is None:
                    raise Hold("Missing pinned inventory content")
                contents = stream.read(MAX_SINGLE_FILE + 1)
                if len(contents) != member.size:
                    raise Hold("Truncated pinned inventory content")
                result[name] = hashlib.sha256(contents).hexdigest()
                seen.add(name)
    except (OSError, tarfile.TarError) as error:
        raise Hold("Pinned archive cannot be decoded") from error
    if len(result) != pin.count:
        raise Hold("Pinned source file count differs")
    return result


def _release_index(pin: Pin, digests: dict):
    return {"version": pin.version, "source_sha256": pin.sha256, "files": digests}


def _exact_metadata(path: Path, expected: dict):
    raw = _read(path, limit=MAX_METADATA)
    if raw != _canonical(expected):
        raise Hold("Modified metadata contents; foreign bytes preserved")
    return hashlib.sha256(raw).hexdigest()


def _stage_metadata(owner: dict, pin: Pin, digests: dict):
    return {"app": APP, "token": owner["token"], "release": pin.release,
            "names": sorted(digests), "hashes": digests}


def _release_dir(root: Path, *, create=False):
    r = root / "releases"
    if _stat(r) is None and create:
        r.mkdir(mode=0o700)
    _dir(r, private=True)
    for child in r.iterdir():
        if not (RELEASE_RE.fullmatch(child.name) or STAGE_RE.fullmatch(child.name)):
            raise Hold(f"Foreign release entry: {child.name}")
        _dir(child, private=True)
    return r


def _index_for(directory: Path, pin: Pin, *, allow_staging=False):
    index = _json(directory / INDEX)
    if not isinstance(index, dict) or set(index) != {"version", "source_sha256", "files"}:
        raise Hold("Malformed release index")
    if index["version"] != pin.version or index["source_sha256"] != pin.sha256 or directory.name != pin.release:
        raise Hold("Pinned release mismatch")
    files = index["files"]
    if not isinstance(files, dict) or len(files) != pin.count or not all(
        _safe_name(k) and isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v)
        for k, v in files.items()
    ):
        raise Hold("Malformed installed file inventory")
    approved = _approved_inventory(directory.parent.parent, pin)
    if files != approved:
        raise Hold("Release index digests do not match pinned source")
    _exact_metadata(directory / INDEX, _release_index(pin, approved))
    expected = {directory / INDEX}
    if allow_staging:
        expected.add(directory / "STAGING.json")
    expected_dirs = {directory, directory / "payload"}
    for rel, digest in files.items():
        target = directory / "payload" / rel
        expected.add(target)
        expected_dirs.update(target.parents)
        if hashlib.sha256(_read(target, limit=MAX_SINGLE_FILE)).hexdigest() != digest:
            raise Hold(f"Installed file integrity mismatch: {rel}")
    for current, dirs, files_in_dir in os.walk(directory, followlinks=False):
        folder = Path(current)
        for name in dirs:
            sub = folder / name
            if sub not in expected_dirs:
                raise Hold("Unexpected installed directory")
            _dir(sub)
        for name in files_in_dir:
            target = folder / name
            if target not in expected:
                raise Hold("Unexpected installed file")
            _file(target, limit=MAX_SINGLE_FILE)
    return index


def _installed_pin(release: str) -> Pin:
    if release in APPROVED_PINS:
        return APPROVED_PINS[release]
    raise Hold("Unapproved release identifier")


def _verify_trusted_active_bytes(p: Paths, raw: bytes):
    """A recovered history pointer must match canonical approved release bytes."""
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise Hold("Malformed crash history pointer") from exc
    if (not isinstance(value, dict) or set(value) != {"active", "previous"}
            or _canonical(value) != raw):
        raise Hold("Untrusted/noncanonical ACTIVE history bytes")
    for release in value.values():
        if release is not None:
            pin = _installed_pin(release)
            _index_for(_release_dir(p.root) / release, pin)
    return value




def _pointer(p: Paths):
    # A crash midway through history->ACTIVE publication MUST NOT be reported
    # as 'not installed'. Only explicit, validated recover can reconcile it.
    if _stat(p.root / POINTER_TXN) is not None:
        raise Hold("Pending ACTIVE pointer transaction; run recover")
    if _stat(p.root / security.ACTIVE_INTENT) is not None:
        raise Hold("Pending ACTIVE crash-intent publication; run recover")
    path = p.root / POINTER
    if _stat(path) is None:
        releases = p.root / "releases"
        if (_stat(releases) is not None and any(
                RELEASE_RE.fullmatch(name) for name in os.listdir(releases))):
            raise Hold("ACTIVE pointer missing while approved releases exist; run recover")
        # Both naming families must be detected: `.history-pointer-*` (PR #14
        # ledger) and `.history-*` (PR #15 durable crash-intent publication).
        # Otherwise a legacy orphan history with no approved releases would be
        # reported as 'not installed' (Agent 02's P2-SAT15-02).
        if any(re.fullmatch(r"\.history-pointer-[0-9a-f]{32}|\.history-[0-9a-f]{32}", name)
               for name in os.listdir(p.root)):
            raise Hold("Unverified orphan pointer history; manual review HOLD")
        return {"active": None, "previous": None}
    value = _json(path)
    if not isinstance(value, dict) or set(value) != {"active", "previous"}:
        raise Hold("Invalid active pointer")
    for release in value.values():
        if release is not None and (not isinstance(release, str) or not RELEASE_RE.fullmatch(release)):
            raise Hold("Invalid release pointer")
    return value


def _valid_pointer_releases(p: Paths, value: dict, *, require_active=False):
    if not isinstance(value, dict) or set(value) != {"active", "previous"}:
        raise Hold("Invalid pointer transaction state")
    if require_active and value["active"] is None:
        raise Hold("Pointer transaction cannot target an empty active release")
    if value["active"] is None and value["previous"] is not None:
        raise Hold("Pointer transition contains impossible empty-active state")
    for release in value.values():
        if release is None:
            continue
        pin = _installed_pin(release)
        _index_for(_release_dir(p.root) / release, pin)


def _write_pinned_fd(fd: int, name: str, data: bytes):
    """Complete O_EXCL durable record before any original pointer rename.

    No cleanup on I/O error: incomplete, uniquely named files are retained for
    explicit manual review. Never unlink names writable by the same UID.
    """
    try:
        handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
    except (OSError, security.RaceHold) as error:
        raise Hold("Pointer transaction persistence failed; preserving ACTIVE") from error


def _publish_pointer(p: Paths, owner: dict, session, old: dict, new: dict,
                     *, operation: str):
    """Two-phase durable transition, preserving old bytes even on SIGKILL.

    Old ACTIVE is moved only AFTER the exact intent and candidate are fsynced.
    A private bounded vault preflight prevents predictable cleanup exhaustion.
    """
    if operation not in ("install", "rollback"):
        raise Hold("Unsupported pointer transition")
    # The *same locked first-install transaction* legitimately stages a
    # source-pinned release before its first ACTIVE name is created.
    # Fresh invocations still go through _pointer() before any staging, so
    # a pre-existing release with a missing pointer remains HOLD.
    if _stat(p.root / POINTER) is None and old == {"active": None, "previous": None}:
        if _stat(p.root / POINTER_TXN) is not None:
            raise Hold("Pending pointer transaction; run recover first")
        if _stat(p.root / security.ACTIVE_INTENT) is not None:
            raise Hold("Pending ACTIVE crash-intent; run recover first")
    elif _pointer(p) != old:
        raise Hold("ACTIVE changed before pointer transaction")
    if new["previous"] != old["active"]:
        raise Hold("Pointer transition must preserve former active release")
    if operation == "rollback" and (
            old["previous"] is None or new["active"] != old["previous"]):
        raise Hold("Rollback pointer transition not approved")
    if new["active"] == old["active"]:
        raise Hold("No-op pointer transition must not create journal")
    _valid_pointer_releases(p, old)
    _valid_pointer_releases(p, new, require_active=True)
    fd = session.root_fd
    session.check()
    # Avoid a transaction that cannot later retain its intent on recovery.
    try:
        vault = security._vault_fd(fd)
        os.close(vault)
    except (security.RaceHold, OSError) as error:
        raise Hold("Preservation vault unavailable/full; pointer unchanged") from error
    token = uuid.uuid4().hex
    before_bytes, after_bytes = _canonical(old), _canonical(new)
    existing = _stat(p.root / POINTER)
    was_present = existing is not None
    if was_present:
        try:
            observed, _ = security._read_owned(fd, POINTER, max_bytes=MAX_METADATA)
        except (OSError, security.RaceHold) as error:
            raise Hold("Unable to attest existing ACTIVE before pointer update") from error
        if observed != before_bytes:
            raise Hold("Noncanonical or foreign ACTIVE; pointer unchanged")
    elif old != {"active": None, "previous": None}:
        raise Hold("Missing old ACTIVE; cannot authorize transaction")
    intent = {
        "schema": 1, "app": APP, "uid": os.getuid(),
        "owner_token": owner["token"], "tx": token, "operation": operation,
        "old_present": was_present, "old": old, "new": new,
        "old_digest": hashlib.sha256(before_bytes).hexdigest(),
        "new_digest": hashlib.sha256(after_bytes).hexdigest(),
        "history": ".history-pointer-" + token,
        "candidate": ".write-pointer-" + token,
    }
    _write_pinned_fd(fd, intent["candidate"], after_bytes)
    _write_pinned_fd(fd, POINTER_TXN, _canonical(intent))
    try:
        if was_present:
            security._noreplace(fd, POINTER, fd, intent["history"])
            os.fsync(fd)
            held, _ = security._read_owned(fd, intent["history"], max_bytes=MAX_METADATA)
            if held != before_bytes:
                raise Hold("ACTIVE was substituted during history rename; HOLD")
        security._noreplace(fd, intent["candidate"], fd, POINTER)
        os.fsync(fd)
        active, _ = security._read_owned(fd, POINTER, max_bytes=MAX_METADATA)
        if active != after_bytes:
            raise Hold("Foreign bytes appeared at ACTIVE publication; HOLD")
        session.check()
        security.unregister_exact(p.root / POINTER_TXN, _canonical(intent))
        return new
    except (OSError, security.RaceHold) as error:
        raise Hold(f"Pointer publication interrupted: {error}; run recover (no forced overwrite)") from error


def _recover_pointer(p: Paths, owner: dict, session) -> int:
    """Recover only an exactly matched, source-validated in-flight pointer tx.

    A same-UID-controlled history filename or OWNER token alone is NOT proof.
    Foreign/malformed metadata is retained, never repaired blindly. Recovered
    'old' means aborting an incomplete update; 'new' means commit had completed.
    """
    fd = session.root_fd
    path = p.root / POINTER_TXN
    if _stat(path) is None:
        if _stat(p.root / POINTER) is None:
            _pointer(p)  # Detect orphaned history or installed release.
        return 0
    try:
        raw, _ = security._read_owned(fd, POINTER_TXN, max_bytes=MAX_METADATA)
        tx = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, OSError, security.RaceHold) as error:
        raise Hold("Unverified pointer transaction intent; HOLD") from error
    keys = {"schema", "app", "uid", "owner_token", "tx", "operation",
            "old_present", "old", "new", "old_digest", "new_digest",
            "history", "candidate"}
    if (not isinstance(tx, dict) or set(tx) != keys or
            tx["schema"] != 1 or tx["app"] != APP or tx["uid"] != os.getuid() or
            tx["owner_token"] != owner["token"] or
            not isinstance(tx["tx"], str) or
            not re.fullmatch("[0-9a-f]{32}", tx["tx"]) or
            tx["history"] != ".history-pointer-" + tx["tx"] or
            tx["candidate"] != ".write-pointer-" + tx["tx"] or
            type(tx["old_present"]) is not bool or
            tx["operation"] not in ("install", "rollback") or
            raw != _canonical(tx)):
        raise Hold("Forged or noncanonical pointer transaction intent; HOLD")
    prior, proposed = tx["old"], tx["new"]
    old_bytes, new_bytes = _canonical(prior), _canonical(proposed)
    if (hashlib.sha256(old_bytes).hexdigest() != tx["old_digest"] or
            hashlib.sha256(new_bytes).hexdigest() != tx["new_digest"] or
            proposed["previous"] != prior["active"] or
            proposed["active"] == prior["active"] or
            (tx["operation"] == "rollback" and (
                prior["previous"] is None or proposed["active"] != prior["previous"])) or
            (not tx["old_present"] and prior != {"active": None, "previous": None})):
        raise Hold("Inconsistent pointer transition metadata; HOLD")
    # The approved source archive, release index, and every installed file
    # must match its fixed pin before any history-to-ACTIVE restoration.
    _valid_pointer_releases(p, prior)
    _valid_pointer_releases(p, proposed, require_active=True)
    def read_maybe(name):
        try:
            content, _ = security._read_owned(fd, name, max_bytes=MAX_METADATA)
            return content
        except FileNotFoundError:
            return None
        except (OSError, security.RaceHold) as error:
            raise Hold("Unsafe pointer transaction file; HOLD") from error
    history = read_maybe(tx["history"])
    candidate = read_maybe(tx["candidate"])
    active = read_maybe(POINTER)
    if history is not None and history != old_bytes:
        raise Hold("Foreign history bytes; no automatic restoration")
    if candidate is not None and candidate != new_bytes:
        raise Hold("Foreign candidate bytes; no automatic publication")
    if not tx["old_present"] and history is not None:
        raise Hold("Unexpected history for first install; HOLD")
    if tx["old_present"] and history is None and active != old_bytes:
        raise Hold("Missing verified old pointer history; HOLD")
    if active is not None and active not in (old_bytes, new_bytes):
        raise Hold("Foreign ACTIVE bytes during recovery; HOLD")
    session.check()
    try:
        if active is None:
            if tx["old_present"]:
                if history != old_bytes:
                    raise Hold("Unverified pointer history; refusing restore")
                # Never rename an attacker-writable history PATH into canonical
                # ACTIVE: rebuild the already-authenticated old bytes in a fresh
                # O_EXCL file and attest the result (policy of recover_active_pointer).
                rebuilt = ".write-pointer-" + uuid.uuid4().hex
                _write_pinned_fd(fd, rebuilt, old_bytes)
                security._noreplace(fd, rebuilt, fd, POINTER)
                restored = old_bytes
            elif candidate == new_bytes:
                security._noreplace(fd, tx["candidate"], fd, POINTER)
                restored = new_bytes
            else:
                raise Hold("Unverified first-install pointer candidate; HOLD")
            os.fsync(fd)
            active = read_maybe(POINTER)
            if active != restored:
                raise Hold("Unexpected ACTIVE during crash recovery; HOLD")
        # After power loss: if old still present, abort pending update;
        # if new is present, leave committed update as-is.
        if active == old_bytes and tx["old_present"]:
            _valid_pointer_releases(p, prior)
        elif active == new_bytes:
            _valid_pointer_releases(p, proposed, require_active=True)
        else:
            raise Hold("Pointer recovery phase could not be verified")
        session.check()
        security.unregister_exact(p.root / POINTER_TXN, raw)
        return 1
    except (security.RaceHold, OSError) as error:
        raise Hold(f"Pointer recovery blocked: {error}; original and foreign bytes retained") from error


def _script(p: Paths):
    script = Path(__file__)
    source = _read(script, limit=128 * 1024)
    dest = p.root / "manager.py"
    if _stat(dest) is not None and _read(dest, limit=128 * 1024) != source:
        raise Hold("Installed manager differs; manual audit required")
    return source


def _wrapper(p: Paths) -> bytes:
    import shlex
    return ("#!/bin/sh\nset -eu\nexec /usr/bin/python3 " +
            shlex.quote(str((p.canonical_root or p.root) / "manager.py")) + ' launch "$@"\n').encode()


def _desktop(p: Paths, pin: Pin) -> bytes:
    # A stable themed icon makes pointer rollback a SINGLE atomic metadata write.
    # It avoids any version-coupled desktop file update during switch/recovery.
    icon = "applications-science"
    if '"' in str(p.launcher) or "\\" in str(p.launcher) or " " in str(p.launcher):
        raise Hold("Desktop Exec path contains characters requiring unsupported escaping")
    return (f"[Desktop Entry]\nType=Application\nName=TIHULU Satellite Center (Stable-first Candidate)\n"
            f"Comment=Private isolated native Satellite Center candidate\n"
            f"Exec={p.launcher} desktop\nIcon={icon}\n"
            f"Terminal=false\nStartupNotify=true\nCategories=Science;Education;\n").encode()


def _check_registration(p: Paths, pin: Pin, previous=None):
    _dir(p.bin_dir, create=True)
    _dir(p.desktop_dir, create=True)
    allowed_desktop = {_desktop(p, pin)}
    if previous is not None:
        allowed_desktop.add(_desktop(p, _installed_pin(previous)))
    for path, choices in ((p.launcher, {_wrapper(p)}), (p.desktop, allowed_desktop)):
        if _stat(path) is not None and _read(path, limit=8192) not in choices:
            raise Hold(f"Foreign launcher/desktop entry cannot be overwritten: {path}")


def _extract(root: Path, contents: dict, pin: Pin, owner: dict):
    """Create every payload ancestor relative to pinned directory descriptors.

    No multi-component path is used for mkdir/open/write even if a hostile
    same-UID process swaps an intermediate directory immediately after a
    successful preflight. A failed swap causes HOLD without foreign writes.
    """
    releases = _release_dir(root, create=True)
    final = releases / pin.release
    if _stat(final) is not None:
        _index_for(final, pin)
        return
    stage = releases / (".staging-" + uuid.uuid4().hex)
    hashes = {}
    releases_fd = security.pinned_dir_fd(releases, private=True)
    try:
        os.mkdir(stage.name, mode=0o700, dir_fd=releases_fd)
        stage_fd = os.open(stage.name, security.O_DIR, dir_fd=releases_fd)
        try:
            security._safe_dir_stat(os.fstat(stage_fd), private=True)
            os.fsync(releases_fd)
            _jsonwrite(stage / "STAGING.json", _stage_metadata(
                owner, pin, {n: hashlib.sha256(data).hexdigest() for n, data in contents.items()}))
            os.mkdir("payload", mode=0o700, dir_fd=stage_fd)
            payload_fd = os.open("payload", security.O_DIR, dir_fd=stage_fd)
            try:
                security._safe_dir_stat(os.fstat(payload_fd), private=True)
                for name, content in contents.items():
                    if not _safe_name(name):
                        raise Hold("Unsafe extraction entry")
                    components = name.split("/")
                    current = payload_fd
                    opened = []
                    try:
                        for component in components[:-1]:
                            try:
                                os.mkdir(component, mode=0o700, dir_fd=current)
                                os.fsync(current)
                            except FileExistsError:
                                pass
                            next_fd = os.open(component, security.O_DIR, dir_fd=current)
                            security._safe_dir_stat(os.fstat(next_fd), private=True)
                            opened.append(next_fd)
                            current = next_fd
                        fd = os.open(components[-1], os.O_WRONLY | os.O_CREAT |
                                     os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                     0o600, dir_fd=current)
                        with os.fdopen(fd, "wb") as out:
                            out.write(content)
                            out.flush()
                            os.fsync(out.fileno())
                            if name.startswith("usr/bin/"):
                                os.fchmod(out.fileno(), 0o700)
                        os.fsync(current)
                        hashes[name] = hashlib.sha256(content).hexdigest()
                    finally:
                        for item in reversed(opened):
                            os.close(item)
                os.fsync(payload_fd)
            finally:
                os.close(payload_fd)
            _jsonwrite(stage / INDEX, _release_index(pin, hashes))
            os.fsync(stage_fd)
        finally:
            os.close(stage_fd)
        security._noreplace(releases_fd, stage.name, releases_fd, final.name)
        os.fsync(releases_fd)
        root_parts = root.parts
        if not (len(root_parts) == 5 and root_parts[:4] ==
                ("/", "proc", "self", "fd") and root_parts[4].isdigit()):
            raise Hold("Unpinned candidate root during release promotion")
        root_fd = int(root_parts[4])
        verified_marker = _exact_metadata(final / "STAGING.json",
                        _stage_metadata(owner, pin, hashes))
        with security.open_nested_dir(releases_fd, [final.name]) as final_fd:
            vault = security._vault_fd(root_fd)
            try:
                security.remove_known_file(final_fd, "STAGING.json",
                                           verified_marker, vault_fd=vault)
            except security.RaceHold as error:
                raise Hold(str(error)) from error
            finally:
                os.close(vault)
        _syncdir(final)
        _index_for(final, pin)
    finally:
        os.close(releases_fd)


def _write_registration(p: Paths, pin: Pin):
    # Existing identical launcher is reused; a swapped foreign filename gets
    # EEXIST and is never overwritten.
    try:
        security.register_no_clobber(p.launcher, _wrapper(p), mode=0o700)
        security.register_no_clobber(p.desktop, _desktop(p, pin))
    except security.RaceHold as error:
        raise Hold(str(error)) from error


def _deploy(p: Paths, archive: Path, manifest: Path, pin: Pin):
    contents = _read_archive(archive, manifest, pin)
    with _locked(p) as (owner, session):
        p = session.p
        session.check()
        if _stat(p.root / UNINSTALL_JOURNAL) is not None:
            raise Hold("Interrupted uninstall; retry uninstall before installing")
        prior = _pointer(p)
        if prior["active"] is not None:
            _index_for(_release_dir(p.root) / prior["active"], _installed_pin(prior["active"]))
        _check_registration(p, pin, prior["active"])
        manager = _script(p)
        companion = _read(Path(__file__).with_name("race_hardening.py"), limit=128 * 1024)
        companion_path = p.root / "race_hardening.py"
        if _stat(companion_path) is not None and _read(companion_path, limit=128 * 1024) != companion:
            raise Hold("Installed security module differs; manual audit required")
        session.check()
        _store_approved_source(p, archive, pin)
        _extract(p.root, contents, pin, owner)
        if _stat(companion_path) is None:
            _atomic(companion_path, companion)
        if _stat(p.root / "manager.py") is None:
            _atomic(p.root / "manager.py", manager, mode=0o600)
        session.check()
        # Publish native entry points only after fully staged/verified payload.
        _write_registration(p, pin)
        if prior["active"] != pin.release:
            _publish_pointer(p, owner, session, prior,
                             {"active": pin.release, "previous": prior["active"]},
                             operation="install")
        return {"active": pin.release, "previous": prior["active"]}


def _recover(p: Paths):
    with _locked(p) as (owner, session):
        p = session.p
        session.check()
        # An HMAC-authenticated ACTIVE crash-intent is reconciled first: it is
        # the durable pre-rename record for a canonical pointer publication.
        try:
            security.recover_active_pointer(
                session.root_fd, pinned_sha=PIN_SHA,
                validate=lambda raw: _verify_trusted_active_bytes(p, raw))
        except security.RaceHold as error:
            raise Hold(str(error)) from error
        recovered_pointer = _recover_pointer(p, owner, session)
        # A SIGKILL inside remove_known_file can strand its deterministic
        # quarantine tombstone in the root. Retain it in the vault so recover
        # completes instead of HOLDing on our own transient name forever. A full
        # vault is the same explicit HOLD as every other preservation path.
        try:
            retained_tombstones = security.retain_quarantine_tombstones(session.root_fd)
        except security.RaceHold as error:
            raise Hold(str(error)) from error
        releases = _release_dir(p.root, create=True)
        if _stat(p.root / UNINSTALL_JOURNAL) is not None:
            raise Hold("Interrupted uninstall; retry uninstall instead")
        found = recovered_pointer + retained_tombstones
        for promoted in list(releases.iterdir()):
            if not RELEASE_RE.fullmatch(promoted.name) or _stat(promoted / "STAGING.json") is None:
                continue
            metadata = _json(promoted / "STAGING.json")
            pin = _installed_pin(promoted.name)
            if not isinstance(metadata, dict) or metadata.get("app") != APP or metadata.get("token") != owner["token"] or metadata.get("release") != pin.release:
                raise Hold("Foreign promoted staging marker")
            index = _index_for(promoted, pin, allow_staging=True)
            if sorted(index["files"]) != metadata.get("names"):
                raise Hold("Promoted staging manifest mismatch")
            approved = _approved_inventory(p.root, pin)
            expected_stage = _stage_metadata(owner, pin, approved)
            verified_digest = _exact_metadata(promoted / "STAGING.json", expected_stage)
            try:
                with security.open_nested_dir(session.root_fd, ["releases", promoted.name]) as directory_fd:
                    vault = security._vault_fd(session.root_fd)
                    try:
                        security.remove_known_file(directory_fd, "STAGING.json",
                                                   verified_digest, vault_fd=vault)
                    finally:
                        os.close(vault)
            except security.RaceHold as error:
                raise Hold(str(error)) from error
            _syncdir(promoted)
            found += 1
        for stage in list(releases.iterdir()):
            if not STAGE_RE.fullmatch(stage.name):
                continue
            _dir(stage, private=True)
            metadata = _json(stage / "STAGING.json")
            if not isinstance(metadata, dict) or set(metadata) != {"app", "token", "release", "names", "hashes"}:
                raise Hold("Unknown staging ownership: manual review")
            if metadata["app"] != APP or metadata["token"] != owner["token"] or metadata["release"] != PRODUCTION_PIN.release:
                raise Hold("Foreign or unsupported staging marker")
            names = metadata["names"]
            if not isinstance(names, list) or len(names) != PRODUCTION_PIN.count or len(names) != len(set(names)) or not all(_safe_name(n) for n in names):
                raise Hold("Unsafe interrupted staging manifest")
            pin = _installed_pin(metadata["release"])
            approved = _approved_inventory(p.root, pin)
            hashes = metadata["hashes"]
            if hashes != approved or names != sorted(approved):
                raise Hold("Untrusted staging marker not matched to pinned payload")
            stage_metadata_sha = _exact_metadata(stage / "STAGING.json",
                                                 _stage_metadata(owner, pin, approved))
            approved_index_sha = hashlib.sha256(
                _canonical(_release_index(pin, approved))).hexdigest()
            if _stat(stage / INDEX) is not None:
                _exact_metadata(stage / INDEX, _release_index(pin, approved))
            expected = {stage / "STAGING.json", stage / INDEX}
            dirs_expected = {stage, stage / "payload"}
            for name in names:
                target = stage / "payload" / name
                expected.add(target)
                dirs_expected.update(target.parents)
            for current, dirs, files in os.walk(stage, followlinks=False):
                for name in dirs:
                    d = Path(current) / name
                    if d not in dirs_expected:
                        raise Hold("Foreign interrupted staging directory")
                    _dir(d)
                for name in files:
                    file = Path(current) / name
                    if file not in expected:
                        raise Hold("Foreign interrupted staging file")
                    _file(file, limit=MAX_SINGLE_FILE)
            # No broad recursive deletion: preserve a late injected foreign file.
            session.check()
            try:
                known = {"payload/" + name: digest for name, digest in approved.items()}
                known.update({"STAGING.json": stage_metadata_sha,
                              "INDEX.json": approved_index_sha})
                security.remove_known_tree(session.root_fd, stage.name, known)
            except security.RaceHold as error:
                raise Hold(str(error)) from error
            found += 1
        if found:
            _syncdir(releases)
        return found


def _rollback(p: Paths):
    with _locked(p) as (owner, session):
        p = session.p
        session.check()
        if _stat(p.root / UNINSTALL_JOURNAL) is not None:
            raise Hold("Interrupted uninstall; retry uninstall instead")
        current = _pointer(p)
        old = current["previous"]
        if old is None:
            raise Hold("No previous approved release is available for rollback")
        _index_for(_release_dir(p.root) / old, _installed_pin(old))
        _check_registration(p, _installed_pin(old), current["active"])
        # Desktop is version-independent; rollback is one durable pointer write.
        _publish_pointer(p, owner, session, current,
                         {"active": old, "previous": current["active"]},
                         operation="rollback")
        return old


def _verify_partial_for_removal(directory: Path, files: dict):
    """Resume a journaled removal without assuming the index still exists.

    Only known original paths are removed. Unknown/mutated files or symlinks
    stop cleanup; no broad cleanup of arbitrary foreign content.
    """
    expected = {directory / "payload" / name: digest for name, digest in files.items()}
    pin = _installed_pin(directory.name)
    approved = _approved_inventory(directory.parent.parent, pin)
    if files != approved:
        raise Hold("Journal inventory not authorized by pinned source")
    expected[directory / INDEX] = hashlib.sha256(
        _canonical(_release_index(pin, approved))).hexdigest()
    dirs_expected = {directory, directory / "payload"}
    for path in expected:
        dirs_expected.update(path.parents)
    for current, dirs, filenames in os.walk(directory, followlinks=False):
        for name in dirs:
            path = Path(current) / name
            if path not in dirs_expected:
                raise Hold("Foreign directory in interrupted uninstall")
            _dir(path)
        for name in filenames:
            path = Path(current) / name
            if path not in expected:
                raise Hold("Foreign file in interrupted uninstall")
            data = _read(path, limit=MAX_SINGLE_FILE)
            digest = expected[path]
            if digest is not None and hashlib.sha256(data).hexdigest() != digest:
                raise Hold("Mutated file in interrupted uninstall")


def _uninstall(p: Paths):
    with _locked(p) as (owner, session):
        p = session.p
        session.check()
        releases = _release_dir(p.root, create=True)
        journal_path = p.root / UNINSTALL_JOURNAL
        if _stat(journal_path) is None:
            pointer = _pointer(p)
            inventory = {}
            for item in releases.iterdir():
                if STAGE_RE.fullmatch(item.name):
                    raise Hold("Incomplete staging; run recover first")
                data = _index_for(item, _installed_pin(item.name))
                inventory[item.name] = data["files"]
            _jsonwrite(journal_path, {
                "app": APP, "token": owner["token"], "inventory": inventory,
                "previous_active": pointer["active"]})
        journal = _json(journal_path)
        if not isinstance(journal, dict) or set(journal) != {
            "app", "token", "inventory", "previous_active"
        } or journal["app"] != APP or journal["token"] != owner["token"]:
            raise Hold("Invalid uninstall journal")
        inventory = journal["inventory"]
        if not isinstance(inventory, dict):
            raise Hold("Invalid journal inventory")
        for release, files in inventory.items():
            pin = _installed_pin(release)
            trusted = _approved_inventory(p.root, pin)
            if files != trusted:
                raise Hold("Forged uninstall journal: inventory differs from SHA-pinned source")
        for entry in releases.iterdir():
            if entry.name not in inventory:
                raise Hold("Unexpected release during interrupted uninstall")
            _verify_partial_for_removal(entry, inventory[entry.name])
        if _stat(p.launcher) is not None and _read(p.launcher, limit=8192) != _wrapper(p):
            raise Hold("Refuse foreign modified launcher")
        if _stat(p.desktop) is not None:
            if _read(p.desktop, limit=8192) not in [_desktop(p, pin) for pin in APPROVED_PINS.values()]:
                raise Hold("Refuse foreign modified desktop entry")
        for entry, expected in ((p.launcher, _wrapper(p)),
                                (p.desktop, _desktop(p, PRODUCTION_PIN))):
            try:
                security.unregister_exact(entry, expected)
            except security.RaceHold as error:
                raise Hold(str(error)) from error
        session.check()
        if _stat(p.root / POINTER) is not None:
            pointer_doc = _pointer(p)
            expected_pointer = _canonical(pointer_doc)
            if _read(p.root / POINTER) != expected_pointer:
                raise Hold("Unexpected ACTIVE metadata; preservation HOLD")
            try:
                security.unregister_exact(p.root / POINTER, expected_pointer)
            except security.RaceHold as error:
                raise Hold(str(error)) from error
        for item in list(releases.iterdir()):
            session.check()
            try:
                pin = _installed_pin(item.name)
                known = {"payload/" + rel: digest for rel, digest
                         in _approved_inventory(p.root, pin).items()}
                known["INDEX.json"] = hashlib.sha256(
                    _canonical(_release_index(pin,
                      _approved_inventory(p.root, pin)))).hexdigest()
                security.remove_known_tree(session.root_fd, item.name, known)
            except security.RaceHold as error:
                raise Hold(str(error)) from error
        _syncdir(releases)
        try:
            security.unregister_exact(journal_path, _canonical(journal))
        except security.RaceHold as error:
            raise Hold(str(error)) from error
        _syncdir(p.root)
        # Logical uninstall moves attested bytes into bounded preservation vaults.
        # Physical reclamation is intentionally HOLD under a hostile same-UID
        # threat; never imply retained bytes were destroyed or disk freed.
        return {"removed_releases": True, "previous_active": journal["previous_active"],
                "runtime_preserved": True, "physical_deletion": "HOLD",
                "payload_bytes_retained": True,
                "note": "Candidate namespaces removed; retained vault files need manual review"}


def _launch(p: Paths, args):
    with _locked(p, shared=True) as (_, session):
        p = session.p
        session.check()
        if _stat(p.root / UNINSTALL_JOURNAL) is not None:
            raise Hold("Interrupted uninstall; launch refused")
        pointer = _pointer(p)
        release = pointer["active"]
        if release is None:
            raise Hold("No active candidate release")
        _index_for(_release_dir(p.root) / release, _installed_pin(release))
        payload = p.root / "releases" / release / "payload"
        python_package = payload / ENTRY
        _dir(p.runtime_data, create=True)
        _dir(p.runtime_home, create=True)
        for name in ("config", "cache", "data", "state"):
            _dir(p.runtime_home / name, create=True)
        env = dict(os.environ)
        # Never inherit interpreter/library injection vectors into the child.
        # PYTHONPATH is overwritten below with the pinned payload only.
        for hostile in ("LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONHOME",
                        "PYTHONSTARTUP", "PYTHONWARNINGS", "PYTHONPYCACHEPREFIX"):
            env.pop(hostile, None)
        env["HOME"] = str(p.runtime_home)
        env["XDG_CONFIG_HOME"] = str(p.runtime_home / "config")
        env["XDG_CACHE_HOME"] = str(p.runtime_home / "cache")
        env["XDG_DATA_HOME"] = str(p.runtime_home / "data")
        env["XDG_STATE_HOME"] = str(p.runtime_home / "state")
        env["TIHULU_SATELLITE_DATA"] = str(p.runtime_data)
        env["PYTHONPATH"] = str(python_package)
        env["PYTHONNOUSERSITE"] = "1"
        # The pinned release payload must stay byte-identical to its approved
        # source inventory. CPython writes __pycache__/*.pyc next to the imported
        # modules by default, which would make every later index check HOLD on
        # "Unexpected installed directory". Refuse to write bytecode instead.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if env.get("WAYLAND_DISPLAY") and not env.get("QT_QPA_PLATFORM"):
            env["QT_QPA_PLATFORM"] = "wayland;xcb"
        # Re-attest the installed management modules against this installer's own
        # bytes: a same-UID swap between install and launch must not become code
        # execution at every start.
        manager = _script(p)
        companion = _read(Path(__file__).with_name("race_hardening.py"), limit=128 * 1024)
        companion_path = p.root / "race_hardening.py"
        if _stat(companion_path) is not None and _read(companion_path, limit=128 * 1024) != companion:
            raise Hold("Installed security module differs; manual audit required")
        if _stat(p.root / "manager.py") is not None and _read(p.root / "manager.py", limit=128 * 1024) != manager:
            raise Hold("Installed manager differs; launch refused")
        session.check()
        # Keep a shared lock across exec to prevent removing payload during use.
        session.hold_for_exec()
        # `-P` keeps the current working directory out of sys.path, so a hostile
        # `satellite_center` package in the invoking directory cannot shadow the
        # pinned payload (PYTHONPATH is the only import root).
        os.execve("/usr/bin/python3",
                  ["/usr/bin/python3", "-P", "-m", "satellite_center"] +
                  (args or ["desktop"]), env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "update", "status", "doctor",
                                             "rollback", "recover", "uninstall", "launch"))
    parser.add_argument("app_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        p = paths()
        if args.command in ("install", "update"):
            s = argparse.ArgumentParser(prog=APP + " " + args.command)
            s.add_argument("--archive", type=Path, required=True)
            s.add_argument("--manifest", type=Path, required=True)
            inputs = s.parse_args(args.app_args)
            result = _deploy(p, inputs.archive, inputs.manifest, PRODUCTION_PIN)
        elif args.command == "recover":
            result = {"recovered_staging_directories": _recover(p)}
        elif args.command == "uninstall":
            result = _uninstall(p)
        elif args.command == "rollback":
            result = {"active": _rollback(p)}
        elif args.command == "launch":
            _launch(p, args.app_args)
            raise AssertionError("execve returned")
        elif args.command == "status":
            if _stat(p.root) is None:
                result = {"active": None, "previous": None, "installed": False}
            else:
                with _locked(p, shared=True) as (_, session):
                    p = session.p
                    session.check()
                    if _stat(p.root / UNINSTALL_JOURNAL) is not None:
                        raise Hold("Interrupted uninstall; status unavailable until retry")
                    value = _pointer(p)
                    if value["active"] is not None:
                        _index_for(_release_dir(p.root) / value["active"],
                                   _installed_pin(value["active"]))
                    result = {**value, "installed": value["active"] is not None}
        elif args.command == "doctor":
            import importlib.util
            qt = importlib.util.find_spec("PyQt5") is not None
            result = {"python3": sys.version.split()[0],
                      "pyqt5_available": qt,
                      "qtwebengine_available": qt and importlib.util.find_spec("PyQt5.QtWebEngineWidgets") is not None,
                      "numpy_available": importlib.util.find_spec("numpy") is not None,
                      "skyfield_available": importlib.util.find_spec("skyfield") is not None,
                      "rtl_sdr_binary_available": shutil.which("rtl_sdr") is not None,
                      "xdg_open_available": shutil.which("xdg-open") is not None,
                      "note": "Read-only dependency presence, never opens SDR/GUI; Python search path is the invoking interpreter"}
        else:
            raise Hold("Unsupported command")
    except (Hold, OSError, UnicodeError, ValueError, tarfile.TarError) as error:
        print("HOLD: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
