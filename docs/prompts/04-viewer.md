# 04 — Update or extend the 3D viewer

Use after a task produces new outputs you want to see in 3D, or to try a viewer idea.

```
Update the 3D web viewer in viewer/ following "3D visualisation rules" in CLAUDE.md.

Change: <e.g. add the M2.4 canopy-class layer | add a 2-panel before/after view | refresh data>.

1. Plan it briefly (layers, styling, legend, data size) and wait for my OK.
2. Regenerate viewer/data/ only through highland.viewer.build().
3. Keep it static, keys-free, under 100 MB, CDN versions pinned.
4. Run the viewer smoke test locally and give me a screenshot of the
   default view plus one with the new layer on, with exaggeration stated.
5. Open a PR; the Pages workflow publishes it after I merge.
```
