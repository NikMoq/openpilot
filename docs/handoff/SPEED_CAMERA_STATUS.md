# Speed cameras / ICBM — 2026-10-26

## Scope and deployment

Active source: SpeedCamOnline, all camera types/all Russian regions,
https://speedcamonline.ru/cg77/Rus/comment/ . This replaces the old APK source.
Code and ARM UI were published on dev in NikMoq/openpilot at
a5efa5767191cb077814e563651326bc900f599e, followed by documentation commit
3f5c326197fec45c5520bded76ff1cdc9c147c59. Read-only device inspection confirmed
3f5c326 running, a clean checkout, the expected running UI SHA256, and the full
74,016-record cache with matching source hash. The user confirmed the new menu
and a successful Update Database action through UI.

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
- Warning Distance is 100..2,000 metres in 50-metre steps,
  with immediate visible value updates and persistent selection. The user found
  the old missing label; read-only device Params proved its buttons did save.
- Camera Speed Offset is 0..30 km/h in 1-km/h steps,
  default zero. It adds to the camera target without raising the driver's cruise
  setting or other planner constraints. The raw camera limit remains distinct.
  This user preference does not establish a legal exemption from penalties.
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

LiveMapDataSP @14..@21 separately carries the eligible control candidate. A
warning-only record at 100 metres no longer masks an eligible camera at 500
metres. An explicitly empty version-1 selection cannot fall back to the warning;
legacy version 0 retains its previous type/eligibility checks. Raw camera limits
stay unchanged on the wire. The offset applies only inside the camera planner;
raising it during an active camera episode cannot raise the existing ceiling,
while reducing it can lower that ceiling.

The confirmed driver-override contract preserves the driver's original cruise
setting throughout a camera episode. Gas temporarily yields button authority;
after release an ahead camera's ceiling applies again. Brake disengages stock
ACC without erasing the setting or camera geometry. Resuming requires physical
RES and engaged stock ACC; the controller does not automatically enable ACC.

The user chose OPTION 1 for physical +/- during a camera episode: temporary
native ACC correction, followed by return to the original setting after passage.
The native increment is read through stock feedback, not assumed to be 1 or 10.
A press begun during the episode remains temporary if released after passage.
Outside the episode, ordinary speed-button and road Speed Limit Assist behavior
is retained. Reengaging with SET takes a new base from valid native feedback
after the button is released; RES keeps the remembered base. Main cruise off
ends the remembered session. Explicit cancel is
distinct from a temporary pedal or speed-button override.

Short inputs are latched at carState cadence before model planning. Passage
needs two distinct fresh fixes behind the saved camera plus a fresh map
generation before release. Geometry continues during temporary overrides, and
confirmed passage is retained if a pedal remains pressed beyond the passage
window. Other lower planner constraints still bound the resulting target.
Actual ICBM transmission stops directly on physical pedals or disengaged stock
ACC, including when carControl is one frame late. During a temporary camera
manual state, transmission remains suspended even if another planner constraint
is below the driver's native correction. Automatic authority resumes through
the existing 0.4-second activation delay when the temporary handover ends.

## Validation

- Driver-override follow-up: 986 portable tests passed, zero skipped, including
  the full external BKM fixture and the updated helper/controller/planner/sender
  scenarios. Linux native suite: 72 passed, zero skipped. Real helper -> decoded
  carState -> planner -> decoded plan -> ICBM sender covers gas, brake/RES, SET,
  temporary +/- corrections, held buttons across passage, +19 camera offset,
  and a lower Vision constraint. Tests also check that a temporary manual
  correction above the remembered base is not counteracted by ICBM.
- Native source snapshots match all five changed production modules. Shared
  helper/sender timers are now per-instance. Explicit SET after disengagement
  takes fresh stock feedback; a brake before any camera does not create a false
  RES requirement for the next camera. No device/CAN actuation was used.
