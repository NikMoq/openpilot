# ICBM addition to exact original stable Panda

Only the exact original signed firmware is an admitted base:
`ef8ea87c17f0802bec38f754d93a3fb2c9850c640b5ba95e01e661323545daba`.
It is the firmware blob in stable commit
`3b9179406c4a43319af3383157fc83e1098cba4d`. No firmware source-tree
substitution or alternate Panda base exists in this workflow.

The builder changes only two previously unhandled A9/AA USB table entries
and three reviewed callsites: native GRA forwarding, TXOK checksum/observation,
and reset-to-main extension initialization. All other original application
bytes remain identical, including DF/F3, MADS, HCA, safety, data initialization,
original RAM/stack/queue locations and the original version tag. The exact
before/after allowlist is generated in `prototype-manifest.json`.

The appended integer-only module occupies its own state at `0x20040000`.
The module preserves the native RX packet and does not call or replace original
RX validation. Independent stricter ICBM eligibility observes original prior
RX status and checks current checksum/counter/freshness. Synthetic button
frames pass through original `can_send` with TX enforcement enabled. Driver
frames retain ordinary forwarding. COMPLETE requires matching original TXOK
return evidence; it is not a radar application acknowledgement.

## Reproduce unsigned prototype

Tested compiler: ARM bare-metal GCC9.2.1. Install Python dependencies from
`requirements.txt` in an isolated environment. Supply a new output directory:

```sh
OUT=/tmp/exact-icbm-build sh tools/exact_stable_icbm/build.sh
python tools/exact_stable_icbm/build_prototype.py \
  --repo /path/to/openpilot \
  --module-elf /tmp/exact-icbm-build/module.elf \
  --output /tmp/exact-icbm-prototype
```

`--repo` extracts only the pinned stable Git firmware blob and verifies its
SHA-256. Alternatively use `--original /path/to/exact-stable.bin.signed`.
The input is never modified. The output is explicitly unsigned and retains
the old length word until separate authorized framing/signing. It is not an
installable firmware. No signing key, device connection or upload is used.

The tested module body is2508bytes with64bytes of state. The prototype body
is60060bytes, SHA-256
`0523b0181b8f1b5be29fd688a7207ee8690ed2dd64973dcc747f942427e06695`.
GCC input source/path differences may change ELF metadata; the module BIN
and complete prototype body are the reproducibility targets.

## Raw ARM testing API

`raw_image.py` loads supplied bytes, verifies their expected hash before Reset,
then runs original startup and original RX/TX/safety/forwarding/queue functions.
It never loads a source-rebuilt replacement firmware. `original_abi.json`
records the independently evidenced exact-image addresses.

```python
from raw_image import PrototypeImage, control, status, native
image = PrototypeImage('/tmp/exact-icbm-prototype/prototype-manifest.json')
print(status(image))
```

The API exposes `rx(address,bus,payload)`, `tx(address,bus,payload)`,
`clock(event_ns,start_ns)` and `ack_return(queued_packet,target_bus)`.
`ack_return` models successful transmitter completion through actual original
`process_can`, not an ECU response. The caller supplies CAN, timing and
heartbeat/configuration inputs explicitly. No driver is created or vehicle
connection opened. Use `RawExactImage(path,expected_sha256)` for exact-original
comparisons.

Original hardware initialization, LEDs and physical transmitter drain are
fixtures. Software equivalence, synthetic ACC models and CAN replay do not
prove physical EPS recovery or ICBM operation. Recordings and generated binaries
belong outside this source package. Physical validation remains separate.
