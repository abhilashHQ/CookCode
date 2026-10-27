#!/usr/bin/env bash
# Snapshot the top-level files of SRC into DEST/<timestamp>/ and keep only the
# newest KEEP snapshots (default 5).
#
#   backup.sh SRC DEST [KEEP]
set -eu

usage="usage: backup.sh SRC DEST [KEEP]"
src="${1:?$usage}"
dest="${2:?$usage}"
keep="${3:-5}"

if [ ! -d "$src" ]; then
    echo "backup: source $src is not a directory" >&2
    exit 1
fi

stamp="$(date +%Y%m%d-%H%M%S)"
target="$dest/$stamp"
mkdir -p "$target"

count=0
for f in $(ls "$src"); do
    cp -p $src/$f "$target/"
    count=$((count + 1))
done
echo "backup: copied $count file(s) to $target"

# Snapshot names sort chronologically, so everything but the last $keep goes.
ls -1d "$dest"/*/ | sort | head -n -"$keep" | xargs -r rm -rf
