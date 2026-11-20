# Build, package and verification

    ./build.sh verify     # check every FILES.txt entry and every SHA256SUMS digest
    ./build.sh tar        # produce dist/installed-package-files.tar.gz deterministically
    ./build_deb.sh build  # produce dist/tihulu-satellite-center_0.3.0~preview10_all.deb
    ./build_deb.sh verify # package entry count + extraction digest comparison
    ./verify_tree.sh      # report every FILES.txt entry digest

## What this proves

- **Integrity:** the 86 application files match `SHA256SUMS` and `FILES.txt` exactly
  (86/86 against the preservation evidence; order-insensitive comparison).
- **Archive determinism:** two runs of `build.sh tar` produce a byte-identical archive
  (fixed uid/gid 0/0, fixed mtime, sorted names, `gzip -n`, no directory entries).
- **Package determinism:** two runs of `build_deb.sh build` produce a byte-identical
  `.deb` (`SOURCE_DATE_EPOCH` pinned, fixed owner/group and mtimes, sorted input).
- **Package fidelity:** the `.deb` file-entry count equals the 86-file tree, and the
  extracted digests match `SHA256SUMS` exactly.
- **Extracted tree runs:** CI imports `satellite_center` and runs
  `python3 -m satellite_center doctor` successfully (passive receiver detection only —
  no SDR/device access, no GUI).

CI (`standalone-build.yml`) runs the integrity checks, both determinism checks, the
package fidelity check, and the extracted-tree smoke.

## What this does NOT claim

- It does **not** reproduce the private preservation artifact
  `installed-package-files.tar.gz` (SHA-256 `291b88a05ec21e3becab01bca2c5c6311fba2a29673bb18317a8376570380c0a`).
  The original build recipe is not in this repository. The `.deb` here is **this
  repository's own** package of the same source, not that pinned binary.
- The package is **not** a release artifact: it is not tested for clean installation,
  rollback, launch acceptance, hardware or RF behaviour. Those remain **NOT TESTED**;
  see `ACCEPTANCE.md` for exactly what was verified in a sandbox, and what was not.
- Public release remains **HOLD**.