- Settings/selection follow-up portable suite: 853 passed, including full external BKM, producer,
  manifest fallback, HTTP transport, cache, replay, controller, planner,
  independent camera selection and offset wiring.
- Settings/selection follow-up Linux native suite: 50 passed, zero skipped; all original 40 tests
  retained. Real Capnp/msgq/Params,
  OsmMapData publication and actual LongitudinalPlannerSP construction/update/
  arbitration/publication. MPC input is an explicitly supplied crash-counter
  fixture; no MPC solver, model inference or complete plannerd loop ran.
- Native offset cases prove raw 60 km/h remains on the wire; +19 creates the
  expected kinematic approach ceiling and a 70 km/h user setting stays at 70.
  Native Params was rebuilt with the new key and 0/19/30 roundtrips passed.
- Actual Qt controls verify immediate labels, 100/150/200/2,000 metre steps and
  0/1/2/19/30 km/h offsets, boundary buttons and selection after reopening.
  Saved 1,500 metres and +19 survive construction/reopening. Preview also shows
  the raw 60 km/h camera separately from ACC TARGET 79 km/h at 150 metres.
- Qt button -> real Python updater -> loopback full download -> cache -> native
  labels: cold and cached refresh both passed, 74,016/hash/date visibly drawn.
- Follow-up ARM64 UI built with Qt 5.12.8; all 20 NEEDED libraries match the
  prior device-compatible build; maximum GLIBC2.29/GLIBCXX3.4.26. Tracked UI SHA256:
  d36447230a2947d1070dd227a5364ee836f8ba95da4d761d2805d00d1f79538b.
  The earlier 4545afe1028f... build was the one confirmed running on the device.
  Follow-up build input hashes match the current workspace, including schema and
  native Params registry; the exact ABCM title is present without Alpha.
- Production Python live SCO transfer validated on PC through the diagnostic
  proxy route. Read-only validation on Comma also passed: 43.103 seconds, 74,016 records, matching SHA256, no device cache writes.

Evidence remains ignored under .native-build: ui-final-evidence,
runtime-smoke/final, runtime-smoke/followup, runtime-smoke/override-policy,
warning-distance-fix, source-probe,
speedcam-release. Do not commit keys, caches,
user archives, downloaded databases or vendor runtime libraries.

## Remaining checks

The settings/selection follow-up is published as fd454f15fbf247e2094d149c3456e8198e5af216.
Its SpeedCamOnline producer CI run 40350871045 passed. Install through UI.
The driver-override follow-up in this commit is NOT part of fd454f1. Its
confirmed contract (implemented and tested above) is:

- Gas/brake must retain the driver's original cruise setting (example: 130).
- Brake still disengages stock ACC; the driver explicitly presses RES to resume.
- Gas temporarily yields control; on release a still-ahead camera's ceiling
  applies again. Passing the camera permits returning to the remembered setting.
- User explicitly chose OPTION 1 for wheel +/− during a camera episode:
  temporary native ACC correction, then return to the original 130 after passage.
  Do not change the remembered maximum to 80/131/140 based on those presses.
  Native button increments are not assumed to be 1 km/h or hardcoded to 10.
- Outside a camera episode, speed buttons retain ordinary setpoint behavior.
- User requires behavioral algorithm decisions to be discussed BEFORE choosing
  and implementing them. The above choices are already confirmed; do not ask
  again or substitute a different manual-override policy without agreement.
Installation of this follow-up through UI is not yet confirmed. A recorded
vehicle trace is still required for physical ICBM acceptance and
behavior. Monitor a scheduled run after the successful initial push-triggered
GitHub run; schedule dispatch can be delayed by GitHub.

Average-speed sections and full road-graph association are not implemented;
parallel-road ambiguity remains a validation limitation. The user explicitly
requested the display title Intelligent Cruise Button Management (ABCM),
without Alpha/Stable. This cosmetic title change does not certify hardware
readiness; internal ICBM parameter and protocol names remain unchanged.
