#!/bin/sh
# Deterministic Debian package built from this source tree.
# Root-free and reproducible: two runs produce a byte-identical .deb.
set -eu
umask 022
export SOURCE_DATE_EPOCH=1794216934
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$root"
mode=${1:-all}

stage() {
    rm -rf .packaging; mkdir -p .packaging/DEBIAN .packaging/usr
    cp -a usr/. .packaging/usr/
    cp packaging/control .packaging/DEBIAN/control
    find .packaging -exec touch -h -d @"$SOURCE_DATE_EPOCH" {} +
}

build() {
    stage
    rm -rf dist; mkdir -p dist
    dpkg-deb --root-owner-group --build .packaging \
        "dist/tihulu-satellite-center_0.3.0~preview10_all.deb" >/dev/null
    sha256sum dist/*.deb | tee dist/SHA256SUMS
}

verify_deb() {
    [ -f "dist/tihulu-satellite-center_0.3.0~preview10_all.deb" ] || { echo "no deb"; return 1; }
    count=$(dpkg-deb --contents "dist/tihulu-satellite-center_0.3.0~preview10_all.deb" | grep -c '^-')
    tree=$(find usr -type f | wc -l)
    [ "$count" = "$tree" ] || { echo "deb file count $count != tree $tree"; return 1; }
    rm -rf .extract; mkdir -p .extract
    dpkg-deb -x "dist/tihulu-satellite-center_0.3.0~preview10_all.deb" .extract
    # Compare order-insensitively: locale collation differences must not fail
    # verification when the file set and every digest are identical.
    (cd .extract && find usr -type f | LC_ALL=C sort | xargs sha256sum > ../.extract.sha)
    LC_ALL=C sort SHA256SUMS > .expected.sha
    LC_ALL=C sort .extract.sha > .actual.sha
    diff .expected.sha .actual.sha >/dev/null || { echo "extracted digests differ"; return 1; }
    rm -f .expected.sha .actual.sha
    echo "verify_deb OK: $tree files, digests identical after extraction"
}

case "$mode" in
    build) build ;;
    verify) verify_deb ;;
    all) build && verify_deb ;;
    *) echo "usage: build_deb.sh [build|verify|all]"; exit 2 ;;
esac
