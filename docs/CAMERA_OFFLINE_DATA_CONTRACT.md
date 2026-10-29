# Camera data and independent offline road association

Implementation uses the existing CityGuide parser and versioned local camera
cache. `CameraSpeedSource` adds per-ID selection over independent road geometry;
it emits data only and does not send ACC/CAN commands. Road speed tags are never
read by this producer. Original mapd supplies road names and limits through shared
Params, not road geometry; its installed Go binary/tile format is not claimed to
be an interoperable graph source.

## Usable graph input

Acquire a complete ordinary OSM XML regional export plus its provenance sidecar at
`Paths.mapd_root()/camera_roads.osm`, normally
`/data/media/0/osm/camera_roads.osm`, or set `SpeedCameraRoadGraphPath` to its local
path. This file is data, not a list of trusted camera IDs. No graph/camera database
is committed. An OSM XML export must contain `<osm>`, one `<bounds>` with
minlat/minlon/maxlat/maxlon, all referenced `<node id lat lon>`, and `<way>` records
with `<nd ref>` and highway/oneway/junction/layer/access/motor_vehicle tags.
The `camera_roads.osm.manifest.json` sidecar records schema_version=1,
source_url, retrieved_at (Unix seconds), sha256 and complete_region_export=true.
It must describe an unfiltered OSM map bounding-box response. Use the included
offroad acquisition utility, which reads the complete response, checks its XML
and bounds, and writes the hash/time sidecar:

```sh
python -m openpilot.sunnypilot.mapd.camera_road_graph 'https://api.openstreetmap.org/api/0.6/map?bbox=MINLON,MINLAT,MAXLON,MAXLAT' /data/media/0/osm/camera_roads.osm
```

Replace the coordinate placeholders with a small valid requested region. This
explicit command makes a read-only map request; live mapd does not download it.
Preserve original bounds and all roads in the region, including competing roads;
filtering the export to a preferred road would remove evidence of ambiguity.

For a small region, the OpenStreetMap Export interface can download the ordinary
OSM XML map export. Its read API shape is
`https://api.openstreetmap.org/api/0.6/map?bbox=minlon,minlat,maxlon,maxlat`.
Choose bounds around the intended offline replay, with at least 25 m margin.
Respect service area/node limits. Larger extract formats require a separately
verified complete-region acquisition adapter; the current producer accepts only
the bounded map API contract. [OSM API description](https://wiki.openstreetmap.org/wiki/API_v0.6)
documents the bounding-box export and recommends bulk extracts for downloading.
Do not hand-author way IDs as camera permission. Acquire/update the file while
offroad, preserve its recorded acquisition metadata, and replace graph/sidecar
after download completion. Rewriting mtime cannot rejuvenate the sidecar age.

The loader rejects missing/incomplete XML, unsupported oneway values, invalid
coordinates/bounds, absent usable roads, missing/mismatched provenance, acquisition
older than 30 days, and future acquisition timestamps. It ignores modification
time for freshness. Provenance records the unfiltered download but is not a signed
attestation that the OSM service contains every real road or an authenticated
anti-tamper mechanism. Runtime exposes `SpeedCameraRoadGraphStatus`; missing graph and stale
graph are explicit causes for no new camera acquisition. XML data is read locally
at runtime; the producer makes no network request.

## Association policy and limitations

Directed segments are formed from actual OSM node connectivity and one-way tags.
Vehicle and camera positions must project within 12 m of compatible directed
segments, with heading within 35 degrees. Different nearby ways within 8 m of
the best projection cause rejection, including roads at different layers.
The forward corridor follows only a unique continuation and stops at a branch;
no route choice is invented at an interchange. Crossing roads with separate
nodes do not become connected. Curved roads use segment lengths, not radial
distance. Bounds have a 25 m exclusion margin.

This intentionally rejects some legitimate cameras, for example near junctions,
where independent topology does not uniquely identify the route. Sparse or
incorrect map geometry remains a limitation. These geometric thresholds are
conservative research policies, not validated navigation guarantees.

## Message and lifecycle contract

Existing `liveMapDataSP` warning fields remain independent. Version 1 control
fields carry raw camera speed in m/s, route distance in m, position/type and an
explicit eligibility flag. Appended fields are `speedCameraControlEventId`,
`speedCameraControlState`, `speedCameraControlReason`,
`speedCameraControlPassProven`; warning identity is `speedCameraEventId`.
`SpeedCameraEnabled` controls warnings only. `SpeedCameraControlEnabled` keeps
the independent source running even when warnings are off. Warning-only mode
works with control off; neither toggle silently enables the other.

Identity hashes provider type, source label, coordinates and directions; speed
and user offset are excluded. Distinct equal-limit cameras remain independent.
Collocated records identical in all these metadata are indistinguishable.
Source states are IDLE, APPROACHING, ACTIVE, DRIVER_OVERRIDE and PASSED. Reaction
distance accepts 50–1000 m in 50 m increments, default 300. Overrides can be
reported to the pure source by event ID; controls owns manual priority and the
actual speed ceiling. Raw speed offsets are applied once by the existing resolver.

For cameras only, the map publisher also subscribes to the raw GPS service selected
by `get_gps_location_service`. Its position/course/time tuple is used consistently;
LLK continues to supply all existing map/navigation data. New camera data requires
GPS fix, live/valid messages, LLK valid/gpsOK, horizontal accuracy 0–12 m (exclusive
zero), bearing accuracy 0–15 degrees, moving speed at least 1 m/s, finite bounded
coordinates and an ordered Unix fix no older than two seconds or in the future.
Undocumented units of LLK geodetic standard deviation are not guessed.

An invalid GPS/graph/association frame retains selected identity, sets valid
false, and never proves passage. A step over 100 m between source updates is
rejected for that update. Passage requires reliable independent road association
and an unbranched forward path from the retained camera to the vehicle, 20–1000 m
beyond it, observed on at least two distinct increasing fix timestamps. Repeated
fixes do not advance passage. ACTIVE remains latched across reaction-distance
jitter, and both supported bidirectional/second-bearing camera directions use a
retained traversal orientation. PASSED carries that camera ID and passProven=true for one publisher
generation; the consumer must latch by ID. Next update may select the next camera.
Completed IDs are suppressed for the source process session. Consumers hold any
existing ceiling during uncertainty; absence alone is never permission to restore.

## Current delivery evidence, 2026-10-29

The configured direct SpeedCamOnline CG77 endpoint returned HTTP 200 with the
complete 37-character body `Contact with me: info@speedcam.online`, not BKM.
The existing fixed fallback release was independently retrieved in memory:
[manifest](https://github.com/NikMoq/openpilot/releases/download/speedcamonline-rus/manifest.json).
Its asset was 5,306,230 bytes, 74,023 records, header `2|Radars|1251`, SHA-256
`116d1748b9175ecc3a617264d2855ae4f5479121829cfb649e0e0962101c4579`.
Actual byte count/hash matched the manifest; source acquisition timestamp was
2026-10-28 05:50:24 UTC. The original strict parser accepted all 74,023 rows;
59,674 satisfied its point-camera eligibility policy before road association.
This proves available delivery/integrity, not independently
verified provider provenance or each camera's correctness. No database was saved.

Focused offline graph/source tests cover parallel and stacked roads, branching
interchange, curved connected roads, stale/missing graph, uncertain GPS, reaction
threshold/jitter, same-limit independent IDs, route-proven two-fix pass,
duplicate/backward/stale/future fixes and jumps, raw GPS accuracy, reverse supported
bearings, acquisition provenance/hash/age, unknown limits and opposing direction.
No hardware/bench/road readiness is claimed.
