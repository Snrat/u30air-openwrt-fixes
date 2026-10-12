/* SPDX-License-Identifier: GPL-2.0 */
#ifndef SC2355_DFS_PROTOTYPE_H
#define SC2355_DFS_PROTOTYPE_H
#include <net/cfg80211.h>
struct sprd_priv;
struct sprd_vif;
void sc2355_dfs_setup(struct wiphy *wiphy, struct sprd_priv *priv);
void sc2355_dfs_ops(struct cfg80211_ops *ops);
void sc2355_dfs_abort(struct sprd_vif *vif);
void sc2355_dfs_remove(struct sprd_priv *priv);
void sc2355_dfs_event(struct sprd_vif *vif, const u8 *data, unsigned int len);
bool sc2355_dfs_block_ap(const struct cfg80211_chan_def *chandef);
#endif
