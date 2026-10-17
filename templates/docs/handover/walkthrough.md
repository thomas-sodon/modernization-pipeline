# Walkthrough script: $project_name

<!--
Skeleton for {{p.target}}/docs/walkthrough.md, demo engagements only (S6.handover): the
narration for the video the S6 demo gate records from the @pytest.mark.walkthrough test in
app/tests/e2e/ (E2E_VIDEO_DIR={{p.private}}/synthetic/media/walkthrough, file
walkthrough.webm, seed-1 data only, never committed). About 3 minutes. One ### scene per
step of the test, in the test's order; each says what is on screen and what to say.
Replace every fill hint; a filled copy keeps none. Delete this comment.
-->

<!-- fill: one paragraph: which workflow the walkthrough follows (workflow id and title from analysis/rules.json), which test records it (file and function), how long it runs, and that every figure on screen comes from synthetic seed 1. -->

## Before you record

<!-- fill: the recording command from the S6 demo gate (run from the merged main in a plain terminal), where the file lands, the 1280x800 viewport, the pause between steps, and that the narrator records the voice separately and keeps to the script; no real data, no private path on screen. -->

## Scenes

<!-- fill: one ### heading per scene: "### <n>. <step> (about <s> s)", then two lines: **On screen:** what the viewer sees (page, buttons, badges; names of fields, never seed values read aloud as facts) and **Say:** the narration (what the user does, which rule it shows, by id, and what the original Access app did the same way). Start with an opening scene (what the original app was) and end with a closing scene. -->

## Closing

<!-- fill: the last scene's narration: where the evidence is (<target.root>/docs/equivalence-report.md: the lowest cell rate over the cells, rule and end-to-end tests) and how a viewer re-runs the synthetic checks (<target.root>/README.md, equivalence/README.md). Say what recorded the reference outputs as the report's Method names it (for Access sources the UCanAccess engine, not Access itself, unless a confirmation run on Access is recorded) and how many kept units were not confirmed on the original; never say the queries were recorded on the original app. -->
