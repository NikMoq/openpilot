# Speed cameras / ICBM — 2026-10-26

## Scope and deployment

Active source: SpeedCamOnline, all camera types/all Russian regions,
https://speedcamonline.ru/cg77/Rus/comment/ . This replaces the old APK source.
Code and ARM UI are published on dev in NikMoq/openpilot at
a5efa5767191cb077814e563651326bc900f599e. Installation remains a user action
through the device Software UI; publication does not confirm installation.

SSH remains diagnostics-only. User installs code through Software UI. No
Panda/EPS firmware, safety-limit or stable-branch changes are included.
DragonPilot remains a UX reference, not evidence of PQ35 command acceptance.

## Download diagnosis and autonomous delivery

The full live source was downloaded with curl and the production Python
transport (22/27 seconds). It matches the original user-supplied BKM exactly:
5,305,735 bytes, 74,016 records, SHA256
0ccaba30044729081e36cc006817d81501ad52dbe9eaf2ddbf35aa5751482c6c.
The original file remains unchanged, outside Git.

The PC proxy configuration routes .ru domains DIRECT even though an IP-check
site shows foreign egress. The SCO server allocation is HETZNER-fsn1-dc10/DE.
A request-specific proxy CONNECT to the server IP, retaining the original TLS
name/certificate checks, completed the transfer. Earlier direct PC/Comma routes
stalled. No VPN, ACL, credentials or network configuration was changed.
The exact downstream network cause of that route's interruption is not proven.

Product delivery tries the exact SCO default directly for up to 30 seconds,
then uses a validated copy from the SAME source at the fixed repository release:
https://github.com/NikMoq/openpilot/releases/tag/speedcamonline-rus .
Custom URLs remain direct-only. Total network budget is 120 seconds; stdlib
DNS/trust-store loading and CPU/disk work are not preemptible.

The initial release contains a fresh, live-downloaded BKM. A CI producer validates
all records, publishes immutable hash-named BKM assets, verifies uploaded bytes,
and switches manifest last with rollback. Scheduled refresh is every six hours.
GitHub schedules require the workflow on default branch res; only that service
workflow was added there at 169ad7dcc98f77474415768138ada99ef732f092.
Producer/application source remains on dev. The first GitHub Actions run passed
source download, validation and publication independently of the laptop:
https://github.com/NikMoq/openpilot/actions/runs/40343842489 .

The client checks source identity, timestamp (maximum 7 days, future tolerance
300 seconds), filename, size, SHA256 and exact count >=10,000 before replacing
cache. Mirrored data keeps the producer's acquisition time, not a fresh local
stamp. Automatic retry timing is separate and monotonic. Failures preserve cache.

## Implemented product paths

- Native Qt Cruise -> Speed Limit settings below offsets: warnings, automatic
  camera control (default OFF), source, Manual/6/24/168-hour updates, warning
  distance, status/count/date/hash/error and Update Database.
- Native HUD displays camera distance/limit and temporary target/hold. Stale
  map/planner messages suppress stale display details.
- Camera data does not require downloaded OSM road maps. Mapd starts in a clean
  PC profile; native Qt/Python use matching PARAMS_ROOT/OPENPILOT_PREFIX paths.
- Recovery covers interrupted downloads, corrupt caches, source migrations and
  source/path changes during an active worker. HTTP206/Content-Range are rejected.
- Restored native Qt sources/build tool are required because the fork runs the
  tracked selfdrive/ui/ui executable. Python-only UI edits were not visible.

CityGuide IDs differ from iGo IDs. HIGH CONFIDENCE: 290 exact ID+coordinate
matches establish travel bearing=(1705+180)%360 for supported point types.
1713 is beam shape, not DIRTYPE. Source IDs/raw parameters/ranges and a second
explicit azimuth survive cache roundtrips. No average-speed linkage is invented.
Full file: 70,164 positive limits; 60,149 normalized bearings; 59,669 supported
point records eligible by import policy; 14,347 warning-only records retained.
This policy is not hardware or road validation. See cityguide.py for evidence.

LiveMapDataSP @13 carries validated eligibility. The planner additionally checks
supported type, fresh GPS/map data, stock ACC minimum speed and a straight
approach corridor. Camera targets pass through existing ICBM/Volkswagen ACC
button messages. No direct throttle/brake actuation is introduced.

Manual buttons/pedals hand over the current stock setpoint. Short inputs are
latched at carState cadence before model planning. Passage needs two distinct
fresh fixes behind the saved camera plus a fresh map generation before release.
Manual intervention during the subsequent '+' return cannot restore an older
higher virtual setpoint. Disengagement resets the episode.

## Validation

- Final portable suite: 633 passed, including full external BKM, producer,
  manifest fallback, HTTP transport, cache, replay, controller and planner.
- Final Linux native suite: 40 passed, zero skipped. Real Capnp/msgq/Params,
  OsmMapData publication and actual LongitudinalPlannerSP construction/update/
  arbitration/publication. MPC input is an explicitly supplied crash-counter
  fixture; no MPC solver, model inference or complete plannerd loop ran.
- Qt button -> real Python updater -> loopback full download -> cache -> native
  labels: cold and cached refresh both passed, 74,016/hash/date visibly drawn.
- ARM64 UI built with Qt 5.12.8; all 20 NEEDED libraries matched device inventory;
  maximum GLIBC2.29/GLIBCXX3.4.26. Final tracked UI SHA256:
  4545afe1028f7a493a03ce282d74006a0795b48525d9c8c0687790516192b221.
- Production Python live SCO transfer validated on PC through the diagnostic
  proxy route. Read-only validation on Comma also passed: 43.103 seconds, 74,016 records, matching SHA256, no device cache writes.

Evidence remains ignored under .native-build: ui-final-evidence,
runtime-smoke/final, source-probe, speedcam-release. Do not commit keys, caches,
user archives, downloaded databases or vendor runtime libraries.

## Remaining checks

User installs through UI and verifies the actual menu/status/notifications.
The native UI binary has not yet been confirmed running on the device in this
checkpoint. A recorded vehicle trace is still required for physical ICBM
acceptance and behavior. Monitor a scheduled run after the successful initial
push-triggered GitHub run; schedule dispatch can be delayed by GitHub.

Average-speed sections and full road-graph association are not implemented;
parallel-road ambiguity remains a validation limitation. Do not rename Alpha
to stable based on compilation or synthetic tests. No hardware readiness claim.
