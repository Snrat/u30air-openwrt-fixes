# The web installer: how it is built, and the test it still needs

`site/web-installer/` (https://kaandikec.com/mu300-linux/web-installer/, in en/tr/zh) installs, manages and removes
MU300 Linux from Chrome or Edge over USB. It is **beta**: nothing of it has run on a device yet. The measurements
behind its design are FINDINGS 72; this file says what runs where, how to test it without a device, and the protocol
for the first run on an F50.

## What runs where

```
 browser (static page, no server)                    device, rooted Android
 ----------------------------------                  ---------------------------------------------
 app.js       the screen, strings.js (en/tr/zh)
 flow.js      what fits, what is refused, answers
 github.js    release list from api.github.com
 zip.js       reads the picked zip's manifest
 device.js    AndroidDevice / LinuxDevice   --adb--> /data/local/tmp/mu300-web/android-web.sh   (pushed by the page)
 adb/*        adb over WebUSB: auth, shell v2, sync      |  under su, Magisk's busybox, ASH_STANDALONE=1
 privacy.js   masks every line a device prints           v
                                                     /data/adb/mu300-web/   (root only)
                                                        zips/<zip>   checked against the release's sha256
                                                        x/<zip>/mu300/install.sh   the zip's own installer
                                                        answers.conf   MU300_ANSWERS (root's, mode 600)
                                                        job/{log,rc,pid}   the run, in a session of its own
```

* **Install** is the Magisk zip's installer (`android/magisk/installer/mu300-install.sh`, in every zip as
  `mu300/install.sh`), started the way Magisk's `customize.sh` starts it, with `MU300_ANSWERS` (the wizard's choices)
  and `MU300_PROGRESS=1` (`MU300-STEP n/9 name`, `MU300-PLAN key=value`). The page runs it twice per zip: once with
  `MU300_DRY_RUN=1` to show the device's own plan, then for real after the user typed INSTALL. Two systems are two
  zips. Nothing about installing is reimplemented in JavaScript.
