# Source-only migration checkpoint (2026-11-20)

This directory was migrated **source-only** from the private coordination repository
(`Tihulu/tihulu-survival-kit-plan`, Draft PR #25, exact head
`17a352d4974f84eae55057ab938762dc82a003c5`) into this public repository at the owner's
explicit request.

## What was NOT migrated, and must not be

- The pinned `0.3.0~preview10` frozen archive (`installed-package-files.tar.gz`), its
  file inventory (`files.txt`), any run directory, snapshot, recording, TLE cache,
  installed program, or local setting. Those remain in authorized private storage.
- The CI workflow, because it cannot execute here: `test_installer.py`,
  `test_adversarial.py`, `test_pr12_p1_negative.py`, `test_pr13_p2_crash.py` and
  `test_pr13_p1p2_remediation.py` read the frozen archive through
  `evidence/satellite-stable-first/runs/<run>/artifacts/`. Until an owner-authorized
  action provisions that archive in this repository, the suites fail at import and CI
  would be a permanent red with no signal. Adding a green-looking CI here would require
  weakening the pinned-archive gate, which is refused.

## Verification status of the migrated exact head

| Gate | Status |
|---|---|
| Isolated successor; no frozen PR or user data touched | DONE |
| Local disposable-HOME suite 74/74 PASS, 0 SKIP | DONE (private archive present in the private repo) |
| Exact-head CI 2/2 SUCCESS | DONE — run `44677008509` in the private coordination repo |
| Independent Agent 02 QA at this exact head | **REQUESTED, NOT PERFORMED.** A local-model review attempt failed with a connection error and was not retried. |
| Production clean install / merge / public release | **HOLD** |

## Integration defects fixed in this exact head (recorded, not re-litigated)

1. `_extract` created intermediate payload directories with `Path.mkdir(parents=True)`,
   whose effective mode is `0o777 & ~umask`; under the common umask 0002 these became
   group-writable and failed the installer's own `_index_for` verification. Now created
   per component with `os.mkdir(..., 0o700, dir_fd=...)`.
2. The group/world-writable directory rejection is scoped to the candidate tree;
   symlinked, foreign-owned and non-directory ancestors are still rejected on every path.

No test was deleted, skipped or weakened. One test expectation (`test_09`) was
*strengthened* from a silent no-op into an explicit HOLD for unsigned orphan history.

## Threat model (unchanged, stated honestly)

Same-UID processes are not a full trust boundary. The helpers defend against
deterministic path swaps and foreign additions, and never recurse-delete unlisted
files. Physical deletion of retained bytes stays HOLD behind a bounded vault.
