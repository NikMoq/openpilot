# Recorded-position replay: negative result, not camera acceptance

New analysis on 2026-10-29 used only original raw recording inputs, current cam
code and fresh provider/map data. No previous analysis reports or other branch
code were used. Raw GPS, route coordinates, graph bounds and camera databases
are not committed. Aggregate evidence, exact input/source hashes, clock rebases
and all six runs are in `CAMERA_RECORDED_ROUTE_EVIDENCE.json`.

## Inputs and clocks

Three adjacent original rlog segments were tested. Their recorded UTC intervals
on 2026-10-26 are:

| Input | Recorded UTC interval | Bytes |
| --- | --- | ---: |
| 0010.rlog | 18:49:48.999–18:50:48.900 | 36,220,944 |
| 0011.rlog | 18:50:49.000–18:51:42.900 | 29,226,280 |
| 0012.rlog | 18:51:43.000–18:52:54.199 | 46,273,824 |

SHA-256:

- 0010: `7c99a83b6d9036abe46f4be37690298ffe3955eb34ac5ed5c93c9b2cbf9c9d45`
- 0011: `0e6ee85c5591e6e749c3589241d50a51906f80017c24b1e7165a058afc7d1734`
- 0012: `bb0b576b9d1430dae6dd04df7a2678eb0c2d2deb50da1fefea27f0b9b7d67da1`

`tools/replay_camera_route.py` reads only GPS, LLK and clock fields. Recorded
Unix/monotonic clock anchors preserve transport ages; a constant explicit virtual
Unix rebase preserves fix order, duplicates and relative timing. The JSON includes
each exact rebase offset. Original timestamps and current data acquisition times
are never relabeled as historical evidence. This is current-data counterfactual
source/association replay, not evidence of historical camera presence or received
vehicle commands.

## Current data and complete bounded graph

The current mirror supplied 74,023 records, acquired 2026-10-28 05:50:24 UTC,
SHA-256 `116d1748b9175ecc3a617264d2855ae4f5479121829cfb649e0e0962101c4579`.
Bytes were verified and parsed in memory only.

A 5 km halo request exceeded the OSM API's explicit 50,000-node limit. Preparation
continued with a smaller genuine unfiltered region enclosing the reliable recorded
positions, all three nearest actual directional camera candidates, and a 250 m
margin. It contains 1,696 directed edges; SHA-256
`24c4cdb222f0487746ce5835c11ca04957a05bcf51f6b1f27d29a5b52b95f21c`.
The ordinary complete map API response and its hash/acquisition provenance were
loaded by the current unchanged graph loader. No road IDs were manually trusted.
This covers the examined nearest candidates, not every possible distant candidate
in a nationwide 5 km search. No examined nearest candidate lay outside the final
export. Assets and bounds remained only in the disposable container's `/tmp`.

## Results at both supported configurations

Each input was replayed with default reaction distance 300 m and supported
reaction distance 1,000 m. Outcomes were identical at both settings:

| Input | Source ticks | Reliable ticks | Vehicle associated | Vehicle ambiguous/unmapped | Selected / passed |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0010 | 60 | 54 | 37 | 17 | 0 / 0 |
| 0011 | 54 | 50 | 43 | 7 | 0 / 0 |
| 0012 | 72 | 2 | 0 | 2 | 0 / 0 |

For 0010, the nearest directional candidate was not on a uniquely established
vehicle corridor on all 54 reliable ticks, including 17 ambiguous/unmapped vehicle
positions. For 0011, 43 nearest camera projections were ambiguous/unmapped and
seven lacked a unique vehicle corridor. Both reliable 0012 ticks had ambiguous or
unmapped camera projections. These are observed conservative rejections, not
independent ground-truth labels proving which physical road each camera monitors.
The matcher was not weakened to force an event.

Reliable raw positions approached eligible forward cameras no closer than
approximately 793 m, 495 m and 459 m respectively. No reliable recorded crossing
was found. Earlier segments 0000–0008 had no policy-qualified moving GPS or fully
valid LLK; 0009 had only six qualified raw fixes and no valid LLK status. Segment
0013 had no qualified moving GPS. The two other raw sessions had no GPS/LLK.

**No reliable unambiguous camera episode exists in the examined available inputs.**
This replay does not positively validate ACTIVE camera control, passage, driver
restoration or all false-positive cases. The user subsequently explicitly stated
that no positive recorded log is available and authorized modeled tests instead.
Authoritative acceptance therefore uses the simulated actual-native pipeline and
native Qt tests; the missing positive recorded episode is **not a task blocker**.
This negative replay remains an honest historical finding. No physical CAN,
MPC/model inference, bench activity or road-readiness claim is part of this replay.

## Reproduction

Use the disposable native environment described in `CAMERA_NATIVE_RUNTIME_QA.md`.
Supply raw input and graph paths privately as external parameters; do not commit
those assets. Run the same graph at both configurations:

```sh
python /cam/tools/replay_camera_route.py --rlog "$PRIVATE_RLOG" --graph "$PRIVATE_GRAPH" --reaction-distance 300 --report /tmp/route-300.json
python /cam/tools/replay_camera_route.py --rlog "$PRIVATE_RLOG" --graph "$PRIVATE_GRAPH" --reaction-distance 1000 --report /tmp/route-1000.json
```

The graph must be a current unfiltered OSM bounded export acquired through
`camera_road_graph.acquire_map_export`, with its matching manifest. The final
recorded-route region was computed from actual recorded positions plus actual
nearest candidate positions before this unfiltered acquisition; only its hash and
counts are public. The replay tool can acquire a standard halo with
`--acquire-graph --graph-margin-m 5500`; a smaller explicit margin is a coverage
limitation, never permission to treat zero selected events as acceptance.

## Private artifact preservation

The exact graph, acquisition manifest and six aggregate replay JSON files are
preserved outside Git under ignored `.native-build/recorded-route-qa/`. No raw GPS
recording or provider database was copied there. These private QA data assets are
not part of the runtime bundle; public evidence retains only hashes and counts.
