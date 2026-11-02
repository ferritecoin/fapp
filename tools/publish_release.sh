#!/usr/bin/env bash
# Put FILE... on the GitHub Release of this build's version: what each CI
# job that builds a deliverable runs (.github/workflows/build-apk.yml). The
# Android job and the Windows job run at once, and each publishes its own
# files; whichever comes first makes the Release.
#
#   GH_TOKEN=… GITHUB_REPOSITORY=… GITHUB_SHA=… RUN_NUMBER=… \
#     tools/publish_release.sh release/Ferritext-15.1.1.apk …
#
# Each version is published once. A re-run of the commit it was published
# from refreshes its files; a later commit that kept the version leaves it
# as it is, so a version's files never change once people have them.
set -euo pipefail

VER="$(grep -E '^version' buildozer.spec | head -1 | sed -E 's/.*=[[:space:]]*//')"
TITLE="$(grep -E '^title' buildozer.spec | head -1 | sed -E 's/.*=[[:space:]]*//')"
TAG="v${VER}"
NOTES="${TITLE} ${VER} (CI run #${RUN_NUMBER}, commit ${GITHUB_SHA:0:7}). \
${TITLE}-${VER}.apk: Android — sideload via 'Install unknown apps'. \
${TITLE}-${VER}-windows.exe: Windows 10 and 11 (64-bit), nothing else to \
install — Windows may say it does not know the publisher: More info, then \
Run anyway. ${TITLE}-${VER}-desktop.py: the same client for Windows, macOS \
and Linux in one file, with its fonts and icons inside — run it with Python \
3.11+ (see the README for the packages it needs)."

if gh release view "$TAG" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
  TAGGED="$(gh api "repos/$GITHUB_REPOSITORY/commits/$TAG" --jq .sha 2>/dev/null || true)"
  if [ -n "$TAGGED" ] && [ "$TAGGED" != "$GITHUB_SHA" ]; then
    echo "::notice::$TAG is already published, from ${TAGGED:0:7}, and stays as it is. Raise the version in buildozer.spec and main.py to publish this build."
    exit 0
  fi
  gh release edit "$TAG" --repo "$GITHUB_REPOSITORY" \
    --title "$TITLE $TAG" --notes "$NOTES"
elif ! gh release create "$TAG" --repo "$GITHUB_REPOSITORY" \
    --target "$GITHUB_SHA" --title "$TITLE $TAG" --notes "$NOTES"; then
  # The other job made it a moment ago: then it is there to add to.
  gh release view "$TAG" --repo "$GITHUB_REPOSITORY" >/dev/null
fi
gh release upload "$TAG" "$@" --repo "$GITHUB_REPOSITORY" --clobber
echo "Published to release $TAG:"; ls -la "$@"
