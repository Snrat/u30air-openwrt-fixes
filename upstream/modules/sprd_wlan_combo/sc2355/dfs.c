/* SPDX-License-Identifier: GPL-2.0 */
/* Disabled by default. Initial prototype is receive/CAC-only: it never reports
 * CAC_FINISHED and explicitly refuses AP transmission on a radar channel.
 * The command/event layout comes from vendor Merlion, not a verified SC2355 RF contract.
 */
#include <linux/moduleparam.h>
#include "common/common.h"
#include "common/iface.h"
#include "cmdevt.h"
#include "dfs.h"
#include "dfs_event.h"

#if LINUX_VERSION_CODE >= KERNEL_VERSION(7, 2, 0)
static bool dfs_experimental;
module_param(dfs_experimental, bool, 0444);
MODULE_PARM_DESC(dfs_experimental, "SC2355 listen-only DFS protocol prototype; not operational DFS support");

static DEFINE_SPINLOCK(dfs_lock);
static struct {
	struct sprd_priv *priv;
	struct net_device *ndev;
	struct cfg80211_chan_def chandef;
	struct wiphy_work event_work;
	struct wiphy_delayed_work expiry;
	bool radar;
	bool invalid;
} dfs;
static unsigned int dfs_status;
/* 0=idle, 1=listening, 2=listen-only aborted, 3=radar, 4=rejected, 5=invalid event */
static void dfs_result(unsigned int state, int error)
{
	WRITE_ONCE(dfs_status, state | ((unsigned int)(-error) << 8));
}
static int dfs_status_get(char *buf, const struct kernel_param *kp)
{
	unsigned int status = READ_ONCE(dfs_status);
	(void)kp;
	return scnprintf(buf, PAGE_SIZE, "state=%u result=%d listen_only=1\n",
			 status & 255, -(int)(status >> 8));
}
static const struct kernel_param_ops dfs_status_ops = { .get = dfs_status_get };
module_param_cb(dfs_probe_status, &dfs_status_ops, NULL, 0444);

struct sc2355_radar_request {
	u8 channel;
	u8 width;
	__le32 cac_ms;
} __packed;

static int dfs_command(struct sprd_vif *vif, u32 duration)
{
	struct sprd_msg *message;
	struct sc2355_radar_request *request;
	if (!sc2355_dfs_api_supported())
		return -EOPNOTSUPP;
	message = get_cmdbuf(vif->priv, vif, sizeof(*request), CMD_RADAR_DETECT);
	if (!message)
		return -ENOMEM;
	request = message->data;
	request->channel = dfs.chandef.chan->hw_value;
	request->width = dfs.chandef.width;
	request->cac_ms = cpu_to_le32(duration);
	return sc2355_send_cmd_recv_rsp(vif->priv, message, NULL, NULL, CMD_WAIT_TIMEOUT);
}