* **Backup, uninstall, status, "start Linux now"** are `tools/android-web.sh` (uninstall in `uninstall.sh`'s order,
  with `tools/storage.sh` and the bc functions of `tools/android-boot-image.sh`; starting Linux is the Magisk
  module's `switch.sh`).
* **Managing Linux** is `LinuxDevice` in `device.js`: one adapter around "a command over adb shell, an answer back".
  It asks `mu300-device --json` first (format 1, `docs/ADB-INTERFACE.md`: device, system, release, kernel, root,
  what adb does) and reads what that does not carry - and, on an older image, everything - with commands every
  image has. The actions are `mu300-update check|apply|kernel`,
  `mu300-os`, `mu300-next-boot`, `reboot`.
* **Not in the page, on purpose:** shrinking `userdata` on the 32 GB variant and growing it back (the page shows the
  figures and points to `install.sh` / `uninstall.sh`), rooting, unlocking, restoring single partitions from a
  backup (its README.txt has the two commands).

The device scripts the page pushes are this repository's (`tools/`, `android/magisk/`), copied next to the page with
their checksums by `site/tools/stage.sh`, which `.github/workflows/pages.yml` runs. Nothing else is built.

## Testing without a device

```sh
node --test tests/web/                       # the adb client, the logic, the device classes (Node 20+)
python3 -m unittest discover -s tests -p 'test_web_installer.py'
python3 -m unittest discover -s tests -p 'test_android_web.py'      # needs busybox; runs the page's code end to end
```

* A real adbd, over TCP (the page's adb client, not its USB transport):
  `docker run --rm -d -p 127.0.0.1:15555:5555 ubuntu:26.04 sh -c 'apt-get update && apt-get install -y adbd && exec adbd'`,
  then `MU300_ADBD_TCP=127.0.0.1:15555 node --test tests/web/interop.test.mjs`. Never point it at a device.
* The page itself: `sh site/tools/stage.sh /tmp/mu300-site && python3 -m http.server -d /tmp/mu300-site 8300`, then
  `http://localhost:8300/web-installer/?demo=android` (also `installed`, `small`, `linux`): the whole wizard with
  made-up answers and no USB device. `?dev` lets a zip that is not a release's file through (its checksum is then
  the file's own): for zips built with `tools/make-magisk-zips.sh` before a release exists.
* The same client from Node against a device, without a browser: the `usb` package's `webusb` object is a WebUSB
  implementation, and `adb/usb.js` takes its `USBDevice` as it is.

  ```js
  // npm install usb   (outside the repository; not a dependency of anything here)
  import { webusb } from "usb";
  import { AdbConnection } from "./site/assets/web-installer/adb/connection.js";
  import { generateKey } from "./site/assets/web-installer/adb/key.js";
  import { ADB_FILTERS, openUsbTransport } from "./site/assets/web-installer/adb/usb.js";
  import { shell } from "./site/assets/web-installer/adb/shell.js";
  const device = await webusb.requestDevice({ filters: ADB_FILTERS });
  const conn = new AdbConnection(await openUsbTransport(device), { key: await generateKey("node"), onAuth: console.log });
  await conn.connect("node");                    // allow the key on the device when it asks
  console.log(await shell(conn, "getprop ro.product.model"));
  ```

## The first run on a device: protocol

For the owner, with a test F50 (rooted with Magisk, USB debugging on), Chrome on the Mac, and a release (or
prerelease) that contains this branch - or zips built from it with `tools/make-magisk-zips.sh` and the page opened
with `?dev`. Every step lists what must appear. **Stop at the first step that does not match and save the log**
(the page's "Save the log"); nothing before step 9 writes to the device's storage.

| # | Do | Must appear | If not |
|---|---|---|---|
| 0 | `adb kill-server` in a terminal. Open the page. | The "Connect the device" panel; no error in DevTools' console. | Note the console text. |
| 1 | Click **Connect**. | Chrome's chooser lists the F50 (as "F50" or its product name). | The page says why (`no-interface`: USB debugging off). On Windows: FINDINGS 72.3. |
| 2 | Pick the device, **Connect**. | "Allow this browser on the device"; Android asks to allow USB debugging. Allow it. | Nothing on the device: note it; this is the unmeasured part of the handshake. |
| 3 | Wait. | Model, masked serial (click "show": it must be `adb devices`' serial), Android version, slot. **Root: asking**, Magisk's prompt for "Shell" (allow), then **granted (Magisk)**. Bootloader: unlocked / locked / not reported. | "not granted": allow Shell in Magisk, "Try again". |
| 4 | Read the overview. | The storage bar; "This device has no Linux yet" (or the installed systems, as `mu300-os` lists them). Open the log: only masked serials, no IMEI. | Compare the figures with `./install.sh --check`. |
| 5 | **Install Linux**, step Storage. | The verdict `./install.sh --check` gives ("... same layout as the tested device" on the 64 GB F50). | - |
| 6 | Choices: OpenWrt, kernel 5.4, defaults. No password. | The release's tag; no beta question unless a newer prerelease exists. Next is enabled. | - |
| 7 | Files: **Download**, then **Choose the downloaded file**. | "the right file". Try a wrong zip once: it must be refused with its system and kernel named. | - |
| 8 | Backup: choose a folder. | `mu300-backup-<date>/` with `boot_b.img`, `uboot_b.img`, `sml_b.img`, `teecfg_b.img`, `trustos_b.img`, `vbmeta_b.img`, `misc-head.bin`, `gpt-*.bin`, `partitions.txt`, `README.txt`. `shasum -a 256 boot_b.img` equals `adb shell su -c 'sha256sum /dev/block/by-name/boot_b'` (in another terminal: `adb kill-server` again before going on). | - |
| 9 | Next (Install step). | A progress bar while the zip is sent (note the speed), then the device's plan in a table: storage internal, a new filesystem, slot b. "The plan in the device's own words" shows the installer's text. **Nothing is written yet**: `adb shell su -c 'ls /data/adb/mu300-web'` shows `zips x job`. | "The device's installer refuses this": its message is shown. |
| 10 | **Abort test.** Type INSTALL, click Install, and when the list shows "Collecting the Wi-Fi and modem files", **close the tab**. Open the page again, connect. | The page shows the running (or finished) job with its log from the start. This is the unmeasured claim "a job does not need its watcher". | If the job died with the tab: the list stops, "interrupted". Nothing is armed (step "arm" not done): note it, run again. |
| 11 | Let it finish. | All nine steps done, "Linux is installed", SSH line `root@192.168.77.1`. In the log: `MU300-INSTALL-OK`, "Writing and verifying boot_b". | "It did not finish": the device still boots Android unless "Arming" was done. |
| 12 | **Restart into Linux.** | The device restarts; about a minute later `ssh root@192.168.77.1` works. | Falls back to Android after 5 tries: collect logs (`tools/collect-logs.sh`). |
| 13 | In Linux, click Connect again. | The chooser lists a new device (`MU300 Linux`). With "Allow this browser to manage Linux" ticked at step 6 it connects without a prompt and shows "Manage MU300 Linux". | "Allow this browser": the key was not authorised; run the command the page shows over SSH. |
| 14 | Check for updates; Read the logs. | `mu300-update check`'s text; logs with MAC addresses masked. | - |
| 15 | **Restart into Android**, connect, **Uninstall Linux**, quick, type UNINSTALL. | Seven steps, "Linux is removed". `adb shell su -c mu300-linux status` (or the overview) says boot_b is a copy of Android's. | - |

How to abort safely at any point: close the tab or unplug nothing and click Cancel/Disconnect. Before step 9
nothing is on the device but files in `/data/local/tmp/mu300-web` and `/data/adb/mu300-web` (`clean` removes them;
so does `adb shell su -c 'rm -rf /data/adb/mu300-web /data/local/tmp/mu300-web'`). After "Install", do not unplug
an F50 (it has no battery); a closed tab is fine. If the page misbehaves in any way, the device side can always be
finished or inspected from a terminal: `adb shell su -c 'sh /data/local/tmp/mu300-web/android-web.sh status deep'`
and `... follow`.

After this protocol has passed on an F50 (and, for the SD card and battery cases, a U30 Air is not needed: it has
no slot), the "beta" callout on the three pages and in the wiki can go, and the two-system run, the SD card target,
the VPN module, the device-download fallback and Windows are the next things to try.
