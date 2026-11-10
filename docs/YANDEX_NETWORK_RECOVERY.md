# Yandex recovery after intermittent connectivity

Implemented on 10 November 2026. The owner requested a commit/push and will
install the update; no device update or process restart was performed here.

## Failure reproduced

The previous updater refused every new attempt while its current thread remained
alive. The HTTP transport's 45-second check ran only after `session.send()`
returned, so it could not interrupt blocked DNS/header handling. A deterministic
local probe blocked DNS, advanced the scheduler clock by an hour and observed the
same live worker with no new attempt. Ordinary completed network errors did
recover, but the retry delays reached 30/60/120/240/300 seconds and returning
connectivity did not reset that delay.

## Acquisition isolation

The default backend now runs native acquisition in a separate Python child.
The parent has a 60-second monotonic budget covering the whole forward/reverse
window, including DNS, connect, headers, body and pipe output. It kills and reaps
the leaf child on timeout or generation cancellation. A 65-second scheduler age
fence additionally cancels a delayed parent generation. No extra threads are
abandoned to bypass the existing worker cap.

The child reads the existing owner-private credential file itself. Credential
values do not appear in arguments, environment variables, stderr, logs or the
JSON protocol. The pipe is limited to 8 MiB plus protocol overhead; input is
limited to 4096 bytes. JSON decoding after child completion adds bounded CPU
work beyond the network deadline. This is native Linux Python, without Android,
manager imports or hardware access.

## Connectivity and retry

After a typed network failure or acquisition timeout, one background probe can
check the actual Yandex service host no more often than once every 10 seconds.
It opens TCP/443 and certificate-verified TLS with the correct hostname. It
does not use ICMP, an unrelated connectivity-test domain, API credentials or an
SDK request. The probe itself runs in a killable child with a five-second limit;
its lightweight imports do not load the acquisition stack.

The first successful probe after a network failure clears the retry backoff and
invalidates old work. The next eligible tick requests the current GPS position
and heading, subject to a minimum five-second acquisition-start spacing. The
10-second probe spacing survives repeated failures, recoveries and key/toggle
changes. If GPS or heading is invalid, acquisition waits for valid localization;
network checks do not manufacture a position.

HTTP authorization failures, invalid credentials and malformed configuration do
not arm the network recovery loop. TLS reachability does not establish API
authorization. A partial window explicitly identifies network failures so an
unavailable reverse request can recover without treating all partial geometry
as an internet failure. Existing retry backoff remains for non-network errors.

Fresh camera data may remain usable during an outage only until its original
expiry. Probes, retries and generation changes do not refresh its timestamps or
turn expiry into proof of passage. Late results from cancelled work cannot
replace the new generation. API keys and installation identities are not rotated.

## Verification

Linux tests use real local subprocesses and blocked DNS to verify termination,
cancellation, pipe limits, sanitized failures, child reaping and the standalone
worker entry point. A repeated outage/recovery integration test exercises the
actual process/probe adapters across three cycles, then checks that HTTP 403
does not start a connectivity retry loop. Socket/TLS unit tests use fakes; no
production key or device connection is needed for these regressions.

The final Linux camera/Waze/Yandex/package/release regression passed 1540 tests
with 9 skips. Static lint and whitespace checks passed. Existing ARM64 UI,
Params and Panda artifacts are unchanged; the native package integrity tests
also passed. No field or on-device recovery result is claimed before the owner's
installation and retest.

Install the published branch and restart Comma normally before the field retest,
so an old in-memory map process is not mistaken for the patched loader.
