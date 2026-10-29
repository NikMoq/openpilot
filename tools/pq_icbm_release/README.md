# PQ ICBM firmware and offline tests

The current release basis is the whole exact original stable Panda with only
ICBM added. Build instructions and the hash-gated original-byte allowlist are
in [../exact_stable_icbm/README.md](../exact_stable_icbm/README.md).
The source-built1020 reference below is historical comparison material and
must not be used as the next release base.

The reproducible production ARM / host closed-loop matrix is documented in
[SYNTHETIC_TESTS.md](SYNTHETIC_TESTS.md). It includes a sanitized metric
button-observation contract and an explicitly modeled stock ACC response.

## Historical1020 source-built reference

Panda base 1020d355584265391eb3acb556e4353b581fa9c0.
Safety is read from unchanged stable opendbc_repo, base
3b9179406c4a43319af3383157fc83e1098cba4d, max torque 300 cNm.
No donor 1000-cNm safety policy or firmware binary is included.

Prepare a NEW output directory (outside Git):

```sh
python tools/pq_icbm_release/prepare_sources.py --panda /path/to/panda1020 \
  --port /path/to/ICBM --output /tmp/icbm300-new
PANDA_1020_SOURCE=/tmp/icbm300-new/panda \
OPENDBC_SAFETY_ROOT=/tmp/icbm300-new/safety \
OUT=/tmp/icbm300-new/artifacts \
sh tools/pq_icbm_release/build_panda1020_icbm_offline.sh
```

Preparation exports the pinned Git object, applies the protocol patch, copies
GRA source, and verifies EVERY existing safety header/source against stable
before staging. All board interop declarations already exist in the pinned stable safety tree.
No donor/fallback headers are copied. inputs.json records
all staged file hashes. GCC9.2.1 ARM bare-metal toolchain was used.
The generated gitversion is an explicit OFFLINE-1020-ICBM300 marker.

Build stock-capability-disabled and ICBM-enabled F413 controls with -Werror.
Both retain the explicitly asserted legacy stack and SRAM2 ring relocation;
stock here means ICBM-disabled build control, not a deployed-image match.
Test-only build adds EXTRA_CFLAGS=-DGRA_REFERENCE_TEST and a separate OUT.
No signer, private keys, bootstub build or hardware access is used.

Run Unicorn tests against that test-only ELF:

```sh
ICBM_TEST_ELF=/tmp/icbm300-new/test-artifacts/panda-icbm.elf \
python -m unittest discover -s tools/pq_icbm_release/tests -p test_protocol.py -v
```

Requires unicorn and pyelftools. Hardware initialization, CAN draining/LED MMIO
and FIFO acknowledgement are stubbed; actual compiled RX/TX safety, adapter,
packet checks, queues and control handler run from the ELF. TX-return is Panda
transmission evidence, not radar application acknowledgement.

Donor protocol source: local aabf70a, tools/pq_icbm_release. One reviewed fix
preserves ordinary standstill RES through normal TX safety checks. Moving
unidentified synthetic commands still fail closed; correlated fine SET/RES
requires motion. Full status and evidence: docs/ICBM_PANDA_OFFLINE.md.

Build admission requires stable300_safety.sha256: all 40 staged headers must
match before compiler invocation. Header contents are normalized to LF during
preparation. A 1000-cNm substitution is tested to fail before OUT is created.

Preparation archives only whitelisted board source/header/assembly/linker files,
python/__init__.py (the source codec patch target), and LICENSE if present.
Filename selection happens before Git archive reads content. Certificate/key,
private, obj, binary and signed-artifact paths are excluded. No whole-repository
archive or unrelated vendor material is used by the final recipe.
