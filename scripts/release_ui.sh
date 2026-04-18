#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UI_DIR="$ROOT_DIR/ui"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/release_ui.sh [version]

Examples:
  scripts/release_ui.sh
  scripts/release_ui.sh 0.1.0

Environment:
  REMOTE=origin  Git remote to push to
  BRANCH=main    Branch to require and push
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

version="${1:-}"
if [[ -z "$version" ]]; then
  read -r -p "Enter UI release version, for example 0.1.0: " version
fi

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
  echo "Invalid version: $version" >&2
  echo "Expected a semver-like version such as 0.1.0 or 0.1.0-beta.1" >&2
  exit 1
fi

current_branch="$(git -C "$ROOT_DIR" branch --show-current)"
if [[ "$current_branch" != "$BRANCH" ]]; then
  echo "Current branch is '$current_branch', but this release script expects '$BRANCH'." >&2
  echo "Set BRANCH=$current_branch if you really want to release from this branch." >&2
  exit 1
fi

tag="ui-v$version"
if git -C "$ROOT_DIR" rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  echo "Tag already exists locally: $tag" >&2
  exit 1
fi

echo
echo "About to release UI version $version"
echo "  Branch: $BRANCH"
echo "  Remote: $REMOTE"
echo "  Tag:    $tag"
echo
read -r -p "Continue? [y/N] " confirm
case "$confirm" in
  y|Y|yes|YES) ;;
  *)
    echo "Canceled."
    exit 0
    ;;
esac

cd "$UI_DIR"
if command -v corepack >/dev/null 2>&1; then
  corepack enable
fi
yarn version --immediate "$version"

cd "$ROOT_DIR"
git add ui/package.json ui/yarn.lock
git commit -m "chore(ui): release $version"
git tag "$tag"
git push "$REMOTE" "$BRANCH" "$tag"
