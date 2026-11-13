#!/usr/bin/env bash
# Seed python-for-android's download cache for one recipe from the first of
# several official hosts that serves the file with the pinned SHA-256, for
# a recipe that names one host alone (FreeType: savannah, which a build has
# waited 11 minutes on). python-for-android takes a download as done when
# .mark-FILE sits beside it, and skips its own. If no host serves the right
# file, this warns and leaves the download to python-for-android.
#
#   tools/seed_download.sh RECIPE FILE SHA256 URL [URL...]
set -euo pipefail
recipe=$1 file=$2 sha=$3
shift 3
archs="$(grep -E '^android.archs' buildozer.spec | sed -E 's/.*=//; s/[[:space:]]//g; s/,/_/g')"
dir=".buildozer/android/platform/build-${archs}/packages/${recipe}"
mkdir -p "$dir"
if [ -f "$dir/.mark-$file" ] && echo "$sha  $dir/$file" | sha256sum -c --quiet - 2>/dev/null; then
  echo "$recipe: $file already cached (sha256 verified)"
  exit 0
fi
for url in "$@"; do
  if curl -fsSL --retry 3 --connect-timeout 20 --max-time 300 -o "$dir/$file" "$url" &&
     echo "$sha  $dir/$file" | sha256sum -c --quiet -; then
    touch "$dir/.mark-$file"
    echo "$recipe: $file from $url (sha256 verified)"
    exit 0
  fi
  rm -f "$dir/$file" "$dir/.mark-$file"
done
echo "::warning::Could not pre-fetch $file; python-for-android will download it itself"
