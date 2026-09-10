#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p images logs cache
export APPTAINER_CACHEDIR="$PWD/cache"
export APPTAINER_TMPDIR="${TMPDIR:-/tmp}/rexs-build-${USER}"
mkdir -p "$APPTAINER_TMPDIR"
# Resolve the checkout-relative %files source from the repository root.
cd ../..
if [[ ! -f deploy/delta/images/services.sif ]]; then
    apptainer build --fakeroot --mksquashfs-args '-processors 2' deploy/delta/images/services.sif deploy/delta/apptainer/services.def
fi
for role in redis gateway podman mirror warmup live-fire; do
    if [[ ! -f "deploy/delta/images/literegistry-${role}.sif" ]]; then
        apptainer build --fakeroot --mksquashfs-args '-processors 2' "deploy/delta/images/literegistry-${role}.sif" "deploy/delta/apptainer/${role}.def"
    fi
done
sha256sum deploy/delta/images/*.sif > deploy/delta/images/SHA256SUMS
