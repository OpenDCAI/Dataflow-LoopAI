#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCS_DIR="$ROOT_DIR/tutorial"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/release_docs.sh [version]

Examples:
  scripts/release_docs.sh
  scripts/release_docs.sh 0.1.0

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
  read -r -p "Enter docs release version, for example 0.1.0: " version
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

current_version="$(node -p "require('$DOCS_DIR/package.json').version")"

tag="doc-v$version"
if git -C "$ROOT_DIR" rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  echo "Tag already exists locally: $tag" >&2
  exit 1
fi

echo
echo "About to release docs version $version"
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

cd "$DOCS_DIR"
package_changed=false
if [[ "$current_version" != "$version" ]]; then
  npm version --no-git-tag-version "$version"
  package_changed=true
else
  echo "tutorial/package.json is already at version $version; skipping version bump."
fi

cd "$ROOT_DIR"
if [[ "$package_changed" == true ]]; then
  git add tutorial/package.json
  git commit -m "chore(docs): release $version"
fi
git tag "$tag"
git push "$REMOTE" "$BRANCH" "$tag"
