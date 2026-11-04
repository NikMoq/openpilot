# Waze cameras, mobile reports and posted road limits

Branch `waze` starts at `stable` commit
`43f97a991c341c47dfb0b147acbefff0ce193554`.
Waze is phase 1; Yandex Navigator is reserved for phase 2. The provider block
retains the custom URL editor, adds an independent Waze toggle, and shows a
disabled Yandex Navigator row marked PHASE 2. The new controls have Russian
translations.

## Operation

Enable **Waze cameras and posts** in the speed-camera settings (default off).
The existing global camera and automatic-control settings still apply.
Acquisition requires a localized GPS position and internet access. Fixed cameras
refresh every 120 seconds, mobile reports every 60 seconds, or after 1 km of
movement, within a 5 km request radius. Network work runs on a background worker,
not the map publisher thread. Status, report count and errors appear in settings.

Sources are Waze throughout:

- Fixed cameras: public ROW map tiles with validated binary structure, complete
  requested-tile coverage, original categories and stable identities.
- Mobile reports: Waze ROW guest RT session, with additions/removals, affinity
  cookies, bounded full refresh and original observation/publication timestamps.
- Posted limits and directed geometry: the editor's ordinary anonymous Practice
  mode (`Features` with `sandbox=true`). No personal Waze account is required by
  the verified acquisition path.

State lives under `Paths.mapd_root()/waze`, outside the source checkout. RT guest
credentials are private state with restricted permissions; logs and committed
fixtures contain none. Ambiguous registration is not automatically repeated,
and rejected guest credentials are not silently replaced.

## Applicability and controls

The mobile report's facing does **not** exclude a police post, tripod or portable
camera. Its warning remains visible from both approaches. Road limits are chosen
from the legal directions of the matched road: equal two-way limits produce one
both-direction candidate; different limits produce separate approach candidates.
One-way roads have the single legal approach. For a divided street, a proven
nearby opposite one-way carriageway receives its own control point and posted
limit: both roads must share a Waze primary street ID, have opposite parallel
legal traffic, interior projections and fresh usable evidence, with no extra
nearby road. The opposite carriageway must be within 40 m. The physical report
coordinates remain unchanged. These are road applicability directions, not a
claim about which way a police officer or camera can observe.

A report must match an unambiguous Waze road, with explicit km/h units and a
fresh posted limit. Parallel roads, unresolved restrictions, missing limits and
ambiguous intersections remain warnings. Fixed camera associations can also use
Waze's explicit camera-to-segment link. Other road events remain in the raw
snapshot; closures, congestion and accidents are not mislabeled as cameras.

Qualified candidates enter the existing camera pipeline. Existing localized
trajectory, installed offline road geometry, route association, reaction distance,
driver offset and passage checks remain required for automatic control. Waze
supplies the camera/report and posted limit; the existing offline OSM bridge
provides geometry only, not replacement cameras or limits. Acquisition eligibility
alone is not an instruction to brake.

The previous driver setting is restored through the existing proven-passage
logic. Report deletion, expiry or loss of road evidence cannot invent a passage
or prematurely release an active ceiling. Existing Russian cameras retain their
source policy and are merged without being overwritten by nearby unknown-limit
Waze warnings.

Mobile observations expire after at most 180 seconds; fixed observations after
12 hours. Independent road-limit evidence expires after 5 minutes. Cache reads
never rejuvenate source timestamps. Bulk JSON writes and fsync do not block the
publisher's snapshot lock. Disabling Waze discards in-flight late publication.

## Source evidence

Waze's official editor SDK exposes forward/reverse limits and directed geometry:
https://www.waze.com/editor/sdk/interfaces/index.SDK.Segment.html
The official update contract specifies km/h:
https://www.waze.com/editor/sdk/classes/index.SDK.Segments.html#updateSegment
Current WME v2.378 maps those SDK fields directly to `fwdMaxSpeed` and
`revMaxSpeed`, without unit conversion. Its standard Practice request builder
adds `sandbox=true`; the client independently implements this read-only path.

The same normal `Features` request without Practice mode returned authenticated
API error 100 (HTTP 403), while the ordinary Practice request returned HTTP 200
without cookies, tokens or a personal account. The production client succeeds
with its honest SunnyPilot user agent. This resolves the road-limit acquisition
gap without relying on the Live Map GeoRSS endpoint, which still returns 403.

Tile camera speed bytes retain their original values: their threshold semantics
are not assumed to be the separately verified posted road limit. WME camera
records can have `speedLimit=null` while their associated segment has a valid
posted limit. Complete request-window coverage does not prove a complete national
camera inventory or that every camera is known to Waze.

## Verification evidence, 2026-11-04

Live production Python client checks in Germany:

- Berlin, 3 km requested radius: 54/54 fixed tiles, 11 camera records. Explicit
  segment links and geometric matching qualified 8 road limits; other records
  retained explicit ambiguity/restriction reasons. Example camera 356390 is explicitly linked by
  Waze to segment 272499027, reverse legal direction, 50 km/h.
- Munich, 3 km requested radius: 54/54 fixed tiles, 13 camera records. Explicit
  segment links and geometric matching qualified 6 road limits. Camera 875680 is
  explicitly linked to segment 149671789, reverse legal direction, 50 km/h.
- Frankfurt, verified again at the production 5 km request radius: 69 RT events, including **4 genuine
  POLICE_WITH_MOBILE_CAMERA reports**. Three matched fresh Waze road limits:
  30, 30 and 40 km/h; the fourth remained ambiguous.
- Berlin public road response: 156 segments, 104 usable with posted limits;
  332819 bytes, SHA-256
  `06e34b3d47681da094080131fff79818006494867d995c3a993a160ba46cd93d`.
  Sanitized public-road and tile fixtures include provenance in the tests/package.

Offline tests cover transport bounds, cookies/session reuse, binary parsing,
Practice JSON/units, directed matching, stale and ambiguous evidence, persistence,
source merging, disable races and the actual serialized control path. Synthetic
mobile fixtures exercise 50+15 and 70+15 km/h on opposite approaches, then restore
the driver's 100 km/h only after proven passage. Synthetic traces are identified
as such; live report acquisition is tested separately.

Final Linux/Python 3.12 Docker suite: **1038 passed, 9 skipped**. Ruff and
`git diff --check` pass. `tools/tests/run_camera_checks.ps1` includes all Waze
tests alongside the existing camera and CityGuide tests. The native Qt provider
preview and Russian translation compilation were also verified in Docker; this
is separate from compiling and running the full vehicle UI.

No hardware deployment, bench run or road validation was performed. Native UI
compilation and operation on a physical Comma remain unverified. No controller
acceleration/deceleration algorithms or safety limits are changed.
