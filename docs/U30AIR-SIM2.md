# U30 Air native SIM2 cold start (experimental)

This opt-in path was tested on one U30 Air with OpenWrt 25.12.5 and
Linux 7.2.9. It starts the second physical SIM from Linux without booting
Android first. No Android partitions, SIM identities or firmware are written.
The default remains SIM1. This is not a live-switch implementation.

To select SIM2 on the next OpenWrt boot:

```sh
printf '1\n' > /etc/mu300-sim-slot
reboot
```

Use `0` (or remove this file) to return to SIM1 on the next boot. The init
service snapshots the setting in `/run/mu300/sim-slot`; editing `/etc` does
not move a running PDP context or its AT owners. Configure WAN with the
selected card's APN using the existing OpenWrt interface settings. There is
no per-card APN editor in this patch. Include `/etc/mu300-sim-slot` in the
configuration backup if keeping the selection through a reinstall.

| Physical card | URC / command / data channel | CID 1 bearer |
| --- | --- | --- |
| SIM1 (0) | nr0 / nr1 / nr2 | sipa_eth0 |
| SIM2 (1) | nr3 / nr4 / nr5 | sipa_eth8 |

## Sequence and failure handling

The SIPC character nodes appear before the CP is ready. The SIM2 path first
waits for `sbuf_5_006, state: 1,` in `/dev/sipc_sbuf`, then starts persistent
owners for nr1/nr2/nr4/nr5 and their nr0/nr3 event channels. The data brokers
use their own group's event log to determine liveness. Reopening or respawning
these owners is disabled for SIM2: repeated SIPC opens can wedge the channel.

Under the shared radio lock, the cold-start controller waits for actual CP
output, sends `AT+SMMSWAP=0` on nr1 and requires both groups to report CFUN=0.
It checks SIM2 CPIN and CCID, then sends on nr5:

```text
AT+SPTESTMODEM=134,134
AT+SPSWDATA
AT+SFUN=4
```

This two-argument work-mode command is specific to the observed modemConfig=7
firmware. `SMMSWAP=1` is not used as a slot selector. Success requires SIM2
CFUN=1 and CEREG registration 1 or 5; an `OK` response alone is insufficient.
Only then is `/run/mu300/sim2-radio-ready` created, and netifd attaches sipa_eth8.
The ready marker deliberately has zero bytes: consumers test existence.

An optional local `/etc/mu300/sim2-iccid` can require a specific card. Keep that
file private. The boot log records command names and decisions, not card values.
At most one cold-start attempt runs per Linux boot. After a failed attempt,
reboot Linux to retry; netifd can retry data attachment after successful radio
initialization, but does not repeat the cold-start or reset a running modem.

## Validation and limits

Two complete device boots registered SIM2 in approximately 39–42 seconds and
reported working WAN at approximately 56–62 seconds. SIM1 remained CFUN=0.
IPv4/IPv6 addresses were assigned and external IPv4 traffic worked. End-to-end
IPv6 forwarding, other firmware versions and other devices are not validated.
The existing IPv6 relay/USSD/voice helpers still assume SIM1 in places; use
IPv4 WAN for this initial opt-in path until those helpers are adapted.

The new tests use synthetic AT responses and scratch directories to exercise
command order, wrong/unready SIMs, missing owners/CP output, rejected SFUN,
registration failure, charging boots, and repeated-attempt refusal. The source
port onto current main has not been installed on the device as a clean image.

Charging-only boots leave the radios off. Radio suspend and live SIM reset,
and the LuCI plugin's lock writes/replay, are refused or skipped for SIM2 until
their resume/restart sequences are validated. Read-only SIM2 dashboard queries
share nr4; the SIM1-only nr6/nr7 pool is not opened. A modem crash needs a Linux
reboot; automatic CP restart is outside this patch.

Upstream PR #72 independently proposes an F50 internal/external SIM selector
with overlapping channel and data routing changes. This patch documents the
additional U30 Air mainline-kernel cold-start gates; the two configuration
interfaces should be reconciled before either is presented as universal support.
