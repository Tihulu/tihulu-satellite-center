# Phase C: pin-only user-scoped candidate (private; release HOLD)

**Branch:** `satellite-stable-first-phase-b-user-installer-20261112` (stacked on Phase B PR #9). This is NOT a public installer. Never run on the owner's Pop!_OS computer without explicit approval.

## Scope / invariant

- Closed allowlist: preserved `0.3.0~preview10` archive SHA-256 `291b88a05ec21e3becab01bca2c5c6311fba2a29673bb18317a8376570380c0a` only. Recheck archive bytes and inventory from *one immutable byte snapshot* before extracting. Never pass untrusted paths to `tarfile.extractall`.
- Unique executable: `~/.local/bin/tihulu-satellite-stable-first`. Unique desktop: `$XDG_DATA_HOME/applications/tihulu-satellite-stable-first.desktop`. Versioned package root: `$XDG_DATA_HOME/tihulu-satellite-stable-first/releases/<pinned release>`.
- Separate sandbox at `$XDG_DATA_HOME/tihulu-satellite-stable-first-runtime`, with `HOME` and XDG config/cache/state switched to `$XDG_STATE_HOME/tihulu-satellite-stable-first-home` for **explicit launches**. These *do not* replace or migrate the original Debian `/usr/bin/tihulu-satellite-center`, user-web `~/.local/bin/tihulu-satellite`, user settings, caches, recordings, TLEs, Media/Flight/Text/AI applications or SDR services.
- First-party script requires no root and forbids XDG base paths outside HOME. Directory ancestors cannot be symlinks. Candidate root must be private mode 0700 with a per-user marker and exclusive nonblocking `flock`. No package manager, service manipulation, implicit launches, or network requests.
- Install, repeat/update, rollback, incomplete-staging recovery and uninstall are separate operations. An active GUI holds a shared flock over its release to refuse update/removal while running. Uninstall refuses foreign/mutated files, removes only candidate launcher, desktop entry and verified release payload; preserves isolated runtime data and a tiny private management marker/lock/manager so retries/recovery are possible.
- **Rollback limitation:** preview10 is the only approved production release, so a real previous-version rollback is unavailable until the owner approves another pinned archive. Tests can inject a second synthetic pin *within the Python test process only* to verify distinct-version pointer switching; the CLI cannot accept arbitrary digests or versions.
- Recover reconciles owned interrupted staging (including a completed atomic promotion) using matching transaction token and expected safe paths. Uninstall journals the preverified file-hash inventory so interruption mid-delete can be safely retried; any new, mutated or foreign file fails CLOSED for manual independent inspection. The desktop icon is a stable system theme icon, so rollback changes only the atomic active-release pointer.

## Disposable CLI contract (documentation only — do not use on owner's host)

From the private checked-out repo in a throwaway Ubuntu runner whose `HOME` is disposable:

```sh
python3 evidence/satellite-stable-first/phase-c/installer.py install \
  --archive evidence/satellite-stable-first/runs/20261112T190206Z-ff7172/artifacts/installed-package-files.tar.gz \
  --manifest evidence/satellite-stable-first/runs/20261112T190206Z-ff7172/files.txt

# Commands: status, doctor, recover, rollback, uninstall
# GUI launch is explicitly user-initiated via the unique stable-first launcher.
```

The true default `desktop` uses the exact bundled HTML/CSS/JS with native Qt WebEngine/WebChannel (not browser WebUSB). An alternate `desktop-widgets` uses native Qt Widgets. Both have independent bundled `--smoke` methods usable in isolated CI. GUI smoke only establishes startup/synthetic UI checks, **not** RTL-SDR reception or feature parity on the owner's machine.

## Gates

- Prior Phase B PR #9: 31/31 validated archive/transaction checks at SHA `78bb8b97ca977b2e08e5df2889e5afc0c2f3d07d`, CI 43303133002; unchanged and reserved for Agent 02.
- Phase C defines 29 disposable lifecycle/security/dependency tests, 1 read-only frozen archive check, and two real bundled GUI smoke procedures (WebEngine + Qt Widgets). Cite the **exact matching HEAD** successful workflow run in the PR report. Test existence is not evidence of PASS.
- Independent Agent 02 audit, genuine same-UID filesystem race analysis, power interruption matrix, different authorized production version rollback, Pop!_OS owner-authorized clean install, user-web coexistence, source and data licenses, runtime dependency matrix and real SDR field tests: **HOLD**.
- No merge, public release, owner's machine, real receiver use, or authoring agent self-approval. Coordinator ledger: https://github.com/Tihulu/tihulu-survival-kit-plan/issues/8; Agent 02 issue: https://github.com/Tihulu/tihulu-survival-kit-plan/issues/10.

## PR #13 — independent P1-SAT12-01/02/03/04 remediation candidate (2026-11-13)

Private *stacked* Draft PR #13 adds only code/tests and disposable Ubuntu workflow; original Draft PRs #9, #11, #12 remain frozen. **No owner installation and no hardware access.** Exact verified run and SHA must be read from GitHub Actions and recorded in PR #13 / issue #10 / issue #8; test files alone never establish PASS.

- **Journal is NOT authorization**: `UNINSTALL.json` is a progress record only. Each permitted payload path and SHA-256 must equal the inventory decoded from the fixed SHA-256 approved archive cached under the candidate's `sources/` directory. If the archive is missing, substituted, has the wrong hash, or the journal disagrees, **HOLD** (including legacy PR #12 installations that lack the cache). The cache itself is verified against an in-code approved digest; a same-UID-writable owner token is never an integrity credential.
- **Metadata must be exact**: `INDEX.json` and `STAGING.json` bytes must match canonical serialized records using the pinned payload inventory; a file at a known name is **not** removable merely because its path is allowlisted. Interrupted staging accepts missing known files but refuses altered or unapproved data.
- **No final quarantine unlink**: known files are moved with `RENAME_NOREPLACE` into a bounded retention vault; bytes are preserved, even for later detected substitution. Return value `removed_releases=true` means candidate names are absent from `releases/`, **not** that storage is reclaimed. `physical_deletion=HOLD`; retention vaults require manual inspection/reclamation outside the scope of this installer. Maximum 4096 retained entries per vault; exhaustion produces HOLD.
- **Metadata publication never calls `os.replace`**: existing metadata is moved to unique preserved `.history-*` names, rechecked and a new record is inserted with `RENAME_NOREPLACE`; if foreign bytes appear, the operation fails closed. History/temp residues can remain after interruption and must not be silently cleaned.
- **Staging promotion is no-clobber** and promotion metadata is checksum-authenticated before retention. Full root/lock descriptor protections and all prior malicious-file test cases remain in the test suite.
- **Test strategy:** 29 previous lifecycle + 12 previous adversarial + 6 new independent P1 negative cases, against actual `installer.py` and `race_hardening.py`, all on throwaway `HOME`. The CI also performs frozen package hash/inventory and two native Qt GUI smoke procedures. No owner-host/SDR test.
- **Security caveat:** user-owned and same-UID-writable files cannot be made a robust hostile same-UID trust domain using hash checks alone. An attacker may modify their own HOME, file names or running Python interpreter. This candidate prioritizes zero automatic foreign-byte deletion; threat-model acceptance requires Agent 02. Local retained data, incomplete crash points, actual second production pin, real Pop!_OS UX, physical receiver, dependency rights and release remain **HOLD**.

Author must publish exact SHA, job URLs, test counts and negative byte preservation before requesting independent Agent 02 re-review. Never merge or release without owner approval and independent security acceptance.
