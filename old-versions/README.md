# Old versions — preservation directory

Purpose: **preserve** installable snapshots of Satellite Center so that a future refresh or
cleanup cannot lose them. Versions may and will change; this directory is the archive.

Nothing here is certified for release. Public release remains **HOLD**; clean install,
rollback, hardware and RF acceptance remain **NOT TESTED** unless a version's own note says
otherwise.

## What is preserved

| Version | Form | Location | Size |
|---|---|---|---|
| `0.2.3` — portable AppImage | AppImage set of three apps | **GitHub Release `v0.2.3-appimage`** (assets) | 34 + 230 + 42 MB |
| `0.2.3` — metadata | `SHA256SUMS`, `RUN.txt`, `MANIFEST.txt` | this directory | < 4 KB |
| `0.3.0~preview10` — frozen package | `.tar.gz` + exact inventory + package metadata | this directory | 692 KB |
| `0.3.0~preview10` — hardened installer candidate | source + 5 regression suites + CI workflow | `candidate/` | ~150 KB |

### Why the AppImages live in a Release, not this directory
Git cannot store files larger than 100 MB, so the 230 MB `Tihulu_Flight_Radar` AppImage
cannot be a directory file. GitHub Release assets support up to 2 GB, so all three
AppImages are attached to the release `v0.2.3-appimage` and linked from here:

- `Tihulu_Satellite_Center-x86_64.AppImage` (34 MB) — version 0.2.3, the build shown in the
  2026-11-21 hardware screenshot (real NESDR SMArt v5, 0bda:2838, native rtl_sdr)
- `Tihulu_Flight_Radar-x86_64.AppImage` (230 MB)
- `Tihulu_Media_Center-x86_64.AppImage` (42 MB)

Download link: `<repo>/releases/tag/v0.2.3-appimage`

## Install / run

### AppImage (no installation required)

    chmod +x Tihulu_Satellite_Center-x86_64.AppImage
    ./Tihulu_Satellite_Center-x86_64.AppImage

AppImage bundles its own portable Python and libraries; it does not install anything to
system paths and does not touch an existing installation. See `0.2.3-appimage/RUN.txt`.

### Frozen package `0.3.0~preview10`

The package is the same release the hardened candidate installs. To install it in an
isolated namespace (unique launcher name, own runtime, never overwrites an existing
installation):

    cd candidate
    python3 installer.py install \
      --archive ../installed-package-files.tar.gz \
      --manifest ../files.txt
    python3 installer.py status
    python3 installer.py doctor     # read-only dependency probe, never opens SDR
    python3 installer.py uninstall  # logical: bytes retained in a bounded vault

### Hardened candidate

`candidate/` holds the installer candidate and its regression suites. Exact-head CI for
this content: SUCCESS 2/2 jobs, 78/78 tests. It is **Draft / HOLD**.

## Verify a preserved snapshot

    # AppImage set (against the values recorded at preservation time)
    sha256sum -c 0.2.3-appimage/SHA256SUMS

    # frozen package
    cd 0.3.0-preview10 && sha256sum -c MANIFEST.txt
    cd candidate && sha256sum -c ../MANIFEST-candidate.txt

    # the package inventory is exactly 86 files:
    wc -l < 0.3.0-preview10/files.txt

## Privacy

No user data was collected or published: no recordings, TLE caches, settings, credentials,
IQ/WAV files, local paths or owner-host state. The frozen package is application source
only (86 files). Licence obligations are carried in-tree: ODbL 1.0 city catalog with
attribution, BSD 2-Clause Leaflet, Open-Meteo non-commercial terms.

## Provenance

- `0.2.3` AppImage set: built 2026-11-21 from the owner's portable distribution directory
  (`~/tihulu old stable on pc/AppImage/`), hashes recorded in `0.2.3-appimage/SHA256SUMS`.
- `0.3.0~preview10`: privately preserved candidate snapshot
  `tihulu-satellite-stable-first-20261112T184840Z-UKggjN`, evidence run
  `20261112T190206Z-ff7172`; frozen archive SHA-256
  `291b88a05ec21e3becab01bca2c5c6311fba2a29673bb18317a8376570380c0a`.
