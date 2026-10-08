# U30 Air SC2355 firmware SAE offload (experimental)

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
The key goes to the normal PMKSA/authenticator path; normal RSN validation,
four-way handshake MIC checks and required PMF remain in place. The metadata
must be trusted kernel/firmware output, never an over-the-air management frame.

The driver previously printed SAE passphrases and command/event bytes. The
module changes and vendor-kernel patch remove password logging and suppress
key-bearing command/NEW_STATION dumps. hostapd also avoids dumping the entire
NEW_STATION IE payload. Rebuild/install the matching Wi-Fi module to get that
log protection; an existing stock module still contains the original logging.

## Build and opt in

Use a dedicated [official OpenWrt 25.12.5 armsr/armv8 SDK](https://downloads.openwrt.org/releases/25.12.5/targets/armsr/armv8/).
Verify its checksum before extracting. The package recipe must have source
`ca266cc24d8705eb1a2a0857ad326e48b1408b20`, revision 5. The helper applies the
patch after OpenWrt's existing patches, bumps revision to 6, replaces the SDK
`.config`, and builds the OpenSSL variant with its normal SDK dependencies:

```sh
sh openwrt/build-u30-sae.sh /absolute/path/to/sdk /absolute/path/to/u30-sae-apks
```

The output must contain a matching `hostapd-common` and `wpad-basic-openssl`
pair. The image builder accepts that directory on the tested release:

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

Use a complete `wifi down`, wpad stop/start, then `wifi up` when changing from
OWE or replacing the binary. A plain reload once left the tested firmware in
a state that timed out the four-way handshake (reason 15); a complete restart
restored fresh authentication. Hot reconfiguration remains a review item.
Keep a local original package/config backup for rollback and USB management
access while testing. APK replacement after a later upgrade needs revalidation.

## Validation and remaining work

The original seven-file integration patch was compiled with the OpenWrt
25.12.5 GCC 14.3/musl toolchain and OpenWrt ubus/ucode/APUP support, then deployed
on one U30 Air running Linux 7.2.9. After a full device reboot, a Linux client
with a fresh MAC completed pure SAE, the four-way handshake and DHCP. Router
HTTP returned 200 and external HTTPS returned 204 over Wi-Fi (no proxy, TLS 1.2).
A wrong password was rejected; the correct password worked again afterwards.
Other phones/clients have not been independently verified.

The contribution adds stricter configuration/setup gates, a separately tested
bounded parser, and private-log suppression. The resulting hostapd source was
cross-compiled successfully with the same OpenWrt toolchain and integration
configuration. These source refinements and rebuilt kernel module have not
been deployed as a fresh image. The SDK helper is tested with fake build
commands; the SDK APK pipeline and final Docker image still need end-to-end
build/hardware verification before removing draft status. Local device-linked
test binaries/libraries are deliberately not distributed as package artifacts.

`tests/test_u30_sae.py` compiles the actual parser from the patch and exercises
every truncation, exact/duplicate/non-trailing elements and 500 deterministic
malformed inputs. UBSan is used when its compiler/runtime is available; require
it in CI with `MU300_TEST_UBSAN=1`. The test also checks SDK recipe pinning,
matching package collection, and refusal to overwrite a conflicting patch.

OWE remains unresolved: two independent tests reached association status 43
even with current userspace support. This SAE fix is not evidence of an OWE
fix, and does not alter regulatory/DFS behavior or any Android partition.
