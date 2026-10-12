"""Actual prototype parser and terminal CAC callbacks, with kernel operations recorded."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from helpers import TOP


class DfsPrototype(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('no native C compiler')
        cls.work = tempfile.TemporaryDirectory(prefix='sc2355-dfs-')
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        source = (TOP / 'upstream/modules/sprd_wlan_combo/sc2355/dfs.c').read_text()
        def function(name):
            start = source.index('static void ' + name + '(')
            brace = source.index('{', start)
            depth = 1
            end = brace + 1
            while depth:
                depth += (source[end] == '{') - (source[end] == '}')
                end += 1
            return source[start:end]
        header = TOP / 'upstream/modules/sprd_wlan_combo/sc2355/dfs_event.h'
        prefix = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include "dfs_event.h"
#define WRITE_ONCE(a,b) ((a)=(b))
#define READ_ONCE(a) (a)
#define GFP_KERNEL 0
#define NL80211_RADAR_CAC_ABORTED 1
#define NL80211_RADAR_CAC_FINISHED 2
#define pr_info(...) do {} while (0)
struct wiphy { int unused; };
struct wiphy_work { int unused; };
struct sprd_vif { struct { struct { bool cac_started; } links[1]; } wdev; };
struct net_device { struct sprd_vif vif; };
static struct { struct net_device *ndev; int chandef, expiry, event_work; bool radar, invalid; } dfs;
static int dfs_lock;
static unsigned int dfs_status;
static bool dfs_fault;
static int stop_result, stops, aborted, finished, radars, released;
static void spin_lock_bh(int *v) { (void)v; }
static void spin_unlock_bh(int *v) { (void)v; }
static void wiphy_delayed_work_cancel(struct wiphy *w, int *work) { (void)w; (void)work; }
static void wiphy_work_cancel(struct wiphy *w, int *work) { (void)w; (void)work; }
static struct sprd_vif *netdev_priv(struct net_device *ndev) { return &ndev->vif; }
static int dfs_command(struct sprd_vif *v, unsigned int duration) { (void)v; (void)duration; stops++; return stop_result; }
static void cfg80211_radar_event(struct wiphy *w, int *c, int g) { (void)w; (void)c; (void)g; radars++; }
static void cfg80211_cac_event(struct net_device *n, int *c, int event, int g, int link) {
    (void)n; (void)c; (void)g; (void)link;
    if (event == NL80211_RADAR_CAC_ABORTED) aborted++;
    if (event == NL80211_RADAR_CAC_FINISHED) finished++;
}
static void dev_put(struct net_device *n) { (void)n; released++; }
'''
        suffix = r'''
int main(int argc, char **argv) {
    struct net_device n = {0}; struct wiphy w = {0}; struct wiphy_work work = {0};
    if (argc != 3) return 2;
    if (atoi(argv[1]) == 3) {
        struct sc2355_dfs_event_info out = {99,99};
        unsigned char data[3]; unsigned int len, region, type;
        while (scanf("%u %u %u", &len, &region, &type) == 3) {
            data[0] = region; data[1] = type; data[2] = 0;
            int ret = sc2355_dfs_parse_event(data, len, &out);
            printf("%d %u %u\n", ret, out.region, out.type);
        }
        return 0;
    }
    dfs.ndev = &n; n.vif.wdev.links[0].cac_started = true;
    stop_result = atoi(argv[2]);
    if (atoi(argv[1]) == 1) dfs.radar = true;
    if (atoi(argv[1]) == 2) dfs.invalid = true;
    if (atoi(argv[1]) == 4) n.vif.wdev.links[0].cac_started = false;
    if (atoi(argv[1]) == 5) { dfs.radar = true; dfs.invalid = true; }
    if (atoi(argv[1]) == 0 || atoi(argv[1]) == 4) { dfs_expired(&w, &work); dfs_expired(&w, &work); }
    else { dfs_received(&w, &work); dfs_received(&w, &work); }
    printf("%d %d %d %d %d %u %u\n", stops, aborted, finished, radars, released, dfs_status & 255, dfs_fault);
    return 0;
}
'''
        shutil.copyfile(header, work / 'dfs_event.h')
        (work / 'check.c').write_text(prefix + '\n'.join(function(f) for f in ('dfs_result','dfs_finish','dfs_expired','dfs_received')) + suffix)
        cls.binary = work / 'check'
        subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror',str(work / 'check.c'),'-o',str(cls.binary)],check=True,capture_output=True)

    def callback(self, mode, error=0):
        p = subprocess.run([str(self.binary),str(mode),str(error)],check=True,capture_output=True,text=True)
        return tuple(map(int,p.stdout.split()))

    def test_quiet_timer_aborts_and_never_completes_cac(self):
        self.assertEqual(self.callback(0), (1,1,0,0,1,2,0))

    def test_stop_failure_cannot_mark_a_channel_available(self):
        self.assertEqual(self.callback(0,-5), (1,1,0,0,1,2,1))

    def test_radar_reports_unavailable_and_aborts_cac_once(self):
        self.assertEqual(self.callback(1), (1,1,0,1,1,3,0))

    def test_malformed_event_aborts_without_fabricating_radar(self):
        self.assertEqual(self.callback(2), (1,1,0,0,1,5,0))

    def test_expiry_after_core_aborted_does_not_emit_a_second_cac_event(self):
        self.assertEqual(self.callback(4), (1,0,0,0,1,2,0))

    def test_radar_and_invalid_events_together_cannot_complete_cac(self):
        self.assertEqual(self.callback(5), (1,1,0,1,1,5,0))

    def test_stop_failure_during_radar_latches_the_fault_too(self):
        self.assertEqual(self.callback(1,-110), (1,1,0,1,1,3,1))

    def test_parser_rejects_truncation_extra_bytes_and_unknown_values(self):
        cases = [(n,1,1) for n in (0,1,3,255)] + [(2,r,t) for r,t in ((0,1),(4,1),(1,3),(255,255))]
        p=subprocess.run([str(self.binary),'3','0'],input=''.join(f'{n} {r} {t}\n' for n,r,t in cases),capture_output=True,text=True,check=True)
        for line in p.stdout.splitlines(): self.assertLess(int(line.split()[0]),0)

    def test_parser_accepts_only_the_candidate_v1_domain_and_type_values(self):
        cases=[(r,t) for r in (1,2,3) for t in (0,1,2)]
        p=subprocess.run([str(self.binary),'3','0'],input=''.join(f'2 {r} {t}\n' for r,t in cases),capture_output=True,text=True,check=True)
        self.assertEqual([tuple(map(int,l.split())) for l in p.stdout.splitlines()],[(0,r,t) for r,t in cases])


class DfsCommandContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('no native C compiler')
        cls.work = tempfile.TemporaryDirectory(prefix='sc2355-dfs-contract-')
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        source = (TOP / 'upstream/modules/sprd_wlan_combo/sc2355/dfs.c').read_text()
        command = source[source.index('struct sc2355_radar_request {'):source.index('/* Called with the wiphy')]
        block = source[source.index('bool sc2355_dfs_block_ap('):source.index('#else')]
        prefix = r"""
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint32_t __le32;
#define __packed __attribute__((packed))
#define READ_ONCE(v) (v)
#define CMD_RADAR_DETECT 77
#define CMD_WAIT_TIMEOUT 3000
#define IEEE80211_CHAN_RADAR 8
struct channel { unsigned int hw_value, flags; };
struct cfg80211_chan_def { struct channel *chan; unsigned int width; };
struct sprd_vif { void *priv; };
struct sprd_msg { void *data; };
static struct { struct cfg80211_chan_def chandef; } dfs;
static bool dfs_experimental, dfs_fault;
static int api, allocation, command_result, calls;
static unsigned char bytes[64];
static struct sprd_msg message = {bytes};
static unsigned int requested_size;
static bool sc2355_dfs_api_supported(void) { return api; }
static uint32_t cpu_to_le32(uint32_t n) {
    unsigned char b[4] = {n, n >> 8, n >> 16, n >> 24};
    uint32_t out; memcpy(&out, b, 4); return out;
}
static struct sprd_msg *get_cmdbuf(void *p, struct sprd_vif *v, size_t n, int cmd) {
    (void)p; (void)v;
    if (cmd != 77) abort();
    requested_size = n;
    return allocation ? NULL : &message;
}
static int sc2355_send_cmd_recv_rsp(void *p, struct sprd_msg *m, void *b, void *n, int t) {
    (void)p; (void)m; (void)b; (void)n;
    if (t != 3000) abort();
    calls++;
    return command_result;
}
"""
        suffix = r"""
