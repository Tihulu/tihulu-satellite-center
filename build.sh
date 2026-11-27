#!/bin/sh
# Deterministic build of the standalone source tree.
#
# Produces dist/installed-package-files.tar.gz with fixed metadata so two runs are
# byte-identical. This is a SELF-CONTAINED determinism proof for this repository; it
# does NOT claim to reproduce the private A4 preservation artifact (SHA-256
# 291b88a0...), whose original build recipe is not in this repository.
set -eu
umask 022
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$root"
mode=${1:-tar}     # tar | verify | all
fail=0

verify() {
    if [ ! -f SHA256SUMS ]; then echo "missing SHA256SUMS"; return 1; fi
    sha256sum -c SHA256SUMS --quiet || { echo "digest mismatch"; return 1; }
    count=$(wc -l < FILES.txt)
    # Bytecode is a build artefact, not payload: a developer run that forgets
    # PYTHONDONTWRITEBYTECODE=1 must not fail verification for it.
    have=$(find usr -type f -name "*.pyc" -prune -o -type f -print | wc -l)
    [ "$count" = "$have" ] || { echo "inventory mismatch: FILES.txt=$count tree=$have"; return 1; }
    while IFS= read -r name; do
        [ -f "$name" ] || { echo "FILES.txt entry missing: $name"; return 1; }
    done < FILES.txt
    echo "verify OK: $have files, digests match"
}

build_tar() {
    rm -rf dist; mkdir -p dist
    # Fixed owner/group, fixed mtime, sorted names, no directory entries.
    find usr -type f | LC_ALL=C sort | tar --owner=0 --group=0 --numeric-owner \
        --mtime=@1794216934 --format=ustar --files-from - -cf dist/installed-package-files.tar
    gzip -n -9 -c dist/installed-package-files.tar > dist/installed-package-files.tar.gz
    sha256sum dist/installed-package-files.tar.gz | tee dist/SHA256SUMS
}

case "$mode" in
    verify) verify ;;
    tar)   build_tar ;;
    all)   verify && build_tar ;;
    *) echo "usage: build.sh [verify|tar|all]"; exit 2 ;;
esac