/* Called with the wiphy mutex held. Queueing in RX is serialized with clearing ndev. */
static void dfs_finish(struct wiphy *wiphy, unsigned int state, bool radar, bool notify)
{
	struct net_device *ndev;
	struct sprd_vif *vif;
	int ret;
	spin_lock_bh(&dfs_lock);
	ndev = dfs.ndev;
	dfs.ndev = NULL;
	spin_unlock_bh(&dfs_lock);
	if (!ndev)
		return;
	wiphy_delayed_work_cancel(wiphy, &dfs.expiry);
	wiphy_work_cancel(wiphy, &dfs.event_work);
	vif = netdev_priv(ndev);
	ret = dfs_command(vif, 0);
	if (radar)
		cfg80211_radar_event(wiphy, &dfs.chandef, GFP_KERNEL);
	if (notify && vif->wdev.links[0].cac_started)
		cfg80211_cac_event(ndev, &dfs.chandef, NL80211_RADAR_CAC_ABORTED, GFP_KERNEL, 0);
	dfs_result(state, ret);
	pr_info("SC2355 DFS prototype: listen ended state=%u stop_result=%d; no AP TX\n", state, ret);
	dev_put(ndev);
}
static void dfs_expired(struct wiphy *wiphy, struct wiphy_work *work)
{
	(void)work;
	/* No inference of real radar detection from an ACK or a quiet timer. */
	dfs_finish(wiphy, 2, false, true);
}
static void dfs_received(struct wiphy *wiphy, struct wiphy_work *work)
{
	bool radar;
	bool invalid;
	(void)work;
	spin_lock_bh(&dfs_lock);
	radar = dfs.radar;
	invalid = dfs.invalid;
	spin_unlock_bh(&dfs_lock);
	if (radar || invalid)
		dfs_finish(wiphy, invalid ? 5 : 3, radar, true);
}
static int dfs_start(struct wiphy *wiphy, struct net_device *ndev,
		     struct cfg80211_chan_def *chandef, u32 duration, int link_id)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	int ret;
	if (!dfs_experimental || dfs.priv != vif->priv || !sc2355_dfs_api_supported())
		return -EOPNOTSUPP;
	if (link_id != 0 || !duration || !cfg80211_chandef_valid(chandef) ||
	    chandef->chan->band != NL80211_BAND_5GHZ ||
	    !(chandef->chan->flags & IEEE80211_CHAN_RADAR) ||
	    vif->wdev.iftype != NL80211_IFTYPE_AP ||
	    chandef->width > NL80211_CHAN_WIDTH_80)
		return -EINVAL;
	spin_lock_bh(&dfs_lock);
	if (dfs.ndev) {
		spin_unlock_bh(&dfs_lock);
		return -EBUSY;
	}
	dev_hold(ndev);
	dfs.ndev = ndev;
	dfs.chandef = *chandef;
	dfs.radar = false;
	dfs.invalid = false;
	spin_unlock_bh(&dfs_lock);
	ret = dfs_command(vif, duration);
	if (ret) {
		/* A lost acknowledgement may have started listening; try one stop, never complete CAC. */
		dfs_finish(wiphy, 4, false, false);
		dfs_result(4, ret);
		return ret;
	}
	dfs_result(1, 0);
	wiphy_delayed_work_queue(wiphy, &dfs.expiry, msecs_to_jiffies(duration));
	pr_info("SC2355 DFS prototype: listening channel=%u width=%u duration=%u ms; no AP TX\n",
		chandef->chan->hw_value, chandef->width, duration);
	return 0;
}
static void dfs_end(struct wiphy *wiphy, struct net_device *ndev, unsigned int link_id)
{
	(void)link_id;
	if (READ_ONCE(dfs.ndev) == ndev)
		dfs_finish(wiphy, 2, false, false);
}
static const struct ieee80211_iface_limit dfs_limits[] = {
	{ .max = 1, .types = BIT(NL80211_IFTYPE_AP) | BIT(NL80211_IFTYPE_STATION) },
};
static const struct ieee80211_iface_combination dfs_combination[] = {
	{ .limits = dfs_limits, .n_limits = ARRAY_SIZE(dfs_limits),
	  .max_interfaces = 1, .num_different_channels = 1,
	  .radar_detect_widths = BIT(NL80211_CHAN_WIDTH_20_NOHT) | BIT(NL80211_CHAN_WIDTH_20) |
				 BIT(NL80211_CHAN_WIDTH_40) | BIT(NL80211_CHAN_WIDTH_80),
	  .radar_detect_regions = BIT(NL80211_DFS_FCC) | BIT(NL80211_DFS_ETSI) | BIT(NL80211_DFS_JP) },
};
void sc2355_dfs_setup(struct wiphy *wiphy, struct sprd_priv *priv)
{
	if (!dfs_experimental || (dfs.priv && dfs.priv != priv))
		return;
	if (!dfs.priv) {
		dfs.priv = priv;
		wiphy_work_init(&dfs.event_work, dfs_received);
		wiphy_delayed_work_init(&dfs.expiry, dfs_expired);
	}
	wiphy->iface_combinations = dfs_combination;
	wiphy->n_iface_combinations = ARRAY_SIZE(dfs_combination);
	pr_warn("SC2355 DFS prototype enabled for listen-only protocol tests; not RF-validated\n");
}
void sc2355_dfs_ops(struct cfg80211_ops *ops)
{
	if (dfs_experimental) {
		ops->start_radar_detection = dfs_start;
		ops->end_cac = dfs_end;
	}
}
void sc2355_dfs_abort(struct sprd_vif *vif)
{
	if (READ_ONCE(dfs.ndev) == vif->ndev)
		dfs_finish(vif->priv->wiphy, 2, false, true);
}
void sc2355_dfs_remove(struct sprd_priv *priv)
{
	if (dfs.priv != priv)
		return;
	wiphy_lock(priv->wiphy);
	dfs_finish(priv->wiphy, 2, false, true);
	dfs.priv = NULL;
	wiphy_unlock(priv->wiphy);
}
void sc2355_dfs_event(struct sprd_vif *vif, const u8 *data, unsigned int len)
{
	struct sc2355_dfs_event_info event;
	int ret = sc2355_dfs_parse_event(data, len, &event);
	spin_lock_bh(&dfs_lock);
	if (dfs.ndev && dfs.priv == vif->priv && dfs.ndev == vif->ndev) {
		if (ret)
			dfs.invalid = true;
		else if (event.type)
			dfs.radar = true;
		if (dfs.invalid || dfs.radar)
			wiphy_work_queue(vif->priv->wiphy, &dfs.event_work);
	}
	spin_unlock_bh(&dfs_lock);
}
bool sc2355_dfs_block_ap(const struct cfg80211_chan_def *chandef)
{
	return dfs_experimental && chandef->chan && (chandef->chan->flags & IEEE80211_CHAN_RADAR);
}
#else
void sc2355_dfs_setup(struct wiphy *wiphy, struct sprd_priv *priv) { }
void sc2355_dfs_ops(struct cfg80211_ops *ops) { }
void sc2355_dfs_abort(struct sprd_vif *vif) { }
void sc2355_dfs_remove(struct sprd_priv *priv) { }
void sc2355_dfs_event(struct sprd_vif *vif, const u8 *data, unsigned int len) { }
bool sc2355_dfs_block_ap(const struct cfg80211_chan_def *chandef) { return false; }
#endif
