# Decisions

## 2026-10-27

- **Predicate encoding.** PRD section 8's example uses key-per-op objects (`{"all":[{"exists":...}]}`). The code uses an `op`-tagged tree (`{"op":"all","items":[...]}`) in `backend/app/programs/schema.py`. A discriminator is easier to validate with pydantic and to express in a strict JSON schema. Revisit if `spark_plan.py` shows the endpoint rejecting it.
- **HTTPS for the phone.** Self-signed cert covering localhost and the laptop's LAN IPs (`scripts/make_cert.py`), accepted once on the phone. mkcert and ngrok are not installed/enabled; ngrok would expose the backend publicly, so it is opt-in only.
- **Frame protocol.** Binary WS frame: `flags u8 | seq u32 | client_ts f64 | JPEG`. Flag bit 0 saves the frame to `data/frames/` so the M0 scripts run on real phone captures.
- **SAM box-prompt test source.** `sam_latency.py` seeds box prompts from the concept pass's own boxes, so it measures box-prompt latency without depending on Spark grounding.
- **No commits yet.** The folder was not a git repo; nothing has been initialised or committed.
