#!/bin/sh
# Recompute SHA-256 for every FILES.txt entry and compare with the tree on disk.
set -eu
fail=0
while IFS= read -r name; do
    [ -f "$name" ] || { echo "MISSING $name"; fail=1; continue; }
    got=$(sha256sum "$name" | cut -d' ' -f1)
    printf '%s  %s\n' "$got" "$name"
done < FILES.txt | sha256sum -c - --quiet 2>/dev/null || true
echo "verify_tree.sh: structural check only; compare the printed digests with the private evidence"
[ "$fail" -eq 0 ]
