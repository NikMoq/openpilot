# Local measured trip statistics, schema v1

`system.tripstatsd` is a read-only subscriber to `carState` and `deviceState`.
It uses existing `CarParams` only to reject mock/body/non-car metadata, and reads
existing `CurrentRoute` only as optional provenance. It writes no Params and has
no network/cloud/API dependencies. Manager integration is an independently
reviewed `always_run` PythonProcess; it does not depend on the UI process.

The sole public file is `/data/trip_stats/stats.json`, atomically replaced after
file fsync and followed by directory fsync. `producer.lock` is held exclusively
for the producer lifetime. A second producer or a corrupt/oversized ledger
fails closed; an existing ledger is never silently reset. Maximum ledger size is
16 MiB. Preserve a rejected file for explicit recovery; do not delete it to
make the daemon start.

Public JSON fields:

- `version: 1`
- `all`, `week`: `{routes: integer, distance_m: number, duration_s: number}`
- `coverage`: `measured_only`, `started_utc`, `dated_distance_m`,
  `undated_distance_m`, `week_utc_start`, `week_utc_end`, `routes_incomplete`,
  `week_routes_incomplete`
- Internal persisted `sessions`, `checkpoint`, `runtime`, `imports` must be kept.

A route is an ignition/onroad session with at least one measured interval,
keyed by boot ID plus `deviceState.startedMonoTime`. If that timestamp is absent,
a random fallback session is persisted across daemon restarts. Route counts are
not directory counts, API totals, ACC engagements or estimates of missing trips.
Zero speed contributes measured duration, and no artificial idle distance.
`duration_s` is the accepted sampled onroad duration, not only time with ACC on.

Only finite speeds from 0 to 100 m/s are accepted. Event monotonic timestamps
must be increasing and close to the receiver's CLOCK_BOOTTIME; intervals above
2 s or speed changes above 12 m/s² are excluded. These are measurement quality
filters; no vehicle command or safety limit is modified. Speed integration is
trapezoidal. Samples do not bridge invalid/offroad/mock periods. Persisted
checkpoints and per-session event watermarks prevent replays and duplicate
counts after daemon/UI restarts. A crash/power loss can omit unflushed measurements (normally under 30 s
when periodic writes succeed); that coverage is not invented later.

Flushes occur every 30 s, on a confirmed offroad transition, and clean shutdown.
Weekly fields are refreshed even offroad. UTC weeks mean the current UTC date
and preceding six dates. Intervals spanning midnight are split with the same
interpolated speed. Unavailable/inconsistent wall clocks still contribute to
all-time measured totals; future dates never appear in current weekly fields.
The UI should treat absent/corrupt/incompatible files as no measured data.

## Explicit offline recovery

Recovery is opt-in and does not scan folders or infer kilometers from filenames:

```
python -m openpilot.system.tripstatsd --directory /offline/ledger --import-measured bundle.json
```

The daemon must be stopped before importing into its real ledger; its exclusive
lock prevents concurrent import. Prefer a separate offline ledger for review.
Bundle fields: `source_id` (route/segment/source SHA identity), `route_id`,
`samples`, with optional `source_sha256`, `source_kind`, `segment_id` metadata.
Each sample: `mono_ns`, `speed_m_s`, `wall_time` (Unix UTC seconds or null),
`valid`, `real_car`, `onroad` (explicit booleans). Samples must be strictly ordered
by event monotonic time. Never map a filename or file mtime to a wall clock.

The identical source identity and measurements are idempotent, including after
relocating the same source file: host paths and auxiliary exporter provenance do
not contribute to the canonical measurement digest. Reusing a source ID with different
contents fails. Per-route monotonic watermarks conservatively skip overlapping
or out-of-order recovered segments: they cannot double count, but may omit a
segment boundary interval or an earlier segment imported after a later one.
Import segments chronologically. Offline exports are reviewed evidence; no
recovery automatically runs at daemon startup.

## Tests

```
python -m unittest discover -s system/tests -p test_trip_stats.py -v
```

The integration math imports only the standard library. POSIX lock/durability
cases execute on Linux; Windows runs pure cases and reports those three skipped.
No test publishes vehicle messages or requires a device.

Unavailable CarParams metadata is retried at most once per second while onroad;
this never creates another session. Known CurrentRoute identities/aliases from
measured live sessions explicitly block recovery of the same route. When route
provenance is unavailable, operator review must restrict opt-in recovery to older
predeployment logs; there is no safe inferred live/historical deduplication.
The CLI accepts one JSON bundle per invocation; multi-bundle JSONL exports can
be reviewed using repeated pure import_measured calls on an in-memory ledger.

Recovered diagnostic recording IDs are not proof of complete vehicle journeys.
Recovery counts no trip unless both `known_route` and `count_route` are explicitly
true. Unknown recordings still contribute measured distance/duration;
`routes_incomplete` marks all-time uncertainty and `week_routes_incomplete`
marks only unknown-route buckets inside the current UTC seven-day window.
The UI can display `—` for unknown zero known trips or `≥N` for known lower bounds.
