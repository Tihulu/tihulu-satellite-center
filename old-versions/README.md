# Old versions — preservation directory

Purpose: **preserve** installable snapshots of Satellite Center so that a future refresh or
cleanup cannot lose them. Versions may and will change; this directory is the archive.

Nothing here is certified for release. Public release remains **HOLD**; clean install,
rollback, hardware and RF acceptance remain **NOT TESTED** unless a version's own note says
otherwise.

## What is preserved

| Version | Form | Location | Size |
|---|---|---|---|
| `0.2.3` — Satellite Center | AppImage | **`tihulu-satellite-center`** release `v0.2.3-appimage` | 34 MB |
| `0.3.0` — Satellite Center (native) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-appimage` | 130 MB |
| `0.3.0` — Satellite Center (native + scheduled-job removal) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal` | 130 MB |
| `0.3.0` — Satellite Center (native + scheduled-job removal, **rev. 2**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r2` | 130 MB |
| `0.3.0` — Satellite Center (native + scheduled-job removal, **rev. 3**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r3` | 130 MB |
| `0.3.0` — Satellite Center (native + scheduled-job removal, **rev. 4**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r4` | 130 MB |
| `0.3.0` — Satellite Center (native + scheduled-job removal, **rev. 5**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r5` | 130 MB |
| `0.3.0` — Satellite Center (native + scheduled-job removal, **rev. 6**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r6` | 130 MB |
| `0.3.0` — Satellite Center (native + scheduled-job removal, **rev. 7**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r7` | 130 MB |
| `0.3.0` — Satellite Center (native + scheduled-job removal, **rev. 8**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r8` | 130 MB |

> Use **rev. 8**: the earliest job-removal build could clear a `running` RF capture (losing the record and its result) and lacked removal controls on the HTTP serve mode and the Qt-widgets desktop fallback; rev. 2 fixed both; rev. 3 confirmed the destructive bulk clear; rev. 4 fixed the Qt fallback's removal result message being erased by the calendar render; rev. 5 added the cache-buster on the removal module import; rev. 6 made the standalone web path accept the same terminal states as the backend; **rev. 7** closes the independent review's findings (no half-removal on a failed save, no silent no-op, consent that cannot under-report, and a test that keeps the lock discipline); **rev. 8** closes the verification pass's watch items (the rollback covers every failure kind, a repaint can no longer erase the live-record message, a failed refresh is no longer reported as a failed removal, and a single-record removal reports its outcome).
| `0.2.3` — Flight Radar | AppImage | **`tihulu-flight-radar`** release `v0.2.3-appimage` | 230 MB |
| `0.2.3` — Media Center | AppImage | **`tihulu-media-center`** release `v0.2.3-appimage` | 42 MB |
| `0.2.3` — AppImage metadata | hashes + run notes | `old-versions/0.2.3-appimage/` | < 4 KB |
| `0.3.0~preview10` — frozen package | `.tar.gz` + exact inventory + package metadata | `old-versions/0.3.0-preview10/` | 692 KB |
| `0.3.0~preview10` — hardened installer candidate | source + 5 regression suites + CI workflow | `old-versions/0.3.0-preview10/candidate/` | ~150 KB |

Each app's binary lives in **its own repository's release** (Satellite Center here, Flight
Radar in `tihulu-flight-radar`, Media Center in `tihulu-media-center`), so a snapshot is
never trapped in another project's repository.

| Asset | Repository | Release tag |
|---|---|---|
| `Tihulu_Satellite_Center-x86_64.AppImage` (34 MB, `__version__` 0.2.3) | `tihulu-satellite-center` | `v0.2.3-appimage` |
| `Tihulu_Satellite_Center-x86_64.AppImage` (130 MB, native `0.3.0`) | `tihulu-satellite-center` | `v0.3.0-preview10-appimage` |
| `Tihulu_Satellite_Center-0.3.0-job-removal-x86_64.AppImage` (130 MB, native `0.3.0` + scheduled-job removal) | `tihulu-satellite-center` | `v0.3.0-preview10-job-removal` |
| `Tihulu_Flight_Radar-x86_64.AppImage` (230 MB) | `tihulu-flight-radar` | `v0.2.3-appimage` |
| `Tihulu_Media_Center-x86_64.AppImage` (42 MB) | `tihulu-media-center` | `v0.2.3-appimage` |

The 0.2.3 Satellite Center build is the one shown in the 2026-11-21 hardware screenshot
(real NESDR SMArt v5, 0bda:2838, native rtl_sdr).

### Why the AppImages live in a Release, not this directory
Git cannot store files larger than 100 MB, so the 230 MB `Tihulu_Flight_Radar` AppImage
cannot be a directory file. GitHub Release assets support up to 2 GB, so each binary is
attached to a release **in its own repository** and linked from the table above.

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
