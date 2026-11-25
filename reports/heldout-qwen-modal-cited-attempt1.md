# Held-out, qwen27-ar4-modal-cited: first attempt stopped unscored

25 Nov 2026, 05:13 to 05:27 UTC. Method `reports/heldout-qwen-modal-cited-method.md` (commit 96e6f11).

The runner sent 134 judge requests to the dpl015 Modal endpoint; none was answered. Each returned "warming: health probe did not answer" while the server cold-started (server log: ready at 05:22:33, no judge request served, scaled down at 05:25:20). The orchestrator stopped the runner at 05:27:42. No reply was cached, no row was scored, no verdict or summary was written or seen.

Cause, a runner defect that predates this method: `ModalGateChat._on_unavailable` treated a warming server as a failed judge attempt, so after three attempts the verifier's judge would have labelled a row `error` (scored not stated, a rollback). A completed run could have counted cold-start errors as caught. Fixed before the rerun: a warming server is waited for (`WARM_POLL_S` 10 s between resends, bounded by the existing 900 s per call), never a failed attempt; tests added.

The rerun uses the same method commit (96e6f11), unchanged method, battery, premises, prompt, settings and counting; only the runner's handling of a cold server differs. Cost of this attempt: one cold start and about 3 minutes idle on dpl015.
