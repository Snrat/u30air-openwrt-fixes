# U30 Air SC2355 firmware SAE offload (opt-in, experimental)

**What a normal build from this branch includes.** Normal image builds include only
the driver side of this work: `sprd_wlan_combo` no longer prints passphrases,
station PSKs or raw command/event payloads (the same removals exist as
`kernel/patches/wlan_combo-sae-private-logs.patch` for the vendor kernel).
The hostapd patch is **not** included by a normal image build: it exists as a build
recipe (`openwrt/build-u30-sae.sh`, `MU300_SAE_APK_DIR` for `build-rootfs.sh`)
and does nothing until `u30_sae_offload=1` is set on a BSS. Nothing changes for
a device that does not opt in on both counts.

## What the offload is, and what it is not (maintainer review, 2026-10-13)

- **Where SAE runs.** In the SC2355 firmware, on every kernel: the driver registers
  the access point with the SME in the firmware, so hostapd never sees an
  authentication frame. Nothing in this branch changes what the driver or the
  firmware does; the only code that turns the mode on is the hostapd patch, in
  userspace. It uses two things the vendor driver already had: vendor command OUI
  `0x001374`, subcommand 43 (`vendor_set_sae_password` in `sc2355/vendor.c`, which
  sends `CMD_SET_SAE_PARAM` to the chip) and the IE buffer of the `NEW_STATION` event.
- **Kernels.** One driver source serves 5.4 (the vendor tree plus
  `kernel/patches/wlan_combo-*.patch`) and 6.18/7.2 (`upstream/modules/sprd_wlan_combo`),
  and the vendor command is in both. Measured only on 7.2.9. On 5.4 and 6.18 the
  driver builds with these changes and the option has never been run.
- **Devices.** The switch is the option, not a check of the device. The driver
  refuses the vendor command unless the firmware reports soft-AP WPA3
  (`SPRD_EXTEND_SOATAP_WPA3`); hostapd then logs `U30 SAE: firmware configuration
  failed`, the access point does not start, and the guard below puts WPA2 back.
  The F50 has the same chip and driver; whether its firmware reports the capability
  is not known, nobody has tried.
- **What the password request really carries: the passphrase, nothing else.** The
  driver parses the group attribute and never counts it (`group_count` stays 0 in
  `vendor_set_sae_password`), so no group reaches the firmware; and an ACT of
  `0xffffffff` is the driver's "not set" (`setting->act != -1`), so no
  anti-clogging threshold is sent either. Group 19 and the anti-clogging behaviour
  are the firmware's own defaults. hostapd's `sae_groups` and
  `sae_anti_clogging_threshold` have no effect in this mode.
- **What hostapd still does:** validation of the RSN element, the four-way handshake
  (with the imported PMK), mandatory PMF, group key handling.
  **What it no longer does:** the SAE exchange itself: commit and
  confirm, anti-clogging, group negotiation, the choice between hunting-and-pecking
  and H2E. The firmware does hunting-and-pecking only; how well it resists the
  timing attacks on that method (Dragonblood) is unknown, the firmware is closed.
- **Other clients and modes.** A WPA2 client cannot join such a BSS: the patch
  accepts pure SAE with required PMF only, and refuses WPA2/WPA3 mixed mode, optional
  PMF and a second BSS when the configuration is loaded. The Wi-Fi client (uplink)
  mode is not touched: joining a WPA3-only network still does not work
  ([FINDINGS](FINDINGS.md) 14b).
- **Where the secrets go.** The passphrase: `/etc/config/wireless` and the generated
  `/var/run/hostapd-phy0.conf` (as with WPA2), then one netlink vendor command, a
  copy on the kernel's stack and heap that the driver does not wipe, and
  `CMD_SET_SAE_PARAM` over PCIe into the chip's memory. The PMK comes back with every
  association, in the `NEW_STATION` event, through nl80211 into hostapd's PMKSA
  cache. With this branch's module and package pair none of it is written to a log
  (see "Protocol integration").
