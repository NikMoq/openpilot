# Camera misses: shared road selection and source coverage

Status: confirmed code defects reproduced locally; field retest pending.
UI status superseded on 11 November 2026: the HUD changes from `888f0dc` were
rolled back to the exact approved `9690042` UI at the owner's request. Only the
camera acquisition/matching algorithms and network recovery changes are retained.
The historical HUD-warning change described below is not part of the current UI.
The user reported Waze misses in Germany and Yandex misses in Krasnodar on the
current `waze` branch. A later Yandex camera at 60 km/h did trigger reduction.
No software was installed or process restarted during this driving report.

## Confirmed causes and changes

* `hud.cc` suppressed a valid warning whenever camera control mode was enabled,
  even without a selected control sign. It could display the planner's
  `no_camera` instead. Existing warnings now remain visible without pretending
  that they authorize control; a selected control sign keeps priority.
* `OfflineRoadSnapshot._route` stopped at every fork. A side street therefore
  erased a camera on the same main way, including bends of 5/10/20/30 degrees.
  A unique same-way continuation is now retained only if the camera is on that
  way and the turn satisfies the existing 35-degree tangent tolerance. Different
  target ways, competing same-way branches and 90-degree future turns remain
  refused. Route cache keys include the target way.
* The publisher required an offline OSM snapshot even when the source supplied
  fresh verified geometry. The new per-camera adapter can use witnessed provider
  paths where map coverage is absent, without enabling the legacy point-only
  fallback. Covered OSM association ambiguity is never overridden. Coverage gaps
  are checked only between the actual vehicle and camera projections.
* Waze partial fixed batches previously discarded known cameras when another
  tile contained an unknown type. Known reports are retained with partial status;
  retained old data expires at its original timestamp. Unknown types do not gain
  automatic control eligibility. Partial retention evicts records beyond 6 km
  and keeps the closest records within the existing count/byte budgets, so an
  old full cache cannot permanently block newly downloaded nearby cameras.
* Waze road-limit enrichment used input order for its 32-event/time budget.
  It now processes the nearest reports first while preserving other warnings.
* Yandex could continue querying a corridor acquired before a gentle departure
  until its ordinary time/distance/heading refresh. It now also refreshes when
  the vehicle departs fresh retained geometry, subject to the existing request
  spacing, backoff and worker limits. These are acquisition triggers, not relaxed
  control tolerances.

Native Yandex records retain original route vertices around the event: at most
512 per record and 32768 across one route. They include source timestamps and
are not extrapolated. Opposite-carriageway mobile variants retain the driven
road's context and limit. Oversized optional approach context is omitted without
erasing the underlying camera. Provider expiry revokes retained CAN anchoring;
an actually refreshed same-identity record can replace the old evidence without
resetting a confirmed approach. Cache reads never renew source freshness.

## Evidence and remaining limits

The native Qt presentation regression checks a valid warning with and without a
control sign. Shared road tests exercise the main-road bend plus side street,
wrong branches, parallel roads, absent maps, missing path coverage, stale source
data and real-wire approach/passage restoration. The camera reaction distance,
GPS health checks, position/heading tolerances and passage requirements were not
increased to make tests pass.

A bounded live Linux fixed-camera probe in Berlin fetched 150/150 requested
tiles: 26 events, comprising 21 speed cameras and 5 red-light cameras. It retained
26 warning candidates and 16 qualified limit candidates; six road lookups were
unqualified. This is a time/area-specific transport check, not a complete camera
inventory or a test of mobile RT events.

A separate live native Yandex Linux probe near Krasnodar returned 8 fixed and
3 warning events, with eight qualified road-limit candidates. The eight retained
paths supported synthetic projections aligned to their acquired route. An
arbitrary incompatible test heading produced no road match, as required. These
projections are not a live vehicle or road test and do not prove coverage of the
user's currently travelled street.

Yandex acquisition remains a bounded set of internal routes, not a copy of the
phone's viewport or navigation destination. A source event count is not the
number of qualified cameras within the current warning distance. The ordinary
warning zone remains the configured reaction distance, normally 300 m.

Waze fixed and mobile sources have independent success/failure states. A fixed
success does not prove that mobile registration/login succeeded. A pending local
registration can stop further registration, while an HTTP/protobuf refusal can
also prevent mobile data. No account identities were rotated, no registration
barrier was bypassed, and no mobile completeness claim is made.

The wider Linux camera/Waze/Yandex/release suite passed 1476 tests with 9 skips.
The final Waze diagnostic/partial-retention changes then passed 59 scoped Linux
tests. The native Qt presentation test, ARM64 compile/link, dependency check and
package/ICBM guard passed. The guard now covers nine native source inputs,
including HUD and its presentation helper. Static lint and whitespace checks
passed. These checks do not replace a subsequent field comparison with the phone.

Actual responses, credentials, camera datasets and build logs remain outside
Git. The release package includes the rebuilt ARM64 HUD and hashes both its
source and its new presentation helper. Panda/daemon/Params artifacts are
unchanged. Field confirmation must follow installation while parked.
