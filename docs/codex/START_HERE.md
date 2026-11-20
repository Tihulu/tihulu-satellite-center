# SATELLITE — Codex Stable-First Handoff

Updated: 2026-11-20 (UTC). Status: **PUBLIC RELEASE HOLD**. This branch is documentation-only and is not an approved stable release.

## Read order (minimum context)
**To resume this application in a new Codex session, copy the ready-to-run [application-specific prompt](CODEX_RESUME_PROMPT.md).**

1. This file (constraints + entry point).
2. [STATE.json](STATE.json) (machine-readable exact baseline, current Draft, blockers, next actions).
3. [KNOWLEDGE.jsonl](KNOWLEDGE.jsonl) (small source-linked retrieval chunks; not embeddings).
4. [HISTORY.md](HISTORY.md) (append-only historical decisions).
5. Project source and evidence referenced in STATE; on Flight Radar also read existing root `AGENTS.md`.

## Current scope
Document Satellite Center 0.3.0~preview10 private stable-first preservation without publishing any private snapshot, SDR data or installed files.

**Baseline:** `0.3.0~preview10` (private; no public source commit claimed — private preserved candidate; no public source provenance certified.

**Latest inspected work:** [Public repository README bootstrap](https://github.com/Tihulu/tihulu-satellite-center) at `197b007cdfe5d45f7809711492807ec96e986f28` — documentation-only; no code or snapshot publicly uploaded. Exact CI/head must be freshly rechecked before working.

## Non-negotiable invariants
- Do not copy private stable archives, exact private file inventories, installed programs, TLE caches, recordings or local settings to public GitHub.
- No installation, RTL-SDR access, model download, background service or device operation on owner host.
- Preview10 preservation, private snapshot and separately installed user-web variant are distinct and must not be overwritten.
- Do not claim any local release or public source was tested merely because private CI fixtures passed.

## Safe continuation
1. Work from this public docs-only handoff; do not assume the current public repository contains Satellite source.
2. Authorized Codex agent may review separately permissioned private preservation ledger without copying artifacts into public GitHub.
3. Plan owner-approved source provenance and clean-room tests; keep immutable preview10 and user-web variant separated.
4. Only after explicit scope and license clearance, propose a source publication Draft PR; never publish private archives by default.

## Retrieval indexing
Run `python3 docs/codex/index_local.py index` for an offline FTS5 search index stored outside Git; then `python3 docs/codex/index_local.py search "query"`. For **genuine semantic vectors**, run the same commands with `--model-dir /path/to/already-installed-local-embedding-model` using locally installed `sentence-transformers`. This is opt-in, strictly offline, no model download or cloud transmission; `KNOWLEDGE.jsonl` is vector-ready, not yet vector-embedded. Never commit private raw data or machine-generated index databases.

## Handoff updates
On a meaningful milestone, append HISTORY, update STATE + knowledge chunks, link exact PR head/CI URLs and PASS/HOLD claims. Do not overwrite history or infer acceptance from a CI green badge. Create a NEW isolated Draft PR; do not merge, tag, release or install without the owner's explicit separate approval.