- **Downgrade.** None over the air on such a BSS: pure SAE, PMF required, no
  transition mode. The guard below is a downgrade made on the device itself, and it
  is logged as an error.

## When the access point cannot start: the way back

Until 2026-10-13 a BSS with the option that could not start stayed down, at every
boot, and a device reached only over its hotspot had no way in but USB. Three roads
lead there: an update (`mu300-update` keeps `/etc/config` and replaces the packages:
the stock `wpad-basic-mbedtls` does not know `u30_sae_offload` and refuses the whole
configuration), a firmware that refuses the vendor command, and a configuration the
patch rejects (mixed mode, optional PMF, a second BSS).

`/opt/mu300/bin/openwrt-wifi-sae-guard` is in every OpenWrt image and does nothing
unless a `wifi-iface` has `u30_sae_offload=1` in its `hostapd_bss_options`:

- **At boot, before the first `wifi up`** (`mu300-hw`): the installed hostapd does
  not have the option (its name is not in the binary). The option is taken out
  before hostapd ever sees it.
- **About two minutes after boot** (`mu300-post`, after its one `wifi up` retry and
  a further 60 s of waiting, long enough for automatic channel selection): the
  access point is still not `ENABLED` in `ubus call hostapd.wlan0 get_status`.

In both cases the BSS becomes `encryption psk2` with the same passphrase,
`ieee80211w` and `sae_pwe` are deleted, the file as it was is kept in
`/etc/mu300/wireless.sae-offload` (root only), and `logread` has one line from
`mu300-wifi`: `WPA3 hotspot (U30 SAE offload): ... The hotspot is back on WPA2 with
the same password`. WPA2 and not hostapd's own SAE: without the offload a WPA3-only
BSS on this driver lets nobody in. The price is the one WPA3 was chosen to avoid:
a captured WPA2 handshake can be attacked offline, so use a long passphrase.

What this means in practice:

- **If the hotspot is gone after a change, reboot and wait three minutes.** It comes
  back as WPA2. The guard acts at boot only; a change made live that takes the
  access point down leaves it down until the reboot.
- It leaves a hotspot alone that is off on purpose: a disabled `wifi-iface` or
  radio, the Wi-Fi client or its scan mode, the Wi-Fi key's "hotspot off".
- To go back to the offload afterwards: fix the cause, then
  `cp /etc/mu300/wireless.sae-offload /etc/config/wireless` and the full restart
  below.
- **Over USB** (always works, and the only place to read the log of a hotspot that is
  down): `ssh root@192.168.78.1`, then `logread -e 'U30 SAE' -e mu300-wifi -e hostapd`.
  By hand, the same as the guard:
  `uci del_list wireless.ap0.hostapd_bss_options='u30_sae_offload=1'; uci set wireless.ap0.encryption=psk2; uci -q delete wireless.ap0.ieee80211w; uci -q delete wireless.ap0.sae_pwe; uci commit wireless; wifi down; /etc/init.d/wpad restart; wifi up`.

The guard is tested with stub `uci`/`ubus` under dash, bash and busybox ash
(`tests/test_u30_sae.py`); it has not run on a device yet.

## Outstanding PMK trust boundary (why this stays experimental)

