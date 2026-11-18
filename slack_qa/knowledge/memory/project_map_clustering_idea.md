---
name: project_map_clustering_idea
description: "Discovery map clustering - DONE + LIVE 10-27 (v4, all signed-in users); editor tab can lose bubbles until reloaded"
metadata:
  node_type: memory
  type: project
  originSessionId: 197623b5-67b9-4b3f-ae23-e4a6497efc21
  modified: 2026-10-28T02:24:02.927Z
---

Discovery map count bubbles are LIVE (owner confirmed 2026-10-27). Page onload workflow f63d66af "Cluster pins (all signed-in users)", version '2026-10-27-all-v4'. It fetches Railway search-api GET /map/all (needs the Xano login token, 401 without it) and draws a clustered symbol layer; single pins go to the normal Mapbox markers. Kill switch: variable map_cluster_preview. Console check: `__ttClusterDebug()` lives on wwLib.getFrontWindow(), which in the editor is NOT the inner.html frame (undefined there even while bubbles draw) - try the `top` frame. Rollback: .claude/weweb_cluster_script_v3_pre_ungate_2026-10-27.json.

**Editor gotcha (2026-10-28):** the owner saw no bubbles AND no pins in the WeWeb editor while live worked. An F5 reload of the editor fixed it; cause NOT established (CORRECTED: `__ttClusterDebug` undefined in inner.html proves nothing - it is also undefined there while bubbles work). Candidates: the 10 s token wait or the 3-failure give-up, both last for the tab session. Static checks found no staging/live difference (same page config, script, mapbox-gl chunk; CORS allows the live site, *-staging.weweb.io and *-editor/preview hosts, but NOT staging.tulletogether.app). At world zoom the old MapLight path loads no pins (its fetch only runs when zoom > 7), so a stopped cluster script = empty map. **First step next time: reload the editor tab.**

Console also shows WW-MAPBOX "FAILED TO REFRESH SOURCES AND LAYERS ... is not a constructor" from WeWeb's own element refresh; not shown to break anything.

**2026-10-30 missing bubbles at fractional DPR (Kate report):** at browser zoom 90%/110%, Windows 125% scaling or Retina+zoom, bubbleImage declared a fractional size, Mapbox threw "mismatched image size" (swallowed) -> no bubbles, only unclustered singles, dense areas (Manhattan/Chicago) looked empty. Reproduced in headless Chromium (DPR 0.9/1.25/1.8 fail; 1/1.5/2 ok). v5 '2026-10-30-all-v5' rounds to whole pixels; reproduced fixed. Edited in the editor; NEEDS OWNER PUBLISH. Backup .claude/weweb_backups/cluster_script_v4_pre_dpr_fix_2026-10-30.js. Image errors now land in __ttClusterDebug().error.
