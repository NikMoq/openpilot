# mapd-russia — Russian Speed Camera Support for sunnypilot

This project adds Russian speed camera support (from speedcamonline.ru) to sunnypilot via a fork of `mapd`.

## Architecture

- **OSM ways** provide map-matching and bearing assignment
- **CameraTile layer** merges at runtime only
- Cameras without matched OSM way bearing are discarded
- Mobile cameras degrade after 3h (confidence ×0.3), expire after 24h

## Stages

| Stage | Status | Description |
|-------|--------|-------------|
| P0 | ✅ Done | Offline schemas + runtime (fork commit `bd5e0899c`) |
| P1 | ✅ Done | Server pipeline + GitHub Actions (fork commits `74dad0726`, `aa653e3fa`) |
| P2 | ✅ Done | CI/CD smoke test (fork commit `d6da1ad26`) |
| P3 | 🔄 Ready | Deploy to Comma |

## Files

```
tools/mapd-russia/
├── README.md                     # This file
├── README-P3-DEPLOY.md           # Detailed deploy instructions
├── deploy-to-comma.sh            # Automated deploy script
├── server/
│   ├── generate_tiles.py         # Tile generation pipeline
│   └── serve.py                  # Local HTTP server
├── fork-p0/                      # Stage P0 files
│   ├── cereal/
│   ├── maps/
│   └── state_camera.patch
├── fork-p1/                      # Stage P1 files
│   ├── cmd/generate.go
│   └── settings/download_release.go
├── fork-p2/                      # Stage P2 files
│   ├── Dockerfile
│   └── validate-tiles.sh
└── .github/workflows/
    ├── generate-tiles.yml        # Weekly tile generation
    └── smoke-test.yml            # CI smoke test
```

## Quick Deploy

```bash
# 1. Make sure NikMoq/mapd fork is tagged v1.12.1-rus.1
# 2. Run GitHub Actions to generate tiles release
# 3. Update sunnypilot code (this repo)
# 4. Deploy to Comma

cd tools/mapd-russia
./deploy-to-comma.sh comma.local
```

## sunnypilot Changes (P3)

| File | Change |
|------|--------|
| `sunnypilot/mapd/mapd_installer.py` | URL → `NikMoq/mapd`, `update_tiles_version()` |
| `sunnypilot/mapd/live_map_data/osm_map_data.py` | `get_next_camera()` reads from Params |

## How It Works on Device

1. **mapd** runs as daemon, downloads tiles on-demand from GitHub Releases
2. **state.go** (in fork) calls `InitCameraIndex()` after tile load
3. **FindCameraAhead()** searches 9-grid, filters bearing ±45°, distance <2km
4. **Result** written to Params key `NextCamera` as JSON
5. **sunnypilot** reads via `osm_map_data.get_next_camera()`

## Params Keys

| Key | Source | Description |
|-----|--------|-------------|
| `MapdVersion` | mapd_installer | Installed mapd binary version |
| `MapdRussiaTilesVersion` | mapd_installer | Installed tiles version |
| `NextCamera` | mapd (fork) | Next camera ahead (JSON) |

## Camera JSON Format

```json
{
  "type": "stationary",
  "distance": 450.5,
  "speedLimit": 60.0,
  "confidence": 0.95,
  "isGroup": false
}
```

Types: `stationary`, `mobile`, `tripod`, `avtodoria`

## Support

- speedcamonline.ru data format: CityPlan (cp1251)
- Coverage: Russia (OSM extract)
- Update frequency: Weekly (Sunday 3:00 UTC via GitHub Actions)
