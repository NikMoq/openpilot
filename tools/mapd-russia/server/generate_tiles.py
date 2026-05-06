#!/usr/bin/env python3
"""
P1 Server Pipeline: Generate mapd-russia tiles with speed cameras.

Downloads OSM Russia + Rus.radar.txt, generates tiles, creates manifest.
Output format: same directory structure as original mapd for sunnypilot compatibility.

Usage:
    python generate_tiles.py --out ./tiles --auto-download
    python generate_tiles.py --out ./tiles --osm russia.osm.pbf --cameras Rus.radar.txt
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# Default sources
OSM_URL = "https://download.geofabrik.de/russia-latest.osm.pbf"
SPEEDCAM_URL = "https://speedcamonline.ru/map/Rus/Rus.radar.txt"

# Russia coverage box
COVERAGE = {"minLat": 41.0, "minLon": 19.0, "maxLat": 82.0, "maxLon": 169.0}


def download_file(url: str, dest: Path) -> None:
    """Download file with progress."""
    print(f"Downloading {url} ...")
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0))
    downloaded = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0 and downloaded % (10 * 1024 * 1024) == 0:
                    pct = downloaded / total * 100
                    print(f"  {pct:.1f}% ({downloaded // 1024 // 1024} MB / {total // 1024 // 1024} MB)")
    print(f"  Saved: {dest}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_with_mapd(osm_path: Path, cameras_path: Path, out_dir: Path, mapd_bin: Path) -> None:
    """Run mapd generate command to produce tiles."""
    cmd = [
        str(mapd_bin),
        "generate",
        "--osm", str(osm_path),
        "--cameras", str(cameras_path),
        "--out", str(out_dir),
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: mapd generate failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(result.stdout)


def create_manifest(tiles_dir: Path) -> dict[str, Any]:
    """Create manifest.json from generated tiles."""
    manifest: dict[str, Any] = {
        "version": datetime.now(timezone.utc).strftime("%Y-%m-%d-v1"),
        "generation": int(datetime.now(timezone.utc).timestamp()),
        "tiles": {},
        "coverage": COVERAGE,
    }

    for tile_file in sorted(tiles_dir.rglob("*.tar.gz")):
        rel = tile_file.relative_to(tiles_dir).as_posix()
        manifest["tiles"][rel] = {
            "hash": sha256_file(tile_file),
            "size": tile_file.stat().st_size,
        }

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="mapd-russia tile generator")
    parser.add_argument("--out", type=Path, default=Path("./tiles"), help="Output directory")
    parser.add_argument("--osm", type=Path, help="Path to russia-latest.osm.pbf")
    parser.add_argument("--cameras", type=Path, help="Path to Rus.radar.txt")
    parser.add_argument("--auto-download", action="store_true", help="Download sources automatically")
    parser.add_argument("--mapd-bin", type=Path, default=Path("./mapd"), help="Path to mapd binary")
    args = parser.parse_args()

    out_dir = args.out.resolve()
    build_dir = out_dir.with_name(out_dir.name + ".build")

    # Clean build dir
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Get OSM PBF
        if args.osm:
            osm_path = args.osm.resolve()
        elif args.auto_download:
            osm_path = tmp_path / "russia-latest.osm.pbf"
            download_file(OSM_URL, osm_path)
        else:
            print("ERROR: --osm or --auto-download required")
            return 1

        # Get cameras
        if args.cameras:
            cameras_path = args.cameras.resolve()
        elif args.auto_download:
            cameras_path = tmp_path / "Rus.radar.txt"
            download_file(SPEEDCAM_URL, cameras_path)
        else:
            print("ERROR: --cameras or --auto-download required")
            return 1

        # Generate tiles using mapd binary
        tiles_dir = build_dir / "offline"
        tiles_dir.mkdir(parents=True)
        generate_with_mapd(osm_path, cameras_path, tiles_dir, args.mapd_bin)

        # Create manifest
        manifest = create_manifest(tiles_dir)
        manifest_path = build_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        version_path = build_dir / "version.txt"
        version_path.write_text(manifest["version"] + "\n")

        # Atomic swap
        if out_dir.exists():
            backup_dir = out_dir.with_name(out_dir.name + ".old")
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            out_dir.rename(backup_dir)

        build_dir.rename(out_dir)
        print(f"\nDone. Tiles in: {out_dir}")
        print(f"Manifest: {manifest_path}")
        print(f"Version: {manifest['version']}")
        print(f"Tiles: {len(manifest['tiles'])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
