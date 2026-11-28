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
| `0.3.0` — Satellite Center (native + scheduled-job removal, **rev. 9**, orbit deps bundled) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r9` | ~152 MB |
| `0.3.0` — Satellite Center (native + scheduled-job removal + **per-job recording folder and analysis drag & drop**, **rev. 10**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r10` | ~152 MB |
| `0.3.0` — Satellite Center (native + per-job recording folder + analysis drag & drop + **pending-job edit**, **rev. 11**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r11` | ~152 MB |
| `0.3.0` — Satellite Center (native + per-job recording folder + analysis drag & drop + pending-job edit + **default recording folder**, **rev. 12**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r12` | ~152 MB |
| `0.3.0` — Satellite Center (same feature set as rev. 12, **corrected packaging**, **rev. 12.3**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r12.3` | ~152 MB |
| `0.3.0` — Satellite Center (same feature set, **default folder consolidated**, **rev. 12.4**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r12.4` | ~152 MB |
| `0.3.0` — Satellite Center (same feature set plus a **native folder picker**, **rev. 12.5**) | AppImage | `tihulu-satellite-center` release `v0.3.0-preview10-job-removal-r12.5` | ~152 MB |

> Use **rev. 12.5**: the earliest job-removal build could clear a `running` RF capture (losing the record and its result) and lacked removal controls on the HTTP serve mode and the Qt-widgets desktop fallback; rev. 2 fixed both; rev. 3 confirmed the destructive bulk clear; rev. 4 fixed the Qt fallback's removal result message being erased by the calendar render; rev. 5 added the cache-buster on the removal module import; rev. 6 made the standalone web path accept the same terminal states as the backend; **rev. 7** closes the independent review's findings (no half-removal on a failed save, no silent no-op, consent that cannot under-report, and a test that keeps the lock discipline); **rev. 8** closes the verification pass's watch items (the rollback covers every failure kind, a repaint can no longer erase the live-record message, a failed refresh is no longer reported as a failed removal, and a single-record removal reports its outcome); **rev. 9** bundles the Python dependencies the AppImage was missing (skyfield, numpy and friends) — without them orbit calculation could not run inside the AppImage at all, which is why it is ~152 MB where earlier revisions were ~130 MB; **rev. 10** adds the owner-requested per-job recording folder (a scheduled capture writes into a chosen folder, validated to stay inside the app data directory or home with no `..` or symlink escape; the desktop pass planner picks the folder, the web form takes a subdirectory name) and drag & drop of real recordings onto the analysis sections on both the desktop Qt window and the web UI (a dropped file behaves exactly like the same file chosen with that section's own picker); **rev. 11** adds in-place editing of a pending schedule from the armed-jobs list (start time and duration; the Qt calendar button, the web card form and the Qt WebChannel shell all use one Station check: inside the predicted pass, before the LOS margin, no receiver overlap, disk reserve re-checked, and the record rolled back if the save fails). **rev. 12** adds a global default recording folder (set once, applied to all future schedules unless a job overrides it). The published rev. 12 AppImage does not start at all: its squashfs has no root `AppRun`, so it fails with an execv error before any interface appears. It is listed here to preserve the tag, not as a usable build; use **rev. 11** until a corrected AppImage is published. A corrected local build has been verified against the bundled Python, the Qt WebEngine desktop shell and every suite, but it has not been published or independently reviewed.  While that work was in progress, the earlier README edit had already corrupted the preservation table and still pointed readers at a build that cannot start. The published rev. 12 AppImage does not start at all (no root `AppRun`, execv error before any interface appears), so the tag is preserved for provenance only and its release note now carries that warning; **rev. 12.3** is the corrected, separately identified build of the same feature set. It also fixes the default-folder 400 error path, adds the default-folder controls to the native Qt WebEngine desktop shell and makes recording-path resolution idempotent, and it was taken through the bundled Python, the real Qt WebEngine shell and every suite in an isolated HOME and data directory before publication.  **rev. 12.4** consolidates that work: the persisted default folder is re-validated on load (a stale or tampered value can no longer silently redirect captures), and the standalone Web/PWA page now states why it cannot offer the default folder instead of showing nothing, while the installed app and the local companion keep the real controls. Rev. 12.3 remains usable; rev. 12.4 simply supersedes it. **rev. 12.5** adds a native folder picker to the desktop shell (a `Choose folder...` button that opens the OS dialog and fills the field) and stops the packaged launcher from changing the working directory, so recording-folder pickers open in the operator own folders instead of inside the AppImage mount directory. That second point is what the owner hit directly: earlier pickers started in /tmp/.mount_..., where nothing useful can be chosen.
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
