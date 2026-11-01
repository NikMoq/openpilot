# Approved offline signing result

Publication update: the user subsequently explicitly authorized publishing the
generated firmware and matching runtime artifacts on GitHub for a normal
branch update. They are now packaged; see ICBM_UPDATE.md. The source-only
statements below describe the original offline-signing phase.

2026-10-28. Status: OFFLINE_SIGNED_PAIR_VALIDATED. Not installed; ECU acceptance UNKNOWN.
User explicitly authorized separate offline signing of the reviewed Panda300 candidate, without key output/transfer or installation.

Input unsigned SHA-256:
5453088eebc1faa119aa41f6f10f2a8ba9b2152d5b1859f30a370fc2b7c25670
Signed SHA-256:
606c7f6dd713331350c2eaf264a3d5beae23b79b0e6f59edbeed6e95d82bd659
Matched ARM64 daemon SHA-256:
4a7153f4c8046af32906c1a4d2846234baa70dc95d7b2d27f6b1a3180d6dd875

The pinned Panda1020 crypto/sign.py was run with SETLEN in the established local signing process. Private material was handled only inside that authorized signing subprocess, never output, copied or included in artifacts. The original packaged firmware also verifies under the selected public key. Public-key DER fingerprint:
7b96375770f70167da174aec1908415d59ff8b6c9b79c87d50423c11682be304

Checks passed: unchanged application bytes [4,raw_length), SETLEN length field, appended VERS2 tag, 128-byte RSA trailer, public signature verification, rejection after independent application/version/signature corruption, release helper exact pair verification and unchanged authoritative unsigned input. Raw length86432; framed payload86440; signed length86568. Signing did not change torque/safety policy.

Package outside Git:
workspace/results/icbm_signed_review_20261028_af1182e6/
- signing_report.json
- release_root/panda/board/obj/panda.bin.signed
- release_root/selfdrive/pandad/pandad
- release_root/selfdrive/pandad/pq_icbm_release.json

The release_root is a verified artifact pair, not an installer. No binary, private material, signing output or deployment manifest is committed. The source branch must remain usable without a release manifest and must reject corrupt/mismatched release artifacts before its normal Panda update flow. Device installation/configuration and bench testing still require their separately agreed task. Debug-signature validity alone does not prove acceptance by the actual bootstub, device runtime or radar ECU.