int main(int argc, char **argv) {
    if (argc != 7) return 2;
    struct channel ch = {60, 8};
    struct sprd_vif vif = {NULL};
    dfs.chandef.chan = &ch;
    dfs.chandef.width = atoi(argv[1]);
    api = atoi(argv[3]); allocation = atoi(argv[4]); command_result = atoi(argv[5]);
    int ret = dfs_command(&vif, strtoul(argv[2], NULL, 10));
    printf("%d %d %u ", ret, calls, requested_size);
    for (unsigned int i = 0; i < requested_size && calls; i++) printf("%02x", bytes[i]);
    dfs_experimental = atoi(argv[6]);
    printf(" %d", sc2355_dfs_block_ap(&dfs.chandef));
    ch.flags = 0;
    printf(" %d", sc2355_dfs_block_ap(&dfs.chandef));
    dfs_fault = true;
    printf(" %d\n", sc2355_dfs_block_ap(&dfs.chandef));
    return 0;
}
"""
        cls.binary = work / 'check'
        (work / 'check.c').write_text(prefix + command + block + suffix)
        subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', str(work / 'check.c'), '-o', str(cls.binary)], check=True, capture_output=True)

    def run_contract(self, width=0, duration=60000, api=1, allocation=0, result=0, enabled=1):
        p = subprocess.run([str(self.binary), str(width), str(duration), str(api), str(allocation), str(result), str(enabled)], check=True, capture_output=True, text=True)
        return p.stdout.split()

    def test_widths_and_millisecond_duration_match_the_candidate_wire_contract(self):
        for width in (0, 1, 2, 3):
            self.assertEqual(self.run_contract(width)[:4], ['0', '1', '6', f'3c{width:02x}60ea0000'])

    def test_stop_uses_zero_duration_and_preserves_channel_width(self):
        self.assertEqual(self.run_contract(3, 0)[:4], ['0', '1', '6', '3c0300000000'])

    def test_missing_api_does_not_send_a_command(self):
        self.assertEqual(self.run_contract(api=0)[:3], ['-95', '0', '0'])

    def test_allocation_failure_is_reported_without_sending(self):
        self.assertEqual(self.run_contract(allocation=1)[:3], ['-12', '0', '6'])

    def test_transport_error_is_not_replaced_with_success(self):
        self.assertEqual(self.run_contract(result=-110)[:2], ['-110', '1'])

    def test_failed_stop_blocks_all_ap_start_in_the_enabled_experiment(self):
        self.assertEqual(self.run_contract()[-3:], ['1', '0', '1'])

    def test_disabled_experiment_does_not_change_ap_start_policy(self):
        self.assertEqual(self.run_contract(enabled=0)[-3:], ['0', '0', '0'])


class DfsStartLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('no native C compiler')
        cls.work = tempfile.TemporaryDirectory(prefix='sc2355-dfs-start-')
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        source = (TOP / 'upstream/modules/sprd_wlan_combo/sc2355/dfs.c').read_text()
        start = source[source.index('static int dfs_start('):source.index('static void dfs_end(')]
        prefix = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
typedef uint32_t u32;
#define READ_ONCE(v) (v)
#define NL80211_BAND_5GHZ 1
#define IEEE80211_CHAN_RADAR 8
#define NL80211_IFTYPE_AP 3
#define NL80211_CHAN_WIDTH_80 3
#define pr_info(...) do {} while (0)
struct wiphy { int unused; };
struct channel { unsigned int band, flags, hw_value; };
struct cfg80211_chan_def { struct channel *chan; unsigned int width; };
struct sprd_priv { int unused; };
struct sprd_vif { struct sprd_priv *priv; struct { int iftype; } wdev; };
struct net_device { struct sprd_vif vif; };
static struct {
    struct sprd_priv *priv;
    struct net_device *ndev;
    struct cfg80211_chan_def chandef;
    bool radar, invalid;
    int expiry;
} dfs;
static int dfs_lock, command_error, held, released, queued, stopped;
static unsigned int status;
static bool dfs_experimental = true, dfs_fault, api = true, valid = true;
static struct sprd_vif *netdev_priv(struct net_device *n) { return &n->vif; }
static bool sc2355_dfs_api_supported(void) { return api; }
static bool cfg80211_chandef_valid(struct cfg80211_chan_def *c) { (void)c; return valid; }
static void spin_lock_bh(int *l) { (void)l; }
static void spin_unlock_bh(int *l) { (void)l; }
static void dev_hold(struct net_device *n) { (void)n; held++; }
static int dfs_command(struct sprd_vif *v, u32 duration) { (void)v; (void)duration; return command_error; }
static void dfs_result(unsigned int state, int error) { (void)error; status = state; }
static unsigned int msecs_to_jiffies(unsigned int n) { return n; }
static void wiphy_delayed_work_queue(struct wiphy *w, int *e, unsigned int n) { (void)w; (void)e; (void)n; queued++; }
/* Terminal cleanup itself is exercised above; record the start path's handoff. */
static void dfs_finish(struct wiphy *w, unsigned int state, bool radar, bool notify) {
    (void)w; (void)state; (void)radar; (void)notify;
    dfs.ndev = NULL; stopped++; released++;
}
'''
        suffix = r'''
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    struct wiphy w = {0}; struct sprd_priv p = {0};
    struct net_device n = {{&p, {3}}}, busy = {0};
    struct channel c = {1, 8, 60}; struct cfg80211_chan_def ch = {&c, 3};
    unsigned int duration = 60000; int link = 0;
    dfs.priv = &p;
    switch (atoi(argv[1])) {
    case 1: dfs_experimental = false; break;
    case 2: api = false; break;
    case 3: dfs_fault = true; break;
    case 4: dfs.ndev = &busy; break;
    case 5: duration = 0; break;
    case 6: link = 1; break;
    case 7: c.flags = 0; break;
    case 8: ch.width = 4; break;
    case 9: n.vif.wdev.iftype = 2; break;
    case 10: valid = false; break;
    case 11: command_error = -110; break;
    }
    int ret = dfs_start(&w, &n, &ch, duration, link);
    printf("%d %d %d %d %d %u %d\n", ret, held, released, queued, stopped, status, dfs.ndev == &n);
    return 0;
}
'''
        cls.binary = work / 'check'
        (work / 'check.c').write_text(prefix + start + suffix)
        subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', str(work / 'check.c'), '-o', str(cls.binary)], check=True, capture_output=True)

    def lifecycle(self, mode):
        p = subprocess.run([str(self.binary), str(mode)], check=True, capture_output=True, text=True)
        return tuple(map(int, p.stdout.split()))

    def test_valid_start_holds_the_device_and_queues_one_expiry(self):
        self.assertEqual(self.lifecycle(0), (0,1,0,1,0,1,1))

    def test_disabled_or_unnegotiated_start_does_not_touch_firmware(self):
        for mode in (1,2):
            self.assertEqual(self.lifecycle(mode), (-95,0,0,0,0,0,0))

    def test_fault_latch_prevents_another_listener(self):
        self.assertEqual(self.lifecycle(3), (-5,0,0,0,0,0,0))

    def test_busy_start_cannot_replace_the_existing_listener(self):
        self.assertEqual(self.lifecycle(4), (-16,0,0,0,0,0,0))

    def test_invalid_requests_do_not_hold_the_device_or_start_a_timer(self):
        for mode in range(5,11):
            self.assertEqual(self.lifecycle(mode), (-22,0,0,0,0,0,0))

    def test_lost_start_acknowledgement_attempts_cleanup_without_queuing_cac(self):
        self.assertEqual(self.lifecycle(11), (-110,1,1,0,1,4,0))
