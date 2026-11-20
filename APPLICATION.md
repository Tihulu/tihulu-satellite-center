# TIHULU Satellite Center — standalone source (DRAFT, public release HOLD)

Standalone mirror of the privately preserved `0.3.0~preview10` application source,
migrated at the owner's explicit request for consolidation. **Draft only: nothing here
is merged, released, or certified.** Clean install, rollback, launch, hardware and RF
acceptance are **NOT TESTED**; public release remains **HOLD**.

## What this is

- The complete `0.3.0~preview10` application package: **86 files**, byte-identical to the
  private preservation evidence. Verified: every file's SHA-256 matches the recorded
  evidence hashes (86/86, 0 mismatches). See `FILES.txt` for the exact inventory.
- **No user data.** No recordings, TLE caches, settings, credentials, IQ/WAV or local
  paths were collected or published. The upstream snapshot policy explicitly states this.
- The frozen build artifact (`installed-package-files.tar.gz`, SHA-256
  `291b88a05ec21e3becab01bca2c5c6311fba2a29673bb18317a8376570380c0a`) is **not**
  published: it remains in authorized private storage. The installer candidate that
  consumes it lives in the separate Draft PR (see coordination notes).

## Layout

    usr/bin/tihulu-satellite-center                      launcher
    usr/share/applications/tihulu-satellite-center.desktop
    usr/share/doc/tihulu-satellite-center/README.md
    usr/share/icons/hicolor/{512x512,scalable}/apps/...
    usr/share/tihulu-satellite-center/satellite_center/  application package

## Runtime requirements (from package metadata)

`python3 (>= 3.11)`, `python3-pyqt5`, `python3-pyqt5.qtwebengine`, `python3-numpy`,
`python3-skyfield`, `rtl-sdr`, `xdg-utils`.

## License obligations (must be preserved in any distribution)

- **City catalog** (`satellite_center/browser/cities-de-tr.json`): derived database under
  **Open Database License (ODbL) 1.0** — attribution and Share-Alike apply. Keep
  `satellite_center/browser/CITY_DATA_LICENSE.md` with every copy. Upstream:
  `srestre/world-countries-cities-db` -> `dr5hn/countries-states-cities-database`.
  Coordinates are approximate place points, not user GPS positions.
- **Leaflet** (`satellite_center/browser/leaflet.LICENSE.txt`): **BSD 2-Clause** — retain
  the copyright notice and disclaimer in source and binary distributions.
- **Optional online geocoding** uses Open-Meteo; its free endpoint is intended for
  non-commercial use. Commercial use requires a commercial licence and endpoint.
  Typed place names are not transmitted unless the user explicitly requests online
  suggestions.

## Build and verification

    ./build.sh verify        # inventory + every SHA256SUMS digest
    ./build.sh tar           # deterministic source archive (byte-identical across runs)
    ./build_deb.sh build     # deterministic .deb (byte-identical across runs)
    ./build_deb.sh verify    # package entry count + extraction digest comparison
    ./verify_tree.sh         # report every FILES.txt digest

See `BUILD.md` for what these prove and what they explicitly do not claim, and
`ACCEPTANCE.md` for the sandbox acceptance evidence (verified lifecycle, upgrade and
rollback, a crash soak with real SIGKILL injections) and the remaining HOLDs. CI runs
all of it in `.github/workflows/standalone-build.yml`.

## Verification

    ./verify_tree.sh     # recomputes SHA-256 for every FILES.txt entry and compares

## Safety

Never run on the owner's machine without explicit approval. Do not touch the user's
working Pop!_OS installation, the separately installed `tihulu-satellite-center`,
user-web `~/.local/bin/tihulu-satellite`, settings, recordings, TLE caches or SDR/USB
services. Install, merge, release and hardware access require separate owner approval.
