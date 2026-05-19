"""
Central version management for mapd integration.
Single source of truth for all mapd-related versions.
"""

# Mapd binary version
MAPD_VERSION = "v2025.01.0"

# Mapd tileset version (YYYY.MM format)
MAPD_TILESET_VERSION = "2025.01"

# GitHub repository (standard source)
MAPD_REPO_OWNER = "pfeiferj"
MAPD_REPO_NAME = "mapd"

# Release URLs
MAPD_RELEASE_BASE_URL = f"https://github.com/{MAPD_REPO_OWNER}/{MAPD_REPO_NAME}/releases/download/{MAPD_VERSION}"
MAPD_ARM64_ARCHIVE = "mapd-linux-arm64.tar.gz"
MAPD_NATIVE_ARCHIVE = "mapd-native.tar.gz"

# Tiles release
TILES_RELEASE_TAG = f"{MAPD_TILESET_VERSION}"
TILES_RELEASE_BASE_URL = f"https://github.com/{MAPD_REPO_OWNER}/{MAPD_REPO_NAME}/releases/download/{TILES_RELEASE_TAG}"
TILES_ARCHIVE = "mapd-tiles.tar.gz"

# Expected checksums (updated with each release)
MAPD_CHECKSUMS = {
    "v2025.01.0": {
        "mapd-linux-arm64.tar.gz": "",
        "mapd-native.tar.gz": "",
    }
}