Independent review in [PR #74](https://github.com/dikeckaan/mu300-linux/pull/74#issuecomment-6106003178)
confirmed the feature works on a U30 Air, including macOS SAE connections,
wrong-password rejection and WCN recovery. It also identified a key provenance
issue that is still open: `NL80211_ATTR_IE` contains client association-request
IEs as well as the firmware trailer. A unique trailing marker alone does not
prove that a PMK came from a completed firmware SAE exchange. MIC and PMF do
not validate that provenance when a client can choose the imported PMK. The
firmware is a closed blob and the driver exposes no separate SAE-completion
signal, so closing this needs a verified key-handoff interface that nobody has
yet. Until then: **do not rely on this mode where an attacker within radio
range who knows the vendor element format is part of your threat model.** It is
offered as an opt-in for people who want WPA3 on a U30 Air and accept that.
Moving the same unverified trailer into a vendor event would not, by itself,
resolve the trust boundary.

The remaining driver/hostapd changes, required firmware contract and
acceptance criteria are described in
[the implementation handoff](U30AIR-SAE-HANDOFF.md).

The tested U30 Air firmware performs AP SAE authentication in firmware, but
the standard hostapd AP setup neither supplies the vendor runtime password
request nor consumes the firmware's PMK handoff. A client can finish SAE and
then fail association/the WPA four-way handshake. Advertising SAE capability
alone does not implement those two vendor interfaces.

This patch is an explicit opt-in for the observed firmware, not a new default
for F50, other chipsets, WPA2/WPA3 transition mode, or OWE. The firmware uses
hunting-and-pecking with group 19. H2E-only clients and other SAE groups are not
covered by the measured result.

## Protocol integration

After a successful AP setup, hostapd sends OUI `0x001374`, subcommand `43` with
nested vendor data containing the passphrase (attribute 8), group 19 (6), and
ACT `0xffffffff` (7). This sets runtime firmware state and does not flash WCN
firmware. Configuration requires pure SAE, mandatory PMF, `sae_pwe=0`, firmware
AP SME, and one 8–63 byte passphrase. Per-station SAE passwords are unsupported.

The SC2355 firmware reports the derived 32-byte PMK and 16-byte PMKID in a
kernel NEW_STATION event as a trailing vendor IE: EID 221, length 52, prefix
`40:45:da:04`. Only one complete trailing element is accepted, only when the
opt-in is enabled and firmware setup succeeded on a pure SAE/fullmac BSS.
Malformed/duplicate/truncated metadata is rejected by a length-bounded parser.
An association without an accepted element gets no PMK at all, and the four-way
handshake then fails as it does with the stock hostapd.
The key goes to the normal PMKSA/authenticator path; normal RSN validation,
four-way handshake MIC checks and required PMF remain in place. The metadata
needs a trustworthy firmware origin; the current shared IE buffer does not
establish it. See the outstanding review finding above.

The driver previously printed SAE passphrases and command/event bytes. The
module changes and vendor-kernel patch remove password and station PSK logging,
omit raw command/response payload dumps, omit bulk PCIe/SDIO RX dumps, and
suppress the NEW_STATION event dump. Command identifiers, message types and
error/recovery diagnostics remain available. hostapd also avoids dumping the entire
NEW_STATION IE payload. Rebuild/install the matching Wi-Fi module to get that
log protection; an existing stock module still contains the original logging.

## Build and opt in

Keep USB management access while changing these settings. With
`u30_sae_offload=1`, an incompatible security mode (`sae-mixed`, WPA2), optional
PMF, or missing `sae_pwe=0` causes setup to fail and the AP to stop. The option
also requires PMKSA caching: leave `disable_pmksa_caching=0` (the default).
The helper now checks this before sending the firmware password request and
reports the unsupported configuration. Removing the offload option restores
the normal configuration path.
A hotspot that stays down this way comes back as WPA2 after a reboot
(see "When the access point cannot start" above).

This experiment supports **one enabled AP BSS per radio**. Do not add a
second SAE test SSID next to a WPA2 hotspot. The driver interface-combination
table permits one ordinary STA/AP interface and maps all ordinary APs to
`SPRD_MODE_AP`; this suggests a concurrency limitation, but is not proof of
the cause of the maintainer's outage. r9 rejects a multi-BSS configuration with
the option on, before hostapd starts AP interfaces, and checks the static SAE
requirements there too. It does not add multi-BSS support or guarantee that
netifd preserves an existing hotspot after rejecting the new configuration.

Enable, disable or change this mode only over USB/another independent management
path, using a full restart. Keep the existing SSID configuration backed up and
change the single AP in place:

```sh
wifi down
/etc/init.d/wpad stop
# Apply the intended configuration while the AP is down.
/etc/init.d/wpad start
wifi up
```

Do not use `wifi reload` to add a BSS or change the SAE mode/passphrase. In the
maintainer's r8 test, adding a second SAE BSS by reload stopped both hotspots
for at least 25 minutes. That device could only be reached over Wi-Fi, so its
logs were not available and the cause is still unconfirmed. The configuration
check is a support boundary, not a demonstrated repair of that outage. With
automatic channel selection, wait for AP-ENABLED before testing a client; the
local single-BSS restart took approximately 33 seconds.

For source review/builds only while the key-provenance finding is open:
use a dedicated [official OpenWrt 25.12.5 armsr/armv8 SDK](https://downloads.openwrt.org/releases/25.12.5/targets/armsr/armv8/).
Verify its checksum before extracting. Use an ASCII-only SDK path: the SDK's
package stripping scripts failed with a non-ASCII build path on the test host.
Install the SDK's pinned base feed before running the helper:

```sh
cd /absolute/path/to/sdk
./scripts/feeds update base
./scripts/feeds install -p base hostapd
```

The package recipe must have source
`ca266cc24d8705eb1a2a0857ad326e48b1408b20`, revision 1 (the official SDK's
pinned feed) or 5 (the previously recorded release feed). Other sources and
revisions are refused. The helper finds the SDK feed recipe, applies the patch
after OpenWrt's existing patches, bumps revision to 9, replaces the dedicated
SDK's `.config`, and builds the OpenSSL variant with its normal dependencies:

```sh
sh openwrt/build-u30-sae.sh /absolute/path/to/sdk /absolute/path/to/u30-sae-apks
```

Under amd64 emulation, the maintainer saw a Lua jobserver failure with a
parallel build. Use `MU300_JOBS=1` if that occurs; a serial build completed.

The output must contain a matching revision 9 `hostapd-common` and
`wpad-basic-openssl` pair. Start with a clean dedicated SDK; the helper refuses
an older local r6/r7/r8 patch rather than silently overwriting it. Revision 9
includes early single-BSS/configuration checks and the marker-length hardening: every complete
private OUI/type marker counts towards duplicate rejection, regardless of its
element length. This does not establish PMK provenance. The helper only exports
r9 package files, so stale r6/r7/r8 artifacts in the SDK are not included.
The image builder accepts that directory on the tested release:

```sh
MU300_SYSTEM=openwrt-luci MU300_SAE_APK_DIR=/absolute/path/to/u30-sae-apks \
  sh openwrt/build-rootfs.sh
```

Normal image builds keep `wpad-basic-mbedtls`. Supplying the APK directory
includes the patched OpenSSL package but still does not enable the vendor path
automatically. Enable it only on the tested U30 Air interface in LuCI/UCI:

```text
option encryption 'sae'
option ieee80211w '2'
option sae_pwe '0'
list hostapd_bss_options 'u30_sae_offload=1'
```

Set your own passphrase with OpenWrt's existing wireless settings. Inspect the
generated hostapd configuration locally and confirm `wpa_key_mgmt=SAE`,
`ieee80211w=2`, `sae_pwe=0` and `u30_sae_offload=1`. Do not publish that file.
The firmware request uses group 19 even if the generator lists more groups.

## Installation and rollback

Keep USB management access and back up the wireless configuration, installed
Wi-Fi packages/files and original `sprd_wlan_combo.ko` before installation.
On current mainline sources, also back up `wcn_bsp.ko`. Build/install the
matching WCN and WLAN module pair against the target kernel: the WLAN recovery
path added in #100 calls `wcn_mu300_recover`, exported by the updated WCN BSP.
Do not install a new WLAN module over an older BSP without that export.
The OpenSSL variant also requires `libopenssl-legacy`; let APK resolve that
from the official release feed, or download it locally for an offline install.
Install the matching `hostapd-common` and `wpad-basic-openssl` pair together.
Do not leave an earlier standalone `hostapd` binary in front of the package's
`hostapd -> wpad` symlink. Trust only packages you built or whose origin and
checksums you verified; locally built APKs are not signed by OpenWrt's release
key. This is an opt-in build recipe, not an official OpenWrt binary release.

When replacing packages, take Wi-Fi down, stop wpad and wait for all hostapd
processes to exit before removing the old wpad variant. After installation,
start wpad and bring Wi-Fi up. Do not unload/reload the WLAN module live:
a trial on this device panicked in bridge teardown when hostapd was still
operating an old netdev callback after module unload. Install the matching
module file and use a complete Linux reboot instead. For a module trial, an
early-boot restore guard must run before `mu300-hw` (START=12); keep the original
module available until both USB and fresh SAE client access are confirmed.
Restore packages, configuration and module from the backup, then reboot, if
the trial fails. No Android firmware/partition change is needed.

A plain reload after changing from OWE once left the tested firmware timing
out the four-way handshake (reason 15); a complete wpad restart restored it.
Same-configuration `wifi reload` passed with the final packages. This does not validate adding
a second BSS or changing security mode/password by reload; those operations
require the full restart described above. APK replacement after a later upgrade needs revalidation.

## Validation and limits

The 2026-10-13 follow-up starts from the maintainer's `707be31`, including
v2026.10.24 main and the recovery guard. Local SAE/parser/guard/SDK/privacy,
static, Wi-Fi-client and band checks completed successfully (135 cases, one
optional OpenWrt-container configuration case skipped); all five Wi-Fi startup
checks passed too. The parser ran with required UBSan. The hostapd and vendor
logging patches and SDK recipe are unchanged from the previously built r9 pair.

On the connected OpenWrt 25.12.5/7.2.9 U30 Air, the exact guard from `707be31`
was copied to `/tmp` and run over USB with the installed r9 package pair. The
real configuration had the offload option and the AP was ENABLED. Both
`precheck` and `verify` returned without changing the wireless configuration
(the before/after hashes matched). The AT owner PIDs stayed unchanged, and the
final public SIM status reported active/default SIM2 with RF ready and WAN up.
The OpenWrt-first/five-failed-boots policy also remained unchanged.

Eight additional cases ran on that device's real `/sbin/uci` and BusyBox ash,
with **both the UCI configuration and delta directories isolated under `/tmp`**
and a synthetic passphrase. `ubus`, `wifi`, `logger` and the wpad service were
stubs; they did not stop or reconfigure the real radio. Stock-hostapd precheck
and missing-AP verification removed only the offload list entry, preserved the
other entry and passphrase, selected WPA2, removed the SAE/PMF options and
saved a byte-identical mode-0600 backup. Missing-AP verification recorded the
expected `wifi down`, `wpad restart`, `wifi up` order. Healthy, disabled,
client/scan/key-off marker and no-opt-in cases left the fixture unchanged.

This checks the normal guard path on the real AP and the recovery mutations
using real UCI. It does not establish recovery from an actual firmware
rejection, the original r8/new-module outage, or a boot with a replaced wpad
package. No packages/modules were replaced or services restarted in this run,
and no new APK build is claimed. The PMK trust finding below remains open.

The maintainer recovered the #165 test device to its stock Wi-Fi stack and
updated it to v2026.10.21. The original r8 live-add trace was lost after a
power cycle; the surviving removal trace had four beacon-parameter failures,
both BSSs disabled, then the primary AP enabled four seconds later. That
trace does not establish the cause of the original live-add outage.

A further r9 live-add test on 2026-10-12 used continuous `logread -f` over
USB, with a configuration backup and automatic restore. A single WPA2 primary
BSS was brought up using a full restart on channel 149/VHT80. Adding an
opt-in SAE secondary BSS with `wifi reload` produced the explicit
`U30 SAE: supports a single BSS only` configuration error. The existing AP
was disabled as well: **r9 rejects the unsupported configuration but does not
preserve the running primary hotspot during a netifd reload.** Restoring the
original single SAE BSS with a full wpad/Wi-Fi restart enabled it three seconds
after the restore marker. Auto/VHT80 and the original security configuration
were restored, and SIM2 WAN stayed up. The same previously installed modules
were used; this tests r9's rejection/recovery, not the original r8 failure
with the maintainer's freshly built module. Raw USB logs remain private.

The 2026-10-12 follow-up retains the maintainer's #165 commits and merges
main `c47e2c9`. r9 adds a configuration-stage single-BSS restriction and static
SAE requirements, with 16 SAE/parser/SDK/privacy tests passing (required UBSan)
and 47 static checks passing. The matching r9 pair builds through the official
SDK (`MU300_JOBS=1`). r9 was subsequently installed on the local U30 Air (OpenWrt 25.12.5,
kernel 7.2.9) with offline r8 rollback packages retained. The actual hostapd
binary rejected three synthetic configurations at configuration load: the
first or second BSS opting in on a two-BSS radio, and a single WPA2 BSS opting
in. Each exited with its explicit U30 configuration error before driver
initialization; the running single-BSS configuration stayed unchanged.

An independent Linux Wi-Fi interface completed pure SAE with required PMF,
DHCP and router HTTP (200). A complete wpad/Wi-Fi restart and a fresh client
connection also passed. Certificate-verified TLS 1.2 HTTPS to the same site
returned 200 over Wi-Fi and USB. The wireless configuration and both module
files were unchanged. This validates r9 with the previously installed modules;
it is not a new-module test or a live second-BSS reload reproduction. It does
not establish why the second r8 BSS stopped the maintainer's radio or prove
recovery from that case. The PMK trust finding
remains open. Existing r8 device results below apply to r8, not r9.

The latest follow-up is rebased onto main `7a044d5`. The marker-length
hardening is packaged as r8 rather than reusing r7 for different source. The
SDK helper exports only r8 packages and refuses an earlier locally patched
SDK revision. SAE/parser/SDK/privacy tests passed (15, with required UBSan),
Wi-Fi bring-up/client tests passed (64), and static checks passed (42).
The matching r8 pair was built through the official SDK and installed over
r6 on the local U30 Air (OpenWrt 25.12.5, kernel 7.2.9), with offline r6
packages retained for rollback.

A separate managed Linux Wi-Fi interface kept the home Wi-Fi connection up
while testing the U30. Pure SAE with required PMF completed association,
DHCP and router HTTP (200). A full wpad restart and a subsequent client
reassociation also passed. Certificate-verified TLS 1.2 HTTPS to
`www.baidu.com` returned 200 over both the test Wi-Fi and USB in a same-target
comparison. An earlier HTTPS request to `connectivitycheck.gstatic.com`
timed out; this is not recorded as a successful internet check.
With the existing automatic channel selection,
the AP took approximately 33 seconds to become enabled after the restart;
an early connection attempt before that reported the SSID unavailable.
The wireless configuration and both module files were unchanged. This test
used the previously installed modules, not a new build of the rebased pair:
the latest driver privacy changes and #100 recovery path were not installed
or hardware-tested in this follow-up. The PMK provenance finding remains open.

The follow-up logging/configuration revision was rebased onto main `53ebbf5`.
Its hostapd-common/wpad-basic-openssl r7 pair was built through the official
SDK. The SAE/parser/SDK and privacy regressions passed (12, with required
UBSan); Wi-Fi bring-up/client tests passed (64) and static checks passed (40). The changed mainline `cfg80211.c`, `cmdevt.c`, `pcie.c`, `sdio.c` and
`vendor.c` compiled against the prepared 7.2.9 kernel. These were individual
object checks, not a complete module build. r7 packages and the revised modules
were not installed on the device; the PMK trust finding is still unresolved.

The review update rebases this branch onto main `9d21c77`, including #100's
Wi-Fi recovery/backoff and rate-limited logging changes. Those changes are
retained; the SAE hostapd patch and driver privacy filters are unchanged.
After rebasing, the SAE tests passed again (9, with required UBSan), Wi-Fi
bring-up passed (5), Wi-Fi client regressions passed (59), and static checks
passed (40). The rebased `cmdevt.c`, `vendor.c`, `common/iface.c` and WCN
`platform/wcn_procfs.c` also compiled against the prepared Linux 7.2.9 tree.
This was an object compilation check, not a complete kernel/module build or
a hardware test of the new recovery path. The device results below were
obtained before this rebase, from revision `4d61379`; the updated module pair
has not been installed on that device. Independent testing of the rebased
sources is still pending.

The hardware-tested patch was built through the official OpenWrt 25.12.5 armsr/armv8 SDK
(GCC 14.3/musl), producing revision 6 `hostapd-common` and
`wpad-basic-openssl` APKs. The official SDK's base feed is pinned at
`f0a60eee2fe051741c643ea6118718aae1ef17fb`. SDK download SHA-256:
`1b0316604a3e820b2b008a1baff3f9dac6716af942bef800930e58c7de98c98b`.
These packages, plus the official `libopenssl-legacy` dependency, were installed
on one U30 Air with OpenWrt 25.12.5 and Linux 7.2.9. The private-log-suppressed
WLAN module was rebuilt against the matching prepared kernel/configuration and
successfully loaded through a complete Linux reboot. No module versioning was
enabled on the tested kernel; this local module is not interchangeable with a
module for another kernel build. Both mainline module sources and the vendor
kernel's equivalent patch are included; the vendor-kernel variant was not
hardware-tested in this run.

A Linux NetworkManager client on an independent interface, using a fresh MAC
and required PMF, completed pure SAE, the WPA four-way handshake and DHCP.
Router HTTP returned 200 and external HTTPS returned 200 with certificate
verification (direct curl, no proxy, TLS 1.2). An incorrect password never
established a connection within the 25-second test window; the correct password
connected afterwards. Same-configuration Wi-Fi reload, a complete wpad service restart, and fresh
reconnection passed. External connectivity had transient timeouts; repeated checks over
Wi-Fi and a router-local comparison returned 200. Original password-log
markers were absent after the new module boot. Raw device/client logs and
credentials are not part of this contribution.

Other phones/clients, H2E-only clients, alternate SAE groups and the final Docker
rootfs assembly have not been independently verified. Delivery here is the
SDK-built package pair and a tested existing-device installation, rather than
a flashed replacement image. Full rootfs assembly remains an optional image
builder path. No claim is made about OWE or SAE client/station mode.

Before the review rebase, the SAE/parser/SDK tests passed (9 cases), Wi-Fi
bring-up passed (5), Wi-Fi client regressions passed (58), and static tests
passed (40). A broad desktop run
executed 1056 tests with 86 skips and two pre-existing VPN test failures: the
host has a real `/usr/bin/mihomo`, and its `ls` appends the SELinux mode suffix.
The SAE branch does not modify either VPN test or implementation. The SAE tests also passed with `MU300_TEST_UBSAN=1`. This desktop initially
lacked the UBSan runtime; the matching signature-verified Fedora library was
extracted into a private build directory and used without a system installation.
The Linux CI job now requires a successful UBSan run rather than silently
falling back. These local results
are not a claim that all CI checks pass.

`tests/test_u30_sae.py` compiles the actual parser from the patch and exercises
every truncation, exact/duplicate/non-trailing elements and 500 deterministic
malformed inputs. UBSan is used when its compiler/runtime is available; require
it in CI with `MU300_TEST_UBSAN=1`. The tests also check the official SDK feed layout/revision, missing-feed diagnostics,
recipe pinning, matching package collection, and refusal to overwrite a
conflicting patch.

OWE remains unresolved: two independent tests reached association status 43
even with current userspace support. This SAE fix is not evidence of an OWE
fix, and does not alter regulatory/DFS behavior or any Android partition.
