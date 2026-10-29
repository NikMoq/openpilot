# Offline synthetic ICBM closed loop

The current release test uses the complete **exact-original stable+ICBM raw
prototype**, built with `tools/exact_stable_icbm/build_prototype.py`. It does
not substitute a Panda source rebuild. Requirements: Python3.10+ and `unicorn`
(tested2.1.4); prototype construction additionally uses `pyelftools`.

From an openpilot repository root:

```sh
python tools/pq_icbm_release/tests/icbm_closed_loop_replay.py \
  --source . \
  --prototype-manifest /tmp/exact-icbm-prototype/prototype-manifest.json \
  --contract tools/pq_icbm_release/tests/icbm_metric_button_contract.json \
  --output /tmp/icbm-synthetic-report.json
```

The manifest identifies the raw image filename and SHA-256. Canonical
`tools/exact_stable_icbm/raw_image.py` verifies the complete image before
executing original Reset_Handler and the appended initialization hook.
Original CAN RX/TX, safety, forwarding, queues and TXOK-return code execute.
Peripheral initialization, LED I/O, FIFO release and successful-transmission
mailboxes are explicit hardware fixtures. No vehicle is accessed.

Three actual native GRA observations, counters0/1/2 at20ms intervals, warm
the original RX and extension counter guards. The scenario begins at
counter3 with a60ms MCU clock offset so time remains monotonic. No policy
or controls variable in RAM is overwritten by this warm-up.

Exactly one artifact mode is required. `--elf /path/to/panda-icbm.elf`
retains the earlier source-rebuilt **legacy reference only**, and additionally
requires `pyelftools` and existing `arm_can.py`/`arm_memory.py`. It never acts
as fallback for a missing, invalid or unsupported raw prototype. The report
records `artifactKind` and hashes the selected artifact, manifest, canonical
raw helper and original ABI JSON. All source/input hashes are checked before
and after the matrix. `--only` selects comma-separated scenario names;
unknown names or failing matrices produce a nonzero exit.

The complete production VW update method is loaded by AST. Unrelated
steering/HUD branches remain off cadence. The planner, upper ICBM publisher,
driver-base memory and whole-board heartbeat/thermal timing are outside
scope. Target/direction and a healthy connection are fixture inputs.

A separate modeled ACC publishes stock feedback80ms after the first owned
press. This is an explicitly synthetic response, not the ARM TX result.
Historical metric observations support the reference actions. Off-grid DOWN
remains unobserved, so both plausible models are exercised. The contract
records provenance; it is not an executable specification of a physical ECU.

The fifteen scenarios cover high/low/fine endpoints, both DOWN models,
same-ID request retries, manual/gas/brake interruption and recovery, lost
release/feedback, ignored responses and zero/below-minimum targets. Ignored
response times out but can rearm after independent fresh terminal/release
and stock evidence; this is distinct from a permanent-latch assertion.

The canonical test of raw prototype
`0523b0181b8f1b5be29fd688a7207ee8690ed2dd64973dcc747f942427e06695`
passes all15 scenarios. MADS steering continuation is tested independently
against the exact-original binary. Neither result proves generated button
acceptance on a physical radar, EPS recovery, PQ46 compatibility or mph.
