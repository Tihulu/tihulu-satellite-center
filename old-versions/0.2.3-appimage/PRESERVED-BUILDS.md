# Preserved 0.2.3 AppImage builds — provenance and verification

These hashes were **verified by downloading each published release asset and hashing it**
(2026-11-21), not by trusting a local file.

| Asset | Size | SHA-256 (asset, verified) | Repository |
|---|---|---|---|
| `Tihulu_Satellite_Center-x86_64.AppImage` | 33.782.264 B | `21d4ad661c7aa7053e30fa5de2b112254168777067c41ae5ab1720b5d1773dc2` | `tihulu-satellite-center` |
| `Tihulu_Media_Center-x86_64.AppImage` | 44.435.960 B | `5fa5acae1bf7bc5897610db66b83ea7cbff84cd0419fb1f51a3ec2475a832fe3` | `tihulu-media-center` |
| `Tihulu_Flight_Radar-x86_64.AppImage` | 239.745.528 B | `b053b2c8aee874f399fe76b72d6b8f5feb7a1a7e4505fcc508454575aec7c827` | `tihulu-flight-radar` |

## Correction applied to these records (2026-11-21)

An earlier copy of this directory carried a `SHA256SUMS` in which the Media Center value
(`6f5f6f59…`) did **not** match the uploaded asset, and the Media Center release notes
repeated it. No binary was ever replaced: the assets were downloaded and hashed, and the
correct value above was written to both places. The two other assets matched their recorded
hashes exactly.

## Note on the Satellite Center build that was preserved

The preserved asset is the 33.782.264-byte build of `0.2.3` — the one that was running when
the 2026-11-21 hardware screenshot was taken (real NESDR SMArt v5, 0bda:2838, native
rtl_sdr). The owner's working copy in `~/tihulu old stable on pc/AppImage/` has since been
rebuilt to a different size, so **do not verify this preserved snapshot against that
directory**; verify against the values in this file, which describe the published assets.
