# Laya integration

Pulse uses Laya as a decision layer for category, clarification need, spam suspicion and urgency.
Laya does not select a service, queue or operator. The existing Pulse routing engine still uses the
confirmed category, region, language, district, operator skills, availability and workload.

The HTTP contract was audited against the requested
[he-jev/laya bundle](https://github.com/he-jev/laya/tree/c5d78730f3493e4fe16d61507ef4b78eef7318cf)
and its current [upstream implementation](https://github.com/NandhaKishorM/laya/tree/4066d5d5fbf08b66c6757ddeedbd797bd7655bc0).

```text
Intake → Laya → DecisionGate → accept / verify / clarify → Pulse routing → operator
```

## Run

Laya 0.3.20 exposes the current Jev-compatible API at `POST /v1/systemone`. One request contains a
sanitized state plus the four typed questions. The response uses `choice` for category and urgency,
and `noul` for clarification and spam. A medium category result triggers one binary `noul`
verification request. Pulse reads the reported category probability; Laya's entropy-based
`confidence` field is not treated as accuracy.

The same endpoint also supports `score`, returning an expected score with probabilities and a
legend. Pulse does not use it because the existing urgency contract is the categorical
`normal`/`urgent` choice. Laya accepts multiple questions in one request, requires bearer auth only
when `LAYA_API_KEY` is configured, and reports overload/model startup failures as HTTP errors; Pulse
maps those failures and malformed typed answers to the existing fallback.

Start a local Laya server in a separate environment:

```sh
pip install "laya[serve]==0.3.20"
LAYA_PRELOAD=1 LAYA_MODELS=multilingual laya-serve
```

Then start Pulse:

```sh
P109_DECISION_PROVIDER=laya \
P109_LAYA_BASE_URL=http://127.0.0.1:8000 \
python -m uvicorn app:app --host 127.0.0.1 --port 8769
```

Use `P109_DECISION_PROVIDER=hybrid` to compare Laya with the existing classifier. Disagreement is
shown to the operator and never confirms a category automatically. A remote Laya endpoint uses the
same application code; set `P109_LAYA_BASE_URL` and, when required, `P109_LAYA_API_KEY`.

## Fallback and thresholds

`P109_HIGH_CONFIDENCE` and `P109_MEDIUM_CONFIDENCE` default to provisional demo values `0.85` and
`0.55`. They must be calibrated on approved RU/KK labels before production use. Laya timeout,
transport, HTTP and schema failures fall back to the existing classifier. Setting
`P109_LAYA_DEMO_FALLBACK=1` labels that path as `DEMO FALLBACK`; the UI never presents it as Laya.
`GET /api/health` reports Laya separately, so an unavailable decision service does not make Pulse
unhealthy.

Every classification and operator override is stored in the existing append-only audit table.
Audit payloads contain labels, probabilities, provider/version, decision mode and latency, without
the complaint text or sender identifiers. The Laya request removes common phone, email and IIN
patterns and sends no case ID, sender key or operator data.

## Verification

```sh
python scripts/check_laya.py
python scripts/evaluate_laya.py --base-url http://127.0.0.1:8000
python scripts/benchmark_laya.py --base-url http://127.0.0.1:8000 --requests 50
```

The evaluation uses only the checked synthetic RU/KK fixture labels. Its output is a harness result,
not production accuracy. Real data must be split by incident, duplicate cluster and fingerprint so
related reports cannot cross train/test boundaries.

## Measured local evidence

On 25 September 2026 the real Laya 0.3.20 multilingual checkpoint was run locally on an Apple M1
with 16 GB RAM, CPU inference and four threads. The required demo request completed through Pulse in
463 ms total (462 ms in Laya), then the existing router selected Алматы Су and Айдана К.

The checked 20-case synthetic RU/KK fixture set produced 55% accuracy and 0.5033 macro F1 overall:
60% accuracy for RU and 50% for KK. Urgent recall was 0.6667. The set has no labeled spam cases or
operator outcomes, so spam precision and override rate are unavailable. A separate 50-request run
completed with 0 errors, 459.93 ms median latency, 554.76 ms p95 and 32% verification requests.

These are small synthetic demo measurements, not production quality evidence. The aggregate accuracy
is too low for unattended classification even when an individual response reports high confidence.
The default provider therefore remains `mock`; Laya needs approved, group-aware RU/KK evaluation and
threshold calibration before production activation.
