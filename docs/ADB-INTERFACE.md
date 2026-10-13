# The adb interface for tools

What a program (the web installer over WebUSB, a GUI, a script) can rely on when it talks to a device running
MU300 Linux over adb. The rest of adb to Linux - the daemon, the gadget, who is let in and why - is in
[FINDINGS 43 and 70](FINDINGS.md).

## Is this our Linux, before any command runs

The USB serial number, which is also the serial in `adb devices` and the one WebUSB reports:

| The device runs | Serial |
|---|---|
| MU300 Linux (Ubuntu or OpenWrt) | `MU300LINUX-<id>`, e.g. `MU300LINUX-324950664950-203d8329` (boot/init; `MU300LINUX` alone when the board gave no id) |
| Android | the bare number, e.g. `324950664950` |

The adb interface is class `0xff`, subclass `0x42`, protocol `0x01`, as on Android; VID:PID `0525:a4a1`.

A device in Linux is `device` in `adb devices` when it lets this computer in, and `unauthorized` when its adb takes
authorised keys only and this computer's key is not one of them (`mu300-usb adb auth keys`). It is not listed at all
when adb is off there (`mu300-usb adb off`), in the U30 Air's USB host role, or on a kernel without FunctionFS.

**The shell is root, in Linux too.** `adb shell su -c 'id -u'` prints `0` on Ubuntu, so "su works" does not mean
"rooted Android". Tell the two apart by the serial, and by the marker below before writing anything.

## What it is and what state it is in

```sh
adb shell /opt/mu300/bin/mu300-device --json
```

One line of JSON on stdout, exit status 0, nothing on stderr:

```json
{"mu300":1,"device":"f50","system":"openwrt-luci","version_id":"25.12.5","release":"v2026.10.23","kernel":"7.2.10","root":true,"adb":"open"}
```

| Field | Type | Values |
|---|---|---|
| `mu300` | number | The version of this format: `1`. Fields may be added without changing it; it changes when a field goes away or changes meaning |
| `device` | string | `f50` or `u30air` |
| `system` | string | `ubuntu`, `openwrt` or `openwrt-luci`: the system that is running (the name of its directory on the Linux partition; a system of the user's own has its own name) |
| `version_id` | string | `VERSION_ID` of the system's `/etc/os-release`: `24.04` or `26.04` for Ubuntu, OpenWrt's version for OpenWrt; empty when there is none |
| `release` | string | The mu300-linux release the running system's image came from (`/etc/mu300/image-version`), e.g. `v2026.10.23`; `unknown` when the file is missing, `dev` for an image built without a version |
| `kernel` | string | `uname -r`: `5.4.…`, `6.18.…` or `7.2.…` |
| `root` | boolean | Whether the caller is root. Always `true` over adb; `false` when a program runs it over SSH as `ubuntu` |
| `adb` | string | `open` (every computer is let in), `keys` (only authorised keys), `off`, or `unavailable` (no adbd or no FunctionFS in this system) |

Strings hold only `A-Z a-z 0-9 . _ + -` (anything else is dropped, at most 64 characters), so the line needs no
escaping and no field can be forged by a file's contents. The path is given in full on purpose: `mu300-device` is on
the `PATH` of an adb shell too, but a tool should not depend on it.

On a system from before this command had `--json` (v2026.10.23 and older) the same call prints `f50` or `u30air` and
nothing else: treat output that does not parse as JSON as "an older release", and fall back to the marker.

**The marker**, for a yes/no answer on any release with adb: `adb shell 'test -x /opt/mu300/bin/mu300-next-boot && echo mu300'`
prints `mu300` on MU300 Linux and nothing on Android (there is no `/opt` there).

## More state, as text

These are for people; their wording can change. Use them when the JSON has no field for what is needed, and say so in
an issue, so that the field can be added.

| Command | Says |
|---|---|
| `mu300-update version` | the installed image, the systems on disk with their releases, the kernel and the boot image's release |
| `mu300-os` | the systems on disk and which one boots next |
| `mu300-usb adb status` | adb on or off, whom it lets in and why, whether adbd can run, the number of authorised keys |

## Sending the device back to Android

```sh
adb -s MU300LINUX-<id> shell '/opt/mu300/bin/mu300-next-boot android && sync && reboot'
```

The device leaves `adb devices` within seconds and comes back as Android with its own serial. This is what
`install.sh`, `install.ps1` and the uninstallers do when they find the device in Linux.

## What the interface does not promise

* That adb is there. It is on by default and open to every computer (the owner's decision of 2026-10-13, FINDINGS
  70), but the owner of a device can restrict it (`mu300-usb adb auth keys`) or turn it off (`mu300-usb adb off`),
  and a device that had adb set up under v2026.10.20-23 stays keys-only until told otherwise. A tool that meets
  `unauthorized` should say: on the device, `sudo mu300-usb adb key FILE` for this tool's public key, or
  `sudo mu300-usb adb auth open`.
* A network transport. adbd has no TCP or vsock listener in either mode; `adb tcpip` and `adb connect` do not work.
