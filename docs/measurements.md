# Measurements

Every number here comes from a script in `scripts/` run on real data. Raw JSON lands in `data/results/` (gitignored); paste the printed tables below with the date and setup.

Status legend: **TODO** not run yet · **DONE** measured.

## M0 checklist

| Item | Script | Status |
|---|---|---|
| Phone camera over HTTPS + WS echo | `backend/run.py` | server side tested (localhost + LAN IP); **phone not yet tested** |
| SAM keyframe latency / matches | `scripts/sam_latency.py` | TODO (needs `MODEL_API_KEY` + phone frames) |
| Spark box grounding vs SAM | `scripts/spark_grounding.py` | TODO |
| Spark structured plan | `scripts/spark_plan.py` | TODO |
| Voice realtime from mic | `scripts/voice_ws.py` | TODO |

## [VERIFY] items to close in M0

- [ ] SAM stream event shape works with `meta-sam-parser` + `openai` SDK `AsyncStream` (script uses it directly)
- [ ] `metadata` (`mask_encoding`, `include_confidence`) accepted on `/v1/responses` for `sam-3.1`
- [ ] Spark native box coordinate convention (script scores pixels / 0-1000 / 0-1)
- [ ] Spark strict `json_schema` accepts the recursive tagged-union plan schema (else fall back to JSON mode)
- [ ] Voice handshake JSON shape (flat as in PRD 7.3) and event field names (`text` vs `transcript`)
- [ ] Which Spark `reasoning.effort` values the endpoint accepts

## Results

### Phone camera / HTTPS
_not yet measured on a phone_

### SAM latency
_TODO_

### Spark grounding
_TODO_

### Spark plan
_TODO_

### Voice
_TODO_

## Cost log

`data/cost_log.jsonl` is appended by each script (estimates from published prices). Meta credit: $150.

| Date | Script | Est. USD | Notes |
|---|---|---|---|
