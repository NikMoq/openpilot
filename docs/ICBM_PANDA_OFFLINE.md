# ICBM Panda300 offline candidate, 2026-10-28

Status: OFFLINE_BUILD_VALIDATED. Physical device/ECU behavior UNKNOWN.
No signing, installation, flashing, device access or safety-limit change occurred.

## Source identity

Panda base: 1020d355584265391eb3acb556e4353b581fa9c0 (Git archive).
Protocol/GRA donor: aabf70a, tools/pq_icbm_release; donor safety policy excluded.
Safety: port opendbc_repo/opendbc/safety, identical to stable base
3b9179406c4a43319af3383157fc83e1098cba4d. max_torque=300 cNm,
rate_up=6, rate_down=10, RT delta=113, driver allowance=80/multiplier=3.
All eight board interop declaration headers are already tracked in stable
opendbc_repo/opendbc/safety/board; no donor/fallback headers are needed or copied.
The prepare script independently verifies stable file contents before staging.

An independent review identified a donor regression rejecting bulk legacy
standstill RES. A narrow selected==RES && !vehicle_moving early return now
preserves the ordinary safety-checked stock resume path. The normal TX hook
still rejects it with controls disallowed. Moving automatic fine commands use
A9 request IDs and AA status; no unidentified bulk lease is created.

## Artifacts and checks

Production unsigned ELF SHA-256:
d38c119e124630a6f0cfd76a62b09e2e4b8622b5c8940a7c178a85adcc14435f

Production unsigned BIN SHA-256:
5453088eebc1faa119aa41f6f10f2a8ba9b2152d5b1859f30a370fc2b7c25670

ICBM-disabled control BIN SHA-256:
24019d05cb909533416031b54ccd1e90a44babdfff4267fe19c3c150bfa3f551

Test-only ELF SHA-256 (exports bounded test hooks):
aa017fd2a4f8c052e1ff36b29882fe1c1987b6859a04eefa7a03f62ae29138ec

13 actual ARM Unicorn tests passed in 12.229 s. Coverage includes sequential
idempotent requests, timeout, brake/gas/controls/relay/heartbeat/RX guards,
driver interruption, abort before press versus release-pending, exact returned
release matching, seven-frame SET/RES, legacy standstill RES, compiled safety
ramp 0..300 accepted and 301 rejected, rate and controls rejection, SRAM2 zeroing
and legacy stack boundary. MMIO and hardware init are explicitly substituted;
no ECU acceptance, physical timing or vehicle readiness is established.

Both stock and ICBM unsigned builds compile/link using ARM GCC9.2.1 and -Werror.
Two deliberate linker violations are rejected: 64KiB RAM addition triggers
legacy-stack overlap assertion; 4-byte SRAM2 addition overflows its 64KiB bank.
Both builds include the SRAM2 RX ring relocation and assertions from the
reviewed patch; stock is an ICBM-disabled control, not an unmodified image.

## Reproduction and evidence

Source package and pinned preparation: tools/pq_icbm_release/README.md.
Docker: pq35-icbm-f4-build:/tmp/icbm300_offline, Ubuntu20.04 ARM GCC9.2.1.
Build environment variable OPENDBC_SAFETY_ROOT points only to staged stable300.
Full build outputs, inputs and test log remain outside Git in outer workspace
workspace/results/icbm_panda300_offline. Reproduced verified input manifest:
verified_results/inputs.json; verified_results/build.log and wrong-safety.log;
earlier test-build.log and protocol_tests.log. Production unsigned BIN reproduced
identically after normalized safety/header staging. ELF hash above is latest
verified build; earlier test-only ELF used the same C policy and test hooks.
Original signed firmware and private keys were neither read nor generated.

Host source has protocol/echo safeguards but no release manifest, signed
pairing or activation. The user must separately authorize any future signing,
publication, device operation or physical validation. This unsigned candidate
is not a deployable release.

Build admission guard: stable300_safety.sha256 pins all 40 normalized safety
and stable board-interop headers. Build script verifies every hash before
creating OUT or invoking GCC. A deliberate max_torque=1000 substitution in
a disposable staged header was refused before output creation; header restored.
Preparation also rejects changes to any existing stable policy/header by
comparing its contents to stable Git object 3b9179406c4a43319af3383157fc83e1098cba4d.
Safety header source SHA-256 after LF normalization:
bb87201fd156a712e2b29b35c8f4e34971dc8afa94157f9e647dcc4b813167fd

The canonical production ELF (without test-hook exports) additionally passed
4 direct ARM tests: compiled 300/301 torque boundary, rate/controls rejection,
legacy standstill RES and startup SRAM2/stack layout (2.334 s).
The ICBM-disabled stock control passed the same torque/rate/controls/startup
checks (3 tests). Logs production_tests.log and stock_control_tests.log are
retained outside Git with the other evidence.

A clean-source reproduction exported only the safety tree directly from
stable Git object 3b91794 (clean_git_safety_export), then ran preparation into
a NEW clean_git_prepared_final directory. All eight board interop headers
were present in that Git object, so no compat fallback is necessary.
The optional fallback code was removed. All 40 safety admission hashes passed,
and fresh stock/ICBM compilation reproduced both unsigned BIN hashes above.
Container build directory: /tmp/icbm300_clean_git; clean_git_build.log retained.
ELF container hashes differ between build directories; unsigned application bytes
are identical. Canonical artifact references above remain verified_results.

Preparation archives only whitelisted board source/header/assembly/linker files,
python/__init__.py (the source codec patch target), and LICENSE if present.
Filename selection happens before Git archive reads content. Certificate/key,
private, obj, binary and signed-artifact paths are excluded. No whole-repository
archive or unrelated vendor material is used by the final recipe.

The final source-only recipe was executed into scoped_sources_final and built
in /tmp/icbm300_scoped_sources. Admission passed and unsigned stock/ICBM BIN
hashes reproduced identically again. scoped_sources_build.log records it;
scoped_sources_final/inputs.json contains only the selected source inputs.
