"""Linux-only descriptor-pinned, fail-closed filesystem helpers for a private candidate.

Do not mistake same-UID hostility for a fully isolated trust boundary: a process
with the same UID can ultimately rewrite its own HOME or injected Python code.
These helpers defend against known, deterministic path swaps/foreign additions
and never recurse-delete unlisted files. Independent security signoff is HOLD.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import uuid

O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
O_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)
_RENAMEAT2 = getattr(_LIBC, "renameat2", None)
if _RENAMEAT2 is not None:
    _RENAMEAT2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
    _RENAMEAT2.restype = ctypes.c_int


class RaceHold(RuntimeError):
    pass


def _identity(st):
    return st.st_dev, st.st_ino


def _safe_dir_stat(st, *, private=False):
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RaceHold("Foreign owner, symlink, or non-directory in candidate tree")
    if st.st_mode & (0o077 if private else 0o022):
        raise RaceHold("Unsafe candidate directory mode")


def _safe_file_fd(fd, *, max_bytes=16 * 1024 * 1024):
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1 or st.st_size > max_bytes:
        raise RaceHold("Foreign/special/multilink/oversized file")
    return st


def _flock(fd, shared=False):
    try:
        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except BlockingIOError as e:
        raise RaceHold("Another installer/application holds the candidate directory lock") from e


def _same_dir_entry(parent_fd, name, fd):
    found = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if _identity(found) != _identity(os.fstat(fd)) or not stat.S_ISDIR(found.st_mode):
        raise RaceHold("Directory inode replaced during transaction")


class PinnedRoot:
    """Hold shared parent/root inode locks, plus legacy .lock inode, all via dirfd."""

    def __init__(self, base: Path, name: str, *, shared=False):
        self.base = Path(base)
        self.name = name
        self.shared = shared
        self.base_fd = self.root_fd = self.lock_fd = None

    def __enter__(self):
        try:
            # The shared parent (~/.local/share) is NOT part of the candidate
            # tree: reject symlink/foreign-owner/type swaps via pinned_dir_fd,
            # but never its group-write bit, which is standard under umask 0002.
            self.base_fd = pinned_dir_fd(self.base)
            _flock(self.base_fd, self.shared)
            # Root creation occurs underneath a pinned parent, not a pathname.
            try:
                os.mkdir(self.name, 0o700, dir_fd=self.base_fd)
            except FileExistsError:
                pass
            self.root_fd = os.open(self.name, O_DIR, dir_fd=self.base_fd)
            _safe_dir_stat(os.fstat(self.root_fd), private=True)
            _flock(self.root_fd, self.shared)
            self.lock_fd = os.open(".lock",
                                   os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                                   0o600, dir_fd=self.root_fd)
            st = _safe_file_fd(self.lock_fd, max_bytes=1024)
            if st.st_mode & 0o077:
                raise RaceHold("Candidate lock is not private")
            _flock(self.lock_fd, self.shared)
            self.check()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    @property
    def alias(self):
        # The /proc/self/fd path dereferences the ALREADY OPEN directory inode,
        # not any rename/symlink to the original HOME path.
        return Path(f"/proc/self/fd/{self.root_fd}")

    def check(self):
        if self.root_fd is None or self.base_fd is None:
            raise RaceHold("Unpinned candidate root")
        # Parent path itself may be renamed by a same-UID process.
        b = os.stat(self.base, follow_symlinks=False)
        if not stat.S_ISDIR(b.st_mode) or _identity(b) != _identity(os.fstat(self.base_fd)):
            raise RaceHold("Parent candidate directory replaced after lock")
        _same_dir_entry(self.base_fd, self.name, self.root_fd)
        lk = os.stat(".lock", dir_fd=self.root_fd, follow_symlinks=False)
        if not stat.S_ISREG(lk.st_mode) or _identity(lk) != _identity(os.fstat(self.lock_fd)):
            raise RaceHold("Candidate lock inode replaced after flock")
        _safe_file_fd(self.lock_fd, max_bytes=1024)

    def hold_for_exec(self):
        self.check()
        for fd in (self.base_fd, self.root_fd, self.lock_fd):
            os.set_inheritable(fd, True)

    def __exit__(self, exc_type, exc, tb):
        for fd in (self.lock_fd, self.root_fd, self.base_fd):
            if fd is not None:
                os.close(fd)
        self.lock_fd = self.root_fd = self.base_fd = None


def pinned_dir_fd(path: Path, *, create=False, private=False,
                  strict=None) -> int:
    """Pin EVERY absolute ancestor with O_DIRECTORY|O_NOFOLLOW via dir_fd.

    A path below /proc/self/fd/N starts from an already open owned directory,
    never from a fresh pathname lookup. Caller owns the returned descriptor.
    A rename/symlink swap after a component is opened cannot redirect writes.

    The group/world-writable directory rejection applies to the CANDIDATE TREE
    only: the installer-owned root (APP) and everything below it. A standard
    user directory such as ~/.local/share is routinely group-writable under the
    common umask 0002, and the group-write bit grants a same-UID adversary no
    power the owner does not already have (see module docstring). Foreign
    ancestors still reject symlinked, foreign-owned or non-directory entries;
    read-only lookups (strict=False) reject those too but never the mode.
    """
    path = Path(path)
    if not path.is_absolute():
        raise RaceHold("Only absolute pinned directory paths are permitted")
    parts = path.parts
    alias = len(parts) >= 5 and parts[:4] == ("/", "proc", "self", "fd") and parts[4].isdigit()
    if alias:
        try:
            current = os.dup(int(parts[4]))
        except OSError as exc:
            raise RaceHold("Invalid pinned root descriptor") from exc
        offset = 5
        _safe_dir_stat(os.fstat(current))
    else:
        current = os.open("/", O_DIR)
        offset = 1
    try:
        remaining = parts[offset:]
        if not remaining and private:
            _safe_dir_stat(os.fstat(current), private=True)
        if strict is None:
            strict = bool(create or private)
        # Mode enforcement starts at, and only applies below, the installer's
        # own candidate root directory (or an already-pinned /proc/self/fd root).
        in_candidate = alias
        for index, component in enumerate(remaining):
            if not component or component in (".", "..") or "/" in component:
                raise RaceHold("Unsafe absolute directory component")
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current)
                    os.fsync(current)
                except FileExistsError:
                    pass
            nxt = os.open(component, O_DIR, dir_fd=current)
            try:
                st = os.fstat(nxt)
                if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0, os.getuid()):
                    raise RaceHold("Unsafe ancestor owner/type")
                if not in_candidate and component == "tihulu-satellite-stable-first":
                    in_candidate = True
                if (strict and in_candidate and st.st_uid == os.getuid()
                        and st.st_mode & 0o022):
                    raise RaceHold("Unsafe candidate directory mode")
                if private and index == len(remaining) - 1:
                    _safe_dir_stat(st, private=True)
            except BaseException:
                os.close(nxt)
                raise
            os.close(current)
            current = nxt
        return current
    except BaseException:
        os.close(current)
        raise


@contextmanager
def open_nested_dir(parent_fd: int, parts: list[str] | tuple[str, ...]):
    fds = []
    current = parent_fd
    try:
        for part in parts:
            if not part or part in (".", "..") or "/" in part:
                raise RaceHold("Invalid nested directory name")
            fd = os.open(part, O_DIR, dir_fd=current)
            _safe_dir_stat(os.fstat(fd))
            fds.append(fd)
            current = fd
        yield current
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _noreplace(from_fd: int, from_name: str, to_fd: int, to_name: str):
    """Linux renameat2 NOREPLACE; fail closed if kernel lacks the primitive."""
    if _RENAMEAT2 is None:
        raise RaceHold("renameat2 RENAME_NOREPLACE unavailable; fail closed")
    src, dst = os.fsencode(from_name), os.fsencode(to_name)
    code = _RENAMEAT2(from_fd, src, to_fd, dst, RENAME_NOREPLACE)
    if code != 0:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise RaceHold("Destination already exists: foreign entry preserved")
        raise OSError(error, os.strerror(error))


def _read_owned(parent_fd, name, *, max_bytes):
    fd = os.open(name, O_FILE, dir_fd=parent_fd)
    try:
        st = _safe_file_fd(fd, max_bytes=max_bytes)
        data = b""
        while len(data) <= max_bytes:
            chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) > max_bytes or len(data) != st.st_size:
            raise RaceHold("Changed/oversized file during descriptor read")
        return data, _identity(st)
    finally:
        os.close(fd)


def register_no_clobber(target: Path, expected: bytes, *, mode=0o600):
    """Never os.replace over a pre-existing launcher/desktop name.

    Existing *identical* managed entry is left byte-for-byte unchanged. New
    entries use O_EXCL at the final name; a post-preflight foreign insertion
    fails with EEXIST and never gets overwritten.
    """
    target = Path(target)
    # /proc/self/fd/N is a kernel magic-link to an already pinned directory.
    # O_NOFOLLOW on that alias raises ENOTDIR; dup the verified open fd instead.
    parent_parts = target.parent.parts
    if len(parent_parts) == 5 and parent_parts[:4] == ("/", "proc", "self", "fd") and parent_parts[4].isdigit():
        parent = os.dup(int(parent_parts[4]))
    else:
        parent = pinned_dir_fd(target.parent)
    try:
        # User/mid-tree parents (e.g. ~/.local/bin) must not be rejected for their
        # group-write bit: pinned_dir_fd already rejected symlink/foreign/non-dir
        # ancestors and the candidate root fd was validated private at lock time.
        try:
            actual, _ = _read_owned(parent, target.name, max_bytes=16 * 1024)
            if actual != expected:
                raise RaceHold("Foreign launcher or desktop bytes cannot be overwritten")
            return False
        except FileNotFoundError:
            pass
        try:
            fd = os.open(target.name,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         mode, dir_fd=parent)
        except FileExistsError as exc:
            raise RaceHold("Launcher inserted after preflight; no overwrite") from exc
        with os.fdopen(fd, "wb") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
        return True
    finally:
        os.close(parent)


def _vault_fd(parent_fd: int, name: str = ".satellite-preserved") -> int:
    """Pin a private *retention* directory; never delete entries from it."""
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    fd = os.open(name, O_DIR, dir_fd=parent_fd)
    _safe_dir_stat(os.fstat(fd), private=True)
    # A finite bound is preferable to unbounded quarantine accumulation.
    entries = os.listdir(fd)
    if len(entries) >= 4096:
        os.close(fd)
        raise RaceHold("Preservation vault full; manual inspection required")
    return fd


def retain_quarantine_tombstones(root_fd: int) -> int:
    """Retain a stranded `.satellite-quarantine-*` tombstone in the vault.

    remove_known_file renames the attested target to a deterministic tombstone
    and only then moves it into the vault. A power loss or SIGKILL between those
    two renames leaves the tombstone in the candidate root. Its bytes are
    unauthenticated after the interruption (a same-UID process could have
    replaced them), so they are MOVED, never deleted, and never treated as
    canonical. Returns the number of retained tombstones.
    """
    # Fast path: with no tombstone present no vault is opened (or created), so
    # a full vault cannot block an unrelated pointer recovery.
    names = os.listdir(root_fd)
    if not any(re.fullmatch(r"\.satellite-quarantine-[0-9a-f]{32}", name) for name in names):
        return 0
    vault = _vault_fd(root_fd)
    try:
        kept = 0
        for name in names:
            if not re.fullmatch(r"\.satellite-quarantine-[0-9a-f]{32}", name):
                continue
            unique = "preserved-" + uuid.uuid4().hex
            _noreplace(root_fd, name, vault, unique)
            kept += 1
        if kept:
            os.fsync(vault)
            os.fsync(root_fd)
        return kept
    finally:
        os.close(vault)


def remove_known_file(root_fd, relative: str, digest: str | None, *,
                      max_bytes=16 * 1024 * 1024, vault_fd=None):
    """Move only attested bytes into a non-destructive vault. No final unlink.

    This implements logical uninstall while preserving the actual bytes for
    independent recovery. A same-UID actor can swap a pathname at ANY point:
    no check-then-unlink can guarantee the checked inode is later deleted.
    A substitution is either restored, or kept byte-for-byte in the vault.
    """
    parts = relative.split("/")
    if not parts or any(not x or x in (".", "..") for x in parts):
        raise RaceHold("Invalid removal inventory name")
    with open_nested_dir(root_fd, parts[:-1]) as parent:
        name = parts[-1]
        try:
            original, _ = _read_owned(parent, name, max_bytes=max_bytes)
        except FileNotFoundError:
            return False
        if digest is None:
            raise RaceHold("Missing trustworthy file digest; refuse deletion")
        if hashlib.sha256(original).hexdigest() != digest:
            raise RaceHold("Modified/foreign file: refusing removal")
        tomb = ".satellite-quarantine-" + uuid.uuid4().hex
        _noreplace(parent, name, parent, tomb)
        try:
            relocated, _ = _read_owned(parent, tomb, max_bytes=max_bytes)
            if hashlib.sha256(relocated).hexdigest() != digest:
                raise RaceHold("File substituted after validation: preserved")
            own_vault = None
            if vault_fd is None:
                own_vault = _vault_fd(root_fd)
                destination_fd = own_vault
            else:
                destination_fd = vault_fd
            try:
                unique = "preserved-" + uuid.uuid4().hex
                # Move, never overwrite, never unlink; last-nanosecond attacker
                # replacement of tomb is retained under a unique vault name.
                _noreplace(parent, tomb, destination_fd, unique)
                stored, _ = _read_owned(destination_fd, unique, max_bytes=max_bytes)
                os.fsync(destination_fd)
                if hashlib.sha256(stored).hexdigest() != digest:
                    raise RaceHold("Late quarantine replacement retained; HOLD")
            finally:
                if own_vault is not None:
                    os.close(own_vault)
            return True
        except BaseException:
            try:
                _noreplace(parent, tomb, parent, name)
            except (OSError, RaceHold):
                # Unexpected bytes remain in a visible tombstone; no deletion.
                pass
            raise



def remove_known_tree(root_fd: int, dirname: str, known: dict[str, str | None]):
    """No recursive wildcard delete. Explicitly remove owned names, then empty dirs.

    Foreign insertions at any point keep the release directory nonempty and
    therefore cause HOLD rather than being deleted by rmtree.
    """
    if not re.fullmatch(r"(?:preview[0-9]+-[a-f0-9]{12}|\.staging-[a-f0-9]{32})", dirname):
        raise RaceHold("Unexpected release/stage directory name")
    with open_nested_dir(root_fd, ["releases"]) as releases:
        vault = _vault_fd(root_fd)
        try:
            return _remove_known_tree_with_vault(releases, dirname, known, vault)
        finally:
            os.close(vault)


def _remove_known_tree_with_vault(releases, dirname, known, vault):
        with open_nested_dir(releases, [dirname]) as candidate:
            # Reject unknown names early; still use *only* this whitelist later.
            expected_files = set(known)
            expected_dirs = set()
            for rel in expected_files:
                pieces = rel.split("/")
                if any(not x or x in (".", "..") for x in pieces):
                    raise RaceHold("Unsafe whitelist entry")
                for n in range(1, len(pieces)):
                    expected_dirs.add("/".join(pieces[:n]))
            for base, dirs, files, dirfd in os.fwalk(".", topdown=True, follow_symlinks=False, dir_fd=candidate):
                prefix = base[2:] if base.startswith("./") else ""
                for name in dirs:
                    rel = (prefix + "/" if prefix else "") + name
                    if rel not in expected_dirs:
                        raise RaceHold("Foreign directory inserted into candidate release")
                for name in files:
                    rel = (prefix + "/" if prefix else "") + name
                    if rel not in expected_files:
                        raise RaceHold("Foreign file inserted into candidate release")
            for rel in sorted(known, key=lambda v: (-v.count("/"), v)):
                # Missing parent due to interrupted deletion is normal.
                try:
                    remove_known_file(candidate, rel, known[rel], vault_fd=vault)
                except FileNotFoundError:
                    continue
            for rel in sorted(expected_dirs, key=lambda v: (-v.count("/"), v)):
                parts = rel.split("/")
                try:
                    with open_nested_dir(candidate, parts[:-1]) as parent:
                        os.rmdir(parts[-1], dir_fd=parent)
                        os.fsync(parent)
                except FileNotFoundError:
                    continue
                except OSError as error:
                    if error.errno in (errno.ENOTEMPTY, errno.EEXIST):
                        raise RaceHold("Foreign insertion blocked empty-dir removal") from error
                    raise
        try:
            os.rmdir(dirname, dir_fd=releases)
            os.fsync(releases)
        except OSError as error:
            if error.errno in (errno.ENOTEMPTY, errno.EEXIST):
                raise RaceHold("Foreign insertion preserved; candidate directory not removed") from error
            raise


def unregister_exact(target: Path, expected: bytes):
    """Unregister only matching entry, even after a preflight substitution."""
    target = Path(target)
    parts = target.parent.parts
    if len(parts) == 5 and parts[:4] == ("/", "proc", "self", "fd") and parts[4].isdigit():
        parent = os.dup(int(parts[4]))
    else:
        parent = pinned_dir_fd(target.parent)
    try:
        # User/mid-tree parents (e.g. ~/.local/bin) must not be rejected for their
        # group-write bit: pinned_dir_fd already rejected symlink/foreign/non-dir
        # ancestors and the candidate root fd was validated private at lock time.
        try:
            actual, _ = _read_owned(parent, target.name, max_bytes=16 * 1024)
        except FileNotFoundError:
            return False
        if actual != expected:
            raise RaceHold("Foreign launcher/desktop; deletion refused")
        return remove_known_file(parent, target.name, hashlib.sha256(expected).hexdigest(),
                                 max_bytes=16 * 1024)
    finally:
        os.close(parent)


# A crash intent is written and fsynced BEFORE ACTIVE is moved into history.
# A same-UID adversary can read the owner marker, so this HMAC provides tamper
# detection and release-pin binding, NOT an OS trust boundary against that UID.
ACTIVE_INTENT = "ACTIVE.intent.json"
_INTENT_FIELDS = frozenset(("app", "schema", "source_sha256", "token",
                            "history", "old_hex", "new_sha256", "mac"))


def _canonical_json(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _intent_token(root_fd):
    try:
        raw, _ = _read_owned(root_fd, "OWNER.json", max_bytes=4096)
        marker = json.loads(raw)
        if (set(marker) != {"app", "uid", "schema", "token"}
                or marker["app"] != "tihulu-satellite-stable-first"
                or marker["uid"] != os.getuid()
                or marker["schema"] != 1
                or not isinstance(marker["token"], str)
                or not re.fullmatch("[0-9a-f]{32}", marker["token"])):
            raise RaceHold("Invalid pinned owner marker for crash transaction")
        return marker["token"]
    except (TypeError, ValueError, KeyError) as exc:
        raise RaceHold("Malformed owner marker for crash transaction") from exc


def _intent_mac(unsigned, token, pinned_sha):
    if not isinstance(pinned_sha, str) or not re.fullmatch("[0-9a-f]{64}", pinned_sha):
        raise RaceHold("Invalid fixed source SHA")
    key = hashlib.sha256(bytes.fromhex(token) + bytes.fromhex(pinned_sha)).digest()
    return hmac.new(key, _canonical_json(unsigned), hashlib.sha256).hexdigest()


def _intent_bytes(root_fd, *, old, new, history, pinned_sha):
    if len(new) > 8192 or (old is not None and len(old) > 8192):
        raise RaceHold("ACTIVE pointer exceeds crash-intent bounds")
    token = _intent_token(root_fd)
    unsigned = {
        "app": "tihulu-satellite-stable-first", "schema": 1,
        "token": token, "source_sha256": pinned_sha,
        "history": history, "old_hex": old.hex() if old is not None else None,
        "new_sha256": hashlib.sha256(new).hexdigest(),
    }
    return _canonical_json({**unsigned, "mac": _intent_mac(unsigned, token, pinned_sha)})


def _read_intent(root_fd, pinned_sha):
    data, _ = _read_owned(root_fd, ACTIVE_INTENT, max_bytes=32768)
    try:
        value = json.loads(data)
        if set(value) != _INTENT_FIELDS or _canonical_json(value) != data:
            raise RaceHold("Noncanonical ACTIVE crash intent")
        if (value["app"] != "tihulu-satellite-stable-first"
                or type(value["schema"]) is not int or value["schema"] != 1
                or value["source_sha256"] != pinned_sha
                or value["token"] != _intent_token(root_fd)
                or not isinstance(value["history"], str)
                or not re.fullmatch(r"\.history-[0-9a-f]{32}", value["history"])
                or not isinstance(value["new_sha256"], str)
                or not re.fullmatch("[0-9a-f]{64}", value["new_sha256"])
                or value["old_hex"] is not None and (
                    not isinstance(value["old_hex"], str)
                    or len(value["old_hex"]) > 16384
                    or not re.fullmatch("(?:[0-9a-f]{2})*", value["old_hex"]))):
            raise RaceHold("Invalid ACTIVE intent bindings")
        unsigned = {k: value[k] for k in value if k != "mac"}
        expected = _intent_mac(unsigned, value["token"], pinned_sha)
        if not isinstance(value["mac"], str) or not hmac.compare_digest(value["mac"], expected):
            raise RaceHold("Unauthenticated ACTIVE crash intent")
        return value
    except (ValueError, TypeError, UnicodeDecodeError, KeyError) as exc:
        raise RaceHold("Malformed/forged ACTIVE crash intent") from exc


def _retire_active_intent(root_fd):
    """Preserve verified intent in a bounded private vault; never unlink."""
    vault = _vault_fd(root_fd)
    try:
        _noreplace(root_fd, ACTIVE_INTENT, vault, "intent-" + uuid.uuid4().hex)
        os.fsync(vault)
        os.fsync(root_fd)
    finally:
        os.close(vault)


def _publish_active_with_intent(target: Path, payload: bytes, mode: int, pinned_sha: str):
    if target.name != "ACTIVE.json":
        raise RaceHold("Crash-intent publication requires ACTIVE.json")
    parent = pinned_dir_fd(target.parent, private=True)
    try:
        # An unresolved transaction is an explicit HOLD, never auto-overwritten.
        try:
            os.stat(ACTIVE_INTENT, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RaceHold("Pending ACTIVE crash-intent: run recover before publishing")
        old = None
        try:
            old, _ = _read_owned(parent, target.name, max_bytes=8192)
            if old == payload:
                return False
        except FileNotFoundError:
            pass
        # Ensure retention capacity before changing canonical ACTIVE.
        vault = _vault_fd(parent)
        os.close(vault)
        history = ".history-" + uuid.uuid4().hex
        raw_intent = _intent_bytes(parent, old=old, new=payload, history=history,
                                   pinned_sha=pinned_sha)
        temp = ".write-" + uuid.uuid4().hex
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=parent)
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        fd = os.open(ACTIVE_INTENT, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as output:
            output.write(raw_intent)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(parent)  # Durable intent before history rename.
        moved = False
        if old is not None:
            _noreplace(parent, target.name, parent, history)
            moved = True
            os.fsync(parent)  # Durable history before new pointer.
            actual, _ = _read_owned(parent, history, max_bytes=8192)
            if actual != old:
                try:
                    _noreplace(parent, history, parent, target.name)
                    os.fsync(parent)
                except (RaceHold, OSError):
                    pass
                raise RaceHold("Metadata swapped after preflight; foreign bytes preserved")
        try:
            _noreplace(parent, temp, parent, target.name)
            os.fsync(parent)
        except BaseException:
            if moved:
                try:
                    _noreplace(parent, history, parent, target.name)
                    os.fsync(parent)
                except (RaceHold, OSError):
                    pass
            raise
        observed, _ = _read_owned(parent, target.name, max_bytes=8192)
        if observed != payload:
            raise RaceHold("Changed ACTIVE pointer after publication; retained")
        _retire_active_intent(parent)
        return True
    finally:
        os.close(parent)


def recover_active_pointer(root_fd, *, pinned_sha, validate):
    """Only restore an explicitly-intended, authenticated and source-verified old pointer.

    Caller holds exclusive PinnedRoot lock and supplies a validator checking
    all referenced approved release indexes against immutable source digests.
    An unauthenticated/unexpected history entry is NEVER a recovery candidate.
    """
    try:
        intent = _read_intent(root_fd, pinned_sha)
    except FileNotFoundError:
        return False
    old = bytes.fromhex(intent["old_hex"]) if intent["old_hex"] is not None else None
    if old is not None:
        validate(old)
    # Even when the next pointer was published, an unexpected/modified old
    # history is not safe to ignore. Treat it as foreign data and HOLD.
    history = None
    try:
        history, _ = _read_owned(root_fd, intent["history"], max_bytes=8192)
    except FileNotFoundError:
        pass
    if history is not None and history != old:
        raise RaceHold("Untrusted/modified history; pointer recovery HOLD")
    active = None
    try:
        active, _ = _read_owned(root_fd, "ACTIVE.json", max_bytes=8192)
    except FileNotFoundError:
        pass
    if active is not None:
        if active == old:
            pass  # Interruption was before moving the pointer.
        elif hashlib.sha256(active).hexdigest() == intent["new_sha256"]:
            validate(active)  # Fully published, not a reason to restore history.
            if old is not None and history is None:
                raise RaceHold("Missing authenticated old history after commit")
        else:
            raise RaceHold("Foreign ACTIVE pointer during crash recovery")
    elif old is not None:
        if history is None:
            raise RaceHold("Missing authenticated history; pointer recovery HOLD")
        # Never rename an attacker-writable history PATH into canonical ACTIVE
        # after a check/recheck race. Reconstruct from the authenticated bytes
        # already validated against the source-pinned release inventory.
        temp = ".write-" + uuid.uuid4().hex
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root_fd)
        with os.fdopen(fd, "wb") as output:
            output.write(old)
            output.flush()
            os.fsync(output.fileno())
        _noreplace(root_fd, temp, root_fd, "ACTIVE.json")
        os.fsync(root_fd)
        restored, _ = _read_owned(root_fd, "ACTIVE.json", max_bytes=8192)
        current_history, _ = _read_owned(root_fd, intent["history"], max_bytes=8192)
        if restored != old or current_history != old:
            raise RaceHold("Late ACTIVE/history substitution; foreign bytes retained")
    # old==None and absent ACTIVE: no prior pointer to resurrect.
    _retire_active_intent(root_fd)
    return True


def publish_metadata_no_clobber(path: Path, payload: bytes, *, mode=0o600, pinned_sha=None):
    """Publish metadata without os.replace: never overwrite an unknown path.

    For a changed existing value, atomically move it to an immutable-by-policy
    history name (NOREPLACE), attest its bytes, and then insert the new value
    with NOREPLACE. If another writer substitutes an entry, HOLD with both
    objects retained. Retained history is intentionally never auto-cleaned.
    """
    target = Path(path)
    if pinned_sha is not None:
        return _publish_active_with_intent(target, payload, mode, pinned_sha)
    parts = target.parent.parts
    # Existing, verified pinned /proc/self/fd/N is a magic symlink.
    # Duplicate its open inode; never traverse the magic link with O_NOFOLLOW.
    if len(parts) == 5 and parts[:4] == ("/", "proc", "self", "fd") and parts[4].isdigit():
        parent = os.dup(int(parts[4]))
    else:
        parent = pinned_dir_fd(target.parent)
    try:
        # User/mid-tree parents (e.g. ~/.local/bin) must not be rejected for their
        # group-write bit: pinned_dir_fd already rejected symlink/foreign/non-dir
        # ancestors and the candidate root fd was validated private at lock time.
        old = None
        try:
            old, _ = _read_owned(parent, target.name, max_bytes=128 * 1024)
            if old == payload:
                return False
        except FileNotFoundError:
            pass
        tmp = ".write-" + uuid.uuid4().hex
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     mode, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        original_history = None
        if old is not None:
            original_history = ".history-" + uuid.uuid4().hex
            _noreplace(parent, target.name, parent, original_history)
            observed, _ = _read_owned(parent, original_history, max_bytes=128 * 1024)
            if observed != old:
                try:
                    _noreplace(parent, original_history, parent, target.name)
                except (RaceHold, OSError):
                    pass
                raise RaceHold("Metadata swapped after preflight; foreign bytes preserved")
        try:
            _noreplace(parent, tmp, parent, target.name)
        except BaseException:
            if original_history is not None:
                try:
                    _noreplace(parent, original_history, parent, target.name)
                except (RaceHold, OSError):
                    pass
            raise
        observed, _ = _read_owned(parent, target.name, max_bytes=128 * 1024)
        os.fsync(parent)
        if observed != payload:
            # No deletion or overwrite of unexpected bytes, ever.
            raise RaceHold("Metadata changed at publication; HOLD with bytes preserved")
        return True
    finally:
        os.close(parent)
