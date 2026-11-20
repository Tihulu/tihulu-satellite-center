# Satellite stable-first — combined successor verified (2026-11-20, documentation only)

This is a **documentation-only** checkpoint. No application source, frozen archive,
private snapshot, file inventory, recording, installed program or owner-machine state
was copied to this public repository. Public source release remains **HOLD**.

## What was verified, and where

| Item | Value |
|---|---|
| Isolated successor branch | `satellite-pr16-combined-p1p2-remediation` (private coordination repo `Tihulu/tihulu-survival-kit-plan`) |
| Exact head | `17a352d4974f84eae55057ab938762dc82a003c5` |
| Stacked on (frozen) | PR #13 `a201f09c5ca0953fa4b80e45d2a970185855fbc7` |
| Verbatim sources merged | PR #15 `93c79833a92fcb97c74814cb98c9e757d27b391d` (P1 ancestor-dirfd, HMAC `ACTIVE.intent.json`) and PR #14 `f16c7ddd856fc2e6d7a423227f8c083785283eb4` (P2 `POINTER_TXN` two-phase pointer transactions) |
| Draft PR | https://github.com/Tihulu/tihulu-survival-kit-plan/pull/25 |
| Exact-head CI | https://github.com/Tihulu/tihulu-survival-kit-plan/actions/runs/44677008509 — COMPLETED SUCCESS, 2/2 jobs |
| CI test evidence | `disposable-installer`: `Ran 74 tests` + `OK`, 0 SKIP; `disposable-native-qt`: WebEngine/WebChannel + Qt Widgets smoke PASS |

## Test correspondence (74, verified per test — not by count alone)

- 47 inherited: 29 lifecycle (`test_installer.py`) + 12 same-UID adversarial (`test_adversarial.py`) + 6 P1 negatives (`test_pr12_p1_negative.py`)
- 14 PR #14 P2 crash regressions (`test_pr13_p2_crash.py`, verbatim)
- 13 PR #15 P1/P2 remediation regressions (`test_pr13_p1p2_remediation.py`, verbatim except `test_09`)

## Independent reviewer findings addressed

- **P2-SAT15-01** — first ACTIVE publication interruption must not lose usable recovery
  state: covered by SIGKILL at each transaction phase, a real process restart with CLI
  recovery and idempotent repeat, and the durable HMAC intent restore.
- **P2-SAT15-02** — legacy orphan history without a durable intent must produce an
  explicit HOLD (never "not installed"), with foreign bytes preserved. `test_09`'s
  expectation was strengthened from a silent no-op into that HOLD.

## Real defects found and fixed while integrating (not test-only changes)

1. `_extract` used `Path.mkdir(parents=True, mode=0o700)`, which creates intermediate
   directories as `0o777 & ~umask`. Under the common umask 0002 this produced
   **group-writable 0o775 payload ancestors**, violating the candidate-tree mode
   invariant and failing the installer's own `_index_for` verification. Replaced with
   per-component `os.mkdir(..., 0o700, dir_fd=...)`, which also delivers PR #15's
   ancestor-swap protection.
2. A previous local attempt had hand-rewritten `race_hardening.py` with a `PLACEHOLDER`
   MAC instead of the real HMAC, 8 KB read limits and missing `FileNotFoundError`
   handling — a genuine security regression. Replaced by the verbatim PR #15 module.
3. The group/world-writable directory rejection is scoped to the candidate tree (the
   installer's own root and below). Symlinked, foreign-owned and non-directory
   ancestors are still rejected on every path, including read-only lookups of the
   frozen archive. Rationale: `~/.local/share` is routinely 0o775 under umask 0002 and
   the group-write bit grants a same-UID adversary nothing the owner lacks.
4. Recovery reconciles both durable ledgers (HMAC crash-intent first, then
   `POINTER_TXN`); a pending intent yields HOLD rather than "not installed".

## Gates

| Gate | Status |
|---|---|
| Isolated successor; no frozen PR, workflow or user data touched | DONE |
| Local disposable-HOME suite 74/74 PASS, 0 SKIP | DONE |
| Exact-head CI 2/2 SUCCESS | DONE |
| Independent Agent 02 QA at this exact head | **REQUESTED, NOT PERFORMED** |
| Production clean install / merge / public release | **HOLD** |
| Phase B execution, hardware, SDR, services | HOLD (trust roots UNCONFIGURED, measured runner absent) |

## Remaining uncertainties (unverified)

- True power-loss durability (no physical power-cut experiment), arbitrary same-UID
  hostile process containment (HMAC key material is readable via the owner marker), and
  front-door/environment containment in the private QA harness remain unproven.
- Consolidation of the *code* into this public repository is not authorized by this
  repository's own policy ("no application source ... published here"); this record
  intentionally stays documentation-only.
