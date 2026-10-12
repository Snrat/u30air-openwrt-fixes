"""Exercise the actual API negotiation function against truncated firmware replies."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from helpers import TOP


class FirmwareApiReply(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('no native C compiler')
        cls.work = tempfile.TemporaryDirectory(prefix='sc2355-api-')
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        source = (TOP / 'upstream/modules/sprd_wlan_combo/sc2355/cmdevt.c').read_text()
        function = source[source.index('int sc2355_sync_version('):source.index('\nstatic int cmdevt_download_ini(')]
        prefix = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
struct sprd_priv { int unused; };
struct cmd_api_t { u32 main_ver; u8 api_map[256]; };
struct sprd_msg { void *data; };
#define CMD_SYNC_VERSION 9
#define pr_warn(...) do {} while (0)
static unsigned int reply_length;
static int reply_error, allocation_error, fills;
static struct cmd_api_t request;
static struct sprd_msg message = { &request };
static struct sprd_msg *get_cmdbuf(struct sprd_priv *p, void *v, size_t n, int cmd) {
    (void)p; (void)v; (void)n; (void)cmd;
    return allocation_error ? NULL : &message;
}
static void sc2355_api_version_fill_drv(struct sprd_priv *p, struct cmd_api_t *api) {
    (void)p; memset(api, 0, sizeof(*api));
}
static int send_cmd_recv_rsp(struct sprd_priv *p, struct sprd_msg *m, u8 *buf, u16 *n) {
    struct cmd_api_t reply = {0};
    (void)p; (void)m;
    reply.api_map[77] = 1; reply.api_map[161] = 1;
    memcpy(buf, &reply, reply_length < *n ? reply_length : *n);
    *n = reply_length;
    return reply_error;
}
static void sc2355_api_version_fill_fw(struct sprd_priv *p, struct cmd_api_t *api) {
    (void)p; (void)api; fills++;
}
'''
        suffix = r'''
int main(int argc, char **argv) {
    struct sprd_priv priv = {0};
    if (argc != 4) return 2;
    reply_length = strtoul(argv[1], NULL, 10);
    reply_error = atoi(argv[2]); allocation_error = atoi(argv[3]);
    int ret = sc2355_sync_version(&priv);
    printf("%d %d\n", ret, fills);
    return 0;
}
'''
        (work / 'check.c').write_text(prefix + function + suffix)
        cls.binary = work / 'check'
        subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', str(work / 'check.c'), '-o', str(cls.binary)], check=True, capture_output=True)

    def reply(self, length, error=0, allocation=0):
        result = subprocess.run([str(self.binary), str(length), str(error), str(allocation)], check=True, capture_output=True, text=True)
        return tuple(map(int, result.stdout.split()))

    def test_every_short_reply_is_rejected_without_publishing(self):
        for length in range(260):
            with self.subTest(length=length):
                status, fills = self.reply(length)
                self.assertLess(status, 0)
                self.assertEqual(fills, 0)

    def test_complete_reply_is_published(self):
        self.assertEqual(self.reply(260), (0, 1))

    def test_oversized_reply_is_rejected(self):
        status, fills = self.reply(261)
        self.assertLess(status, 0)
        self.assertEqual(fills, 0)

    def test_transport_and_allocation_failures_do_not_publish(self):
        self.assertEqual(self.reply(260, error=-110), (-110, 0))
        self.assertEqual(self.reply(260, allocation=1), (-12, 0))


class FirmwareAcknowledgement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('no native C compiler')
        cls.work = tempfile.TemporaryDirectory(prefix='sc2355-ack-')
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        source = (TOP / 'upstream/modules/sprd_wlan_combo/sc2355/cmdevt.c').read_text()
        function = source[source.index('static int sc2355_checked_ack('):source.index('/* msg is released')]
        prefix = r"""
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
typedef uint8_t u8;
struct sprd_cmd_hdr { uint16_t plen; uint8_t status; };
struct cmd_api_t { uint32_t main_ver; u8 api_map[256]; };
#define le16_to_cpu(v) (v)
#define CMD_SYNC_VERSION 9
"""
        suffix = r"""
int main(int argc, char **argv) {
    if (argc != 5) return 2;
    struct sprd_cmd_hdr hdr = {atoi(argv[1]), atoi(argv[2])};
    printf("%d\n", sc2355_checked_ack(atoi(argv[4]) ? NULL : &hdr, atoi(argv[3])));
    return 0;
}
"""
        cls.binary = work / 'check'
        (work / 'check.c').write_text(prefix + function + suffix)
        subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', str(work / 'check.c'), '-o', str(cls.binary)], check=True, capture_output=True)

    def ack(self, length, status=0, command=9, missing=0):
        result = subprocess.run([str(self.binary), str(length), str(status), str(command), str(missing)], check=True, capture_output=True, text=True)
        return int(result.stdout)

    def test_sync_reply_size_is_checked_before_buffer_truncation(self):
        # The stub header is four bytes; the API payload is 260 bytes.
        for length in (0, 3, 4, 263, 265, 65535):
            self.assertEqual(self.ack(length), -71)
        self.assertEqual(self.ack(264), 0)

    def test_firmware_rejection_is_not_a_successful_ack(self):
        self.assertEqual(self.ack(264, status=1), -121)

    def test_missing_header_is_rejected(self):
        self.assertEqual(self.ack(264, missing=1), -71)

    def test_radar_ack_checks_header_and_status_without_assuming_a_payload(self):
        self.assertEqual(self.ack(4, command=77), 0)
        self.assertEqual(self.ack(3, command=77), -71)
        self.assertEqual(self.ack(4, status=1, command=77), -121)
