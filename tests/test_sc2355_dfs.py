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
    if (atoi(argv[1]) == 0) { dfs_expired(&w, &work); dfs_expired(&w, &work); }
    else { dfs_received(&w, &work); dfs_received(&w, &work); }
    printf("%d %d %d %d %d %u\n", stops, aborted, finished, radars, released, dfs_status & 255);
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
        self.assertEqual(self.callback(0), (1,1,0,0,1,2))

    def test_stop_failure_cannot_mark_a_channel_available(self):
        self.assertEqual(self.callback(0,-5), (1,1,0,0,1,2))

    def test_radar_reports_unavailable_and_aborts_cac_once(self):
        self.assertEqual(self.callback(1), (1,1,0,1,1,3))

    def test_malformed_event_aborts_without_fabricating_radar(self):
        self.assertEqual(self.callback(2), (1,1,0,0,1,5))

    def test_parser_rejects_truncation_extra_bytes_and_unknown_values(self):
        cases = [(n,1,1) for n in (0,1,3,255)] + [(2,r,t) for r,t in ((0,1),(4,1),(1,3),(255,255))]
        p=subprocess.run([str(self.binary),'3','0'],input=''.join(f'{n} {r} {t}\n' for n,r,t in cases),capture_output=True,text=True,check=True)
        for line in p.stdout.splitlines(): self.assertLess(int(line.split()[0]),0)

    def test_parser_accepts_only_the_candidate_v1_domain_and_type_values(self):
        cases=[(r,t) for r in (1,2,3) for t in (0,1,2)]
        p=subprocess.run([str(self.binary),'3','0'],input=''.join(f'2 {r} {t}\n' for r,t in cases),capture_output=True,text=True,check=True)
        self.assertEqual([tuple(map(int,l.split())) for l in p.stdout.splitlines()],[(0,r,t) for r,t in cases])
