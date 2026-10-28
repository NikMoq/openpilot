# ICBM packaged update

2026-10-28. Published at the user's explicit request for installation through
the normal branch updater, followed by WinSCP diagnostics. This replaces the
proposed manual bench deployment. No device update was executed by the agent.

The ICBM branch now includes the matched F4 firmware, ARM64 daemon, Python
extension and release manifest. The existing Panda wrapper compares signatures
and updates firmware during startup through its normal update flow. No separate
manual firmware upload is needed for the observed Caddy dos/F4 configuration.

| Component | SHA-256 |
|---|---|
| panda/board/obj/panda.bin.signed | 606c7f6dd713331350c2eaf264a3d5beae23b79b0e6f59edbeed6e95d82bd659 |
| selfdrive/pandad/pandad | 4a7153f4c8046af32906c1a4d2846234baa70dc95d7b2d27f6b1a3180d6dd875 |
| common/params_pyx.so | 667cb07f118989b8d09a0699008b071a79530bbaca8348ee73f08aed9c0e5ef7 |

The firmware preserves the stable 300-cNm steering limits. H7 images and
bootstub images are unchanged. Sources are the reviewed 5b69bd5 candidate,
plus the release preflight extension hash check and packaged binaries.

The Python extension contains all 14 new parameter keys. Offline ARM64
CPython 3.12.3 checks confirm persistence across manager/onroad/offroad/
ignition/development clears and preservation of existing types and flags.
The existing LanguageSetting default changes from the stale binary's main_en
to the source's main_ru; manager already explicitly sets main_ru at startup.

Before hardware discovery, the wrapper verifies firmware, daemon and extension
hashes against the manifest and provisions the exact expected Panda signature.
A mismatch aborts that attempt and revokes the retained ICBM pairing identity.

After the user's normal update and restart, verify the installed Git commit,
all three hashes, fresh Panda signature, process health and ICBM protocol status
through WinSCP. The update does not automatically enable ICBM or camera control.
Physical ACC acceptance, PQ46 coverage and imperial-unit behavior remain
unverified; publication alone does not establish functional equivalence.

Validation of this packaging change: release/preflight tests 16 passed,
one Windows-only symlink check skipped. Prior firmware emulation, ARM64
protocol/runtime tests and signing verification are documented in
ICBM_SIGNED_OFFLINE.md and ICBM_STATUS.md.
