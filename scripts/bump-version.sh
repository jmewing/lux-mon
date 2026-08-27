#!/bin/bash
# Determine the next semantic version for lux-mon from commits since the last
# tag, and (optionally) write it back to api/__init__.py.
#
# Bump rules (Conventional Commits):
#   feat(inverter): ...   -> MAJOR  (new inverter support)
#   feat: ...             -> MINOR  (new feature)
#   feat(scope): ...      -> MINOR  (new feature, non-inverter scope)
#   fix:/chore:/docs:/refactor:/perf:/test:/ci: -> PATCH (bug fix / maintenance)
#   BREAKING CHANGE / !   -> MAJOR
#
# Usage:
#   scripts/bump-version.sh            # print the next version (dry run)
#   scripts/bump-version.sh --write    # also update api/__init__.py
#   scripts/bump-version.sh --bump major|minor|patch  # force a specific bump
#
# Exit codes:
#   0  next version determined (printed to stdout)
#   1  no commits since last tag (nothing to release)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$REPO_DIR/api/__init__.py"

WRITE=0
FORCE_BUMP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write) WRITE=1 ;;
    --bump) FORCE_BUMP="$2"; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

# ── Current version (file) ─────────────────────────────────────────────────
FILE_VERSION="$(grep -oE 'version="[0-9]+\.[0-9]+\.[0-9]+"' "$VERSION_FILE" | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
if [[ -z "$FILE_VERSION" ]]; then
  echo "Could not parse version from $VERSION_FILE" >&2
  exit 2
fi

# ── Last tag version ───────────────────────────────────────────────────────
LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
TAG_VERSION=""
if [[ -n "$LAST_TAG" ]]; then
  TAG_VERSION="$(echo "$LAST_TAG" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
fi

# The authoritative base is the higher of the file version and the last tag
# version. This reconciles the historical drift where tags were created without
# bumping the file (e.g. v1.1.4 tagged while the file still said 1.1.3).
version_ge() {
  # version_ge A B  -> 0 if A >= B, 1 otherwise
  local a="$1" b="$2"
  [[ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -1)" == "$a" ]]
}
BASE="$FILE_VERSION"
if [[ -n "$TAG_VERSION" ]] && version_ge "$TAG_VERSION" "$BASE"; then
  BASE="$TAG_VERSION"
fi
IFS='.' read -r MAJOR MINOR PATCH <<< "$BASE"

# ── Determine bump from commits since last tag ─────────────────────────────
if [[ -n "$FORCE_BUMP" ]]; then
  BUMP="$FORCE_BUMP"
elif [[ -z "$LAST_TAG" ]]; then
  # No tags yet: treat the whole history as the first release.
  RANGE="HEAD"
  BUMP="minor"
else
  RANGE="${LAST_TAG}..HEAD"
  BUMP="patch"
fi

# Inspect commit subjects in the range to decide the bump level.
if [[ -z "$FORCE_BUMP" ]]; then
  # Highest-priority bump wins: major > minor > patch.
  while IFS= read -r subject; do
    # BREAKING CHANGE or trailing "!" -> major
    if [[ "$subject" =~ BREAKING[[:space:]]CHANGE || "$subject" =~ !: ]]; then
      BUMP="major"
      break
    fi
    # feat(inverter) -> major (new inverter support)
    if [[ "$subject" =~ ^feat\(inverter\) ]]; then
      BUMP="major"
      break
    fi
    # feat -> minor (new feature)
    if [[ "$subject" =~ ^feat ]]; then
      BUMP="minor"
    fi
  done < <(git log --format='%s' "$RANGE" 2>/dev/null || true)
fi

# ── Compute next version ────────────────────────────────────────────────────
case "$BUMP" in
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  patch) PATCH=$((PATCH + 1)) ;;
  *) echo "Invalid bump: $BUMP" >&2; exit 2 ;;
esac

NEXT="${MAJOR}.${MINOR}.${PATCH}"

# ── Write back (optional) ───────────────────────────────────────────────────
if [[ "$WRITE" -eq 1 ]]; then
  sed -i -E "s/version=\"[0-9]+\.[0-9]+\.[0-9]+\"/version=\"${NEXT}\"/" "$VERSION_FILE"
  echo "Updated $VERSION_FILE -> ${NEXT}" >&2
fi

echo "$NEXT"
