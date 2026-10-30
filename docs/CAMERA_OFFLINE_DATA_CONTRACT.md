# Local camera database and installed offline OSM bridge

Current requirement: runtime camera detection, selection, countdown and passage
use only the downloaded camera database, fresh GPS and installed mapd offline
maps. No Overpass/OSM map API request, new map format, separate XML export or
mandatory internet connection is part of this path. Existing user-configured
camera database and map update mechanisms remain separate maintenance actions.

## Data flow

CityGuide records supply the camera position, normalized travel direction, type,
raw speed and stable composed event ID. Installed OSM tiles supply geometry for
matching the vehicle and camera to connected directed road segments. Road speed
limits are never read by the bridge. Only the selected event, limit, distance,
validity and passage evidence are published to existing Resolver/SLA and ICBM.
Geometry is not serialized into the control messages.

The configured reaction distance remains 50–1000 m, step50, default300; the
provider's field1706 detection range does not override that preference. The
native offset applies once in Resolver. Driver priority and retained driver
speed before/after a camera remain in the existing control architecture.

## Actual downloaded camera data

The audited fixed mirror has74023 records,5306230bytes, SHA-256
`116d1748b9175ecc3a617264d2855ae4f5479121829cfb649e0e0962101c4579`.
Observed parameter keys are1709(speed),1713(beam shape),1705(azimuth),1706(range).
No road IDs, section linkage or endpoints were present. Source labels are not
unique. Existing point-camera eligibility admits59674 records; unknown types or
limits do not authorize control. Existing direction normalization is retained,
including its documented HIGH CONFIDENCE basis in matched CG/iGo exports.

## Installed map format

Read-only path: `Paths.mapd_root()/offline/{groupLat}/{groupLon}/
{minLat}_{minLon}_{maxLat}_{maxLon}`, normally under `/data/media/0/osm/offline`.
The stock files are packed Cap'n Proto with .25-degree tile bounds inside
2-degree directories. `camera_osm_bridge.capnp` locally declares the compatible
wire layout; it is not a transplanted control implementation. The schema prefix
is compatible with NikMoq/mapd v1.12.0 commit
`46cd71ade6f630f1564c83bf1763f9d949a9ff30`. Exact-source schema SHA-256:
`5033df0bb036d58b4999732586b61f063c301e1e15a90cf122a161182d0bedcd`.

Compatibility was independently checked against ARM getters in the shipped mapd
ELF (SHA-256 `fdb3b49ee19956e6ce09fdc3373cbba557f1263b2180e9f344c1d4053852284b`):
Way.Nodes uses pointer2, Way.OneWay bit328, Offline.Ways pointer0, Offline.Overlap
byte offset32. This verifies the relevant wire layout; it does not establish the
exact source commit of the shipped Go executable.

These tiles contain polylines, coordinate nodes and oneWay, but omit original
OSM way/node IDs and layer. Stable internal keys derive from geometry; connectivity
uses shared coordinates as in stock mapd. Ambiguous projections, divergent
continuations, missing tile coverage and incompatible directions do not authorize
a new camera. The missing layer data still limits assertions at geometrically
indistinguishable stacked roads; no universal road identity guarantee is made.

## Local work and load

One background thread reads the relevant neighboring installed tiles; it does
not scan the country. Before parsing, its own native TID is switched from any
inherited realtime policy to SCHED_OTHER and nice10. No main/control scheduler is
changed. Bounds and mtimes detect changes; unchanged tiles are not reparsed on
periodic checks. Motion beyond the local snapshot triggers a refresh.

Work is bounded: max16 nearby files,16 MiB per tile,32 MiB raw LRU cache,40000
local directed edges,250000 index references,256 projection cache entries and bounded directory enumeration. Only nearby geometry is
indexed. The regular source tick uses spatial indexes. Missing maps remain a
local-data diagnostic, never a reason to download another map during driving.

## Selection and passage

The source requires ordered fresh GPS fixes with the original quality checks.
A new camera requires two distinct measurements with measured forward approach.
The installed-map bridge is authoritative: its rejection is not replaced by a
point-only match. Curved-road distance follows the local directed polyline.

Passage requires an observed crossing followed by two distinct moving fixes
more than20m behind the same camera on compatible connected geometry. Repeated,
backward, missing or jumping GPS cannot prove passage. Existing event identity
is retained during uncertainty; lack of data does not restore driver speed.

The pure metadata projection path remains a unit-testable source component;
production mapd requires installed OSM geometry. Its25m camera approach corridor
is not evidence of road identity by itself. Roadside camera projection may use
25m while vehicle projection remains12m, with ambiguity rejection still applied.

HUD empty labels are «Камер нет» and «Слабый GPS». A selected camera's raw limit
is centered in the native sign, its native offset is above-right, and the valid
reaction-zone countdown is above-left. Detailed diagnostics remain in logs.

## Evidence

`CAMERA_OFFLINE_OSM_BRIDGE_QA.json` records current tests, real-tile compatibility,
native artifact hash and performance scope. `CAMERA_GRAPH_DELIVERY_QA.json`
describes the superseded online-region implementation and is historical only.
Downloaded maps/provider bytes, device GPS and generated objects are not committed.
