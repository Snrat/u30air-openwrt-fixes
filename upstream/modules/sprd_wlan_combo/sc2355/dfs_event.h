/* SPDX-License-Identifier: GPL-2.0 */
#ifndef SC2355_DFS_EVENT_H
#define SC2355_DFS_EVENT_H
/* Merlion v1 format is a candidate contract, not measured SC2355 RF behavior. */
struct sc2355_dfs_event_info { unsigned char region, type; };
static inline int sc2355_dfs_parse_event(const unsigned char *data,
				       unsigned int len,
				       struct sc2355_dfs_event_info *event)
{
	if (!data || !event || len != 2 || data[0] < 1 || data[0] > 3 || data[1] > 2)
		return -1;
	event->region = data[0];
	event->type = data[1];
	return 0;
}
#endif
