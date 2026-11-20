# Acceptance evidence (sandbox) — status and limits

All runtime verification below was performed in a **sandbox**: a disposable HOME, XDG
data/state directories, TMPDIR, an isolated runtime namespace, and — for GUI checks —
`xvfb`. The owner's working installation was verified byte-identical after every run
(`/usr/bin/tihulu-satellite-center` and `~/.local/bin/tihulu-satellite`: same mode, size,
mtime and SHA-256), and no candidate path was ever created outside the sandbox.

## Verified in the sandbox

| Check | Result |
|---|---|
| Install (pinned `0.3.0~preview10`) from the frozen archive | rc 0; pointer `{"active":"preview10-291b88a05ec2","previous":null}` |
| `status`, `doctor` | rc 0; doctor reports Python/Qt/WebEngine/numpy/skyfield presence, passive receiver detection only |
| `recover` on a clean tree | no-op (0) |
| `rollback` with no previous release | fail-closed HOLD (by design: only one approved production release exists) |
| Launch | executes; GUI stayed alive until our own timeout under `xvfb`; asset replies logged |
| Payload integrity after launch | **0 `.pyc`** (fixed defect, see below) |
| Uninstall | `removed_releases`, `runtime_preserved`, `payload_bytes_retained`, `physical_deletion: HOLD`; pointer removed |
| Upgrade → rollback (second, synthetic in-process pin) | pointer switched to the second release and back; both releases retained; `_index_for` OK for both; 0 `.pyc` |
| Crash soak | 14 randomized operations with 8 real `SIGKILL` injections: 0 unexpected exceptions, 1 expected fail-closed HOLD; final state converged with **86/86 payload digests correct** and `INDEX.json` matching the pinned source SHA-256; retained bytes preserved in the bounded vault |

Regression suites (private coordination repository): **78/78 PASS** — 47 inherited
lifecycle/adversarial/P1 negatives, 14 PR #14 P2 crash regressions, 13 PR #15 P1/P2
remediation regressions, plus 4 regression tests added for defects found during this work.

## Defects found by this acceptance work and fixed

1. **Bytecode in the pinned payload.** The first real launch executed CPython with
   `PYTHONPATH` inside the pinned payload and wrote `__pycache__/*.pyc` (9 files); every
   later index check then failed closed with "Unexpected installed directory". Fixed with
   `PYTHONDONTWRITEBYTECODE=1` in the launch environment. No integrity check was weakened.
2. **Legacy orphan history corner (P2-SAT15-02).** An unsigned `.history-*` orphan with no
   approved releases was reported as "not installed". `_pointer` now detects both orphan
   naming families and produces an explicit HOLD.
3. **Self-inflicted deadlock after SIGKILL in cleanup.** `remove_known_file`'s transient
   `.satellite-quarantine-*` tombstone was rejected by the root allowlist, so every later
   operation — including recover — HOLDed forever. Recover now retains such tombstones in
   the vault; wrong-length lookalikes remain foreign.
4. **Recovery renamed an attacker-writable path.** Recovery rebuilt `ACTIVE` from the
   already-authenticated bytes in a fresh `O_EXCL` file instead of renaming the history path.
5. **Launch environment.** `LD_PRELOAD`, `LD_LIBRARY_PATH`, `PYTHONHOME`, `PYTHONSTARTUP`,
   `PYTHONWARNINGS`, `PYTHONPYCACHEPREFIX` are dropped; `-P` prevents cwd shadowing;
   `manager.py` / `race_hardening.py` are re-attested byte-for-byte before exec.

## Explicitly NOT verified (HOLD)

- Clean installation on the owner's machine, rollback of a real (second) approved release,
  GUI acceptance against the owner's configuration, hardware/RTL-SDR operation, RF
  reception, and any comparison against the owner's installed behaviour.
- Byte-exact reproduction of the private preservation artifact
  (`291b88a05ec21e3becab01bca2c5c6311fba2a29673bb18317a8376570380c0a`) from this
  repository: its original build recipe is not here, so this repository publishes source
  plus its own reproducible package, not that pinned binary.
- Independent security signoff of this repository's own packaging.

**Public release, merge and production installation remain HOLD.**
