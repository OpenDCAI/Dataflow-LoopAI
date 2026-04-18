#!/usr/bin/env python3
"""Download the latest published LoopAI UI dist from GitHub Releases."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


DEFAULT_REPO = "OpenDCAI/Dataflow-LoopAI"
DEFAULT_TAG_PREFIX = "ui-v"
DEFAULT_ASSET_NAMES = ("loopai-ui-dist.tar.gz", "loopai-ui-dist.zip")
ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT_DIR / "api" / "dist"


def request_json(url: str, token: str | None) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Dataflow-LoopAI-ui-release-downloader",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def download_file(url: str, target: Path, token: str | None) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "Dataflow-LoopAI-ui-release-downloader",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        with target.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def find_release(repo: str, tag_prefix: str, include_prerelease: bool, token: str | None) -> dict:
    releases = request_json(f"https://api.github.com/repos/{repo}/releases?per_page=100", token)
    if not isinstance(releases, list):
        raise RuntimeError("GitHub releases response is not a list")

    for release in releases:
        if not isinstance(release, dict):
            continue
        tag_name = str(release.get("tag_name", ""))
        if tag_prefix and not tag_name.startswith(tag_prefix):
            continue
        if release.get("draft"):
            continue
        if release.get("prerelease") and not include_prerelease:
            continue
        return release

    raise RuntimeError(f"No public release found for {repo} with tag prefix {tag_prefix!r}")


def find_asset(release: dict, asset_names: tuple[str, ...]) -> dict:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise RuntimeError("Release assets response is not a list")

    for asset_name in asset_names:
        for asset in assets:
            if isinstance(asset, dict) and asset.get("name") == asset_name:
                return asset

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        if "ui" in name and name.endswith((".tar.gz", ".tgz", ".zip")):
            return asset

    names = ", ".join(str(asset.get("name", "")) for asset in assets if isinstance(asset, dict))
    raise RuntimeError(f"No UI dist asset found in release {release.get('tag_name')}. Assets: {names}")


def extract_archive(archive_path: Path, extract_dir: Path) -> None:
    name = archive_path.name
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(extract_dir)
        return
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_dir)
        return
    raise RuntimeError(f"Unsupported archive type: {archive_path.name}")


def locate_dist_dir(extract_dir: Path) -> Path:
    candidates = [extract_dir, *extract_dir.rglob("dist")]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    for candidate in extract_dir.rglob("index.html"):
        return candidate.parent
    raise RuntimeError("Downloaded archive does not contain index.html")


def replace_dist(source_dir: Path, output_dir: Path) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_dir.with_name(f".{output_dir.name}.staging")
    backup_dir = output_dir.with_name(f".{output_dir.name}.backup")

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    shutil.copytree(source_dir, staging_dir)
    if output_dir.exists():
        output_dir.rename(backup_dir)
    staging_dir.rename(output_dir)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo in owner/name form")
    parser.add_argument("--tag-prefix", default=DEFAULT_TAG_PREFIX, help="Release tag prefix for UI builds")
    parser.add_argument(
        "--asset-name",
        action="append",
        dest="asset_names",
        help="Expected release asset name. Can be passed multiple times.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Where to install the dist files")
    parser.add_argument("--include-prerelease", action="store_true", help="Allow prerelease UI builds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    asset_names = tuple(args.asset_names or DEFAULT_ASSET_NAMES)
    output_dir = Path(args.output_dir).resolve()

    try:
        release = find_release(args.repo, args.tag_prefix, args.include_prerelease, token)
        asset = find_asset(release, asset_names)
        download_url = asset.get("browser_download_url")
        if not download_url:
            raise RuntimeError(f"Asset {asset.get('name')} has no browser_download_url")

        with tempfile.TemporaryDirectory(prefix="loopai-ui-dist-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            archive_path = temp_dir / str(asset["name"])
            download_file(str(download_url), archive_path, token)

            extract_dir = temp_dir / "extract"
            extract_dir.mkdir()
            extract_archive(archive_path, extract_dir)
            dist_dir = locate_dist_dir(extract_dir)
            replace_dist(dist_dir, output_dir)

        print(f"Installed UI {release.get('tag_name')} from {asset.get('name')} into {output_dir}")
        return 0
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"download_ui_release.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
