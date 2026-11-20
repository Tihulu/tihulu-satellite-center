# Codex resume prompt — TIHULU Satellite Center (stable-first)

> Paste the section below into a NEW Codex session. This public repository currently documents a **private stable-first preservation effort**; it must not be treated as a fully published source or working installer. **Public release: HOLD.**

## Copy-paste prompt

Continue **TIHULU Satellite Center** safe private-use stable-first preservation. The protected candidate is **0.3.0~preview10**, but its original preserved application/archive and QA evidence are in separately permissioned **private** coordination storage. Never upload that private archive, program source, recordings, logs, exact private inventories or owner-host files to this public repository by default.

**Repository:** `Tihulu/tihulu-satellite-center`  
**Documentation handoff branch:** `docs/stable-first-codex-handoff-20261120` (Draft PR #1; not merged)  
**Protected candidate:** `0.3.0~preview10` (private; do not guess a public source SHA)  
**Privacy boundary:** documentation in this public repository is a sanitized handoff, not authorization to import from the private TIHULU coordination repository.

### Step 1 — Read and verify
1. Read `AGENTS.md`, `docs/codex/START_HERE.md`, `STATE.json`, `KNOWLEDGE.jsonl`, `HISTORY.md`, `SEARCH_INDEX.md`, this file and the public README from the handoff branch.
2. Verify exactly what is present in this public repo. Do **not** assume it already contains runnable Satellite code, a source archive, a known matching snapshot or a tested installer.
3. Only if existing access and explicit scope permit it, inspect the separate private stable-first ledger **in place**. Do not reproduce confidential source listings, local paths, exact archive bytes, private system details, location data or user-web variants in public artifacts.

### Step 2 — Reconstruct the stable-first plan
- Treat the working native Pop!_OS installation and frozen preview10 as protected. A separately installed user-web variant is distinct and must remain untouched.
- Build an evidence-based inventory of desired Receiver/Planner, Images & Data, Earth/Sky, Signal Analysis and Auto-Capture & Decode capabilities, explicitly labeling verified, planned and unknown features.
- Define independent acceptance for satellite passes, ISS SSTV, Meteor LRPT, compatible WAV/CU8 capture options, long recordings, decoding and images. A captured I/Q file or successful mocked test does not prove a decoded image.
- Plan an isolated clean install and transactional rollback/uninstall lab, archive/path security review, licensing review and real hardware acceptance as **separate gated stages**. Respect default Background OFF, on-demand SDR ownership and hard stop. No device capture or installed application replacement without approval.
- Before any public-source Draft, identify provenance and redistribution rights, privacy boundaries, compatible third-party assets and which material the owner explicitly authorizes for publication. A private preserved snapshot is **not** public release approval.

### Step 3 — Knowledge and local retrieval
- Index only public, sanitized, source-linked `KNOWLEDGE.jsonl` records. SQLite FTS5 works offline; vector embeddings require an explicitly selected already-installed local model. Never download models, send private context to cloud services or store raw recordings/system artifacts in Git.
- If verified milestones change, update `STATE.json`, source-linked chunks and append-only `HISTORY.md` in a new isolated documentation Draft. Keep confidential evidence in its original restricted location.
- **Do not copy private code/data, merge, tag, publish, install, start SDR or change production services without separate specific owner approval.**

## Required output (in Turkish to the user)

Produce a compact evidence-based status report with:
- Exact source branch/commit and links actually inspected; distinguish baseline, current Draft, and installed runtime.
- Scoped PASS/HOLD for source integrity, isolated build, clean install, rollback/uninstall, native device/UI parity, security, licensing, and public release.
- Blockers grouped P0/P1/P2 and concrete next owner-safe tasks.
- Whether the local FTS index was executed/tested and whether semantic embeddings were actually generated; do not claim vectorization otherwise.
- A copyable next-session handoff, plus proposed documentation-only updates to STATE.json, KNOWLEDGE.jsonl and append-only HISTORY.md.

Never invent percentages, test results, successful hardware reception, model availability or release approval. Keep technical GitHub documentation in English.
