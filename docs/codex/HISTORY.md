# SATELLITE — Stable-First Decision History

Append-only; dates are historical report/checkpoint dates, not proof of current CI or deployment.

- **Before 2026-11-20:** Private `0.3.0~preview10` stable-first preservation and disposable security/transaction QA existed separately. This is not evidence of a public deployable source tree.
- **2026-11-20:** The previously empty public Satellite Center repository received a documentation-only README at `197b007cdfe5d45f7809711492807ec96e986f28`; no private archive was published.
- **2026-11-20:** Documentation-only Codex handoff on `docs/stable-first-codex-handoff-20261120`, no source, app, owner-machine, other PR, protected snapshot, or release change. Exact commit supplied by GitHub after creation.

## Recording a new milestone
Append date (UTC), source SHA, PR URL, CI run URL, tested fixture/hardware scope, blocker owner and separate GO/HOLD for source, clean install, hardware and public release. Mark uncertainties as unverified and never delete older decisions.

- **2026-11-20:** Combined P1 ancestor-dirfd + P2 durable ACTIVE recovery successor delivered as Draft PR #25 in `Tihulu/tihulu-survival-kit-plan` (branch `satellite-pr16-combined-p1p2-remediation`, exact head `17a352d4974f84eae55057ab938762dc82a003c5`, stacked on frozen PR #13 `a201f09c…`). Exact-head CI run `44677008509` COMPLETED SUCCESS, 2/2 jobs; disposable-HOME suites `Ran 74 tests … OK` (47 inherited + 14 PR #14 P2 crash + 13 PR #15 P1/P2 remediation, 0 SKIP). Integration also fixed two real product defects: umask-0002 group-writable payload ancestors from `Path.mkdir(parents=True)` and a `PLACEHOLDER`-MAC security regression. Record here is documentation-only: no source, app, private archive, file inventory, recording, owner-machine state, other PR, protected snapshot or release change. Independent Agent 02 QA at this exact head REQUESTED, NOT PERFORMED; production install/merge/public release HOLD.
