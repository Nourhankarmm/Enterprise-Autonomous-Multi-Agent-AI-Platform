# Synthetic UI walkthrough

`phase6-demo.mp4` is an edited, captioned walkthrough assembled from genuine Phase 5 browser captures of the UI carried into Phase 6. It is not a continuous screen recording, a new Phase 6 browser test or a latency measurement. Fresh browser recording was unavailable because the computer-use runtime failed to initialize (`failed to write kernel assets`). The two source PNGs are included for reproducibility.

The overview shows owner-permitted synthetic records. The usage screen shows a deterministic demo-planner call with zero model tokens/cost; infrastructure and live-provider costs are outside that number. No real supplier correspondence is sent.

For an interactive demonstration, start the local Compose stack as described in ../DEPLOYMENT.md, log in with a local synthetic manager, create and review a procurement plan, open its cited evidence, then explicitly create a sandbox follow-up. A separately assigned approver reviews the exact recipient/body/evidence. A changed proposal requires fresh review. The completed workflow displays a sandbox receipt. The real worker-recovery test is separate from this visual walkthrough.
