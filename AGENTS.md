# Pulse 109 — implementation handoff

## Current state and objective

- Latest bounded delivery and exact next task: read `docs/handoff.md` first. It supersedes dated baseline status below, not the architecture or privacy contracts.

- The team has switched from the hospital case to Pulse 109.
- As of 2026-09-10, the synthetic skeleton (issue P109-09) is implemented (`app.py`, `fixtures/demo.json`, `static/*`, `scripts/smoke.py`). Models are not yet trained.
- Build a compact skeleton first; do not repeat the completed research.
- Read the current task and relevant planning files before editing.
- `planning/data_inventory.json` records the audited source headers and limits.
- Read `planning/backlog.json` for one assigned task and `docs/research.md` only
  when you need its decision or source; do not load the whole report every turn.
- Ilyas owns integration/ML; Olga can own UI/review; Nurali can own QA/annotation.
- Work inline unless the task explicitly delegates independent agent work.

## Non-negotiable case requirements

- Final product: intake/routing, operator assistance, and management analytics.
- Classify at least 10 meaningful topics in Russian and Kazakh.
- Both the classifier and semantic embedding encoder require domain fine-tuning.
- Prompting, frozen embeddings, or a trained head alone do not satisfy our plan.
- Preserve two independently trained checkpoints and their evaluation evidence.
- Final scope includes all 20 regions, alerts, 1–3 month forecasts, data questions,
  and PDF/Excel export; the first slice does not claim to complete that scope.
- Human confirmation governs routing, priority, duplicate decisions, and replies.
- A working mock is skeleton evidence, never evidence of trained-model quality.

## Data gate: facts already established

- Primary organizer folder: https://drive.google.com/drive/folders/1rOKRZeEvpJlBkNeWKoIVzq8Lq3zeVi1B
- Its root and Pavlodar subfolder list 8 CSVs representing only 7 regions.
- All 8 organizer CSV headers were read directly. A 2026-09-10 local structural pass counted 1,036,858 CSV records, with zero blank/width/parser errors; sizes/headers match inventory, full Drive identity and unique complaints remain unverified. Numeric evidence is in inventory `local_profile`; repeat with `scripts/audit_received_csv.py`.
- One public catalog search found 12 matching cards representing the same 7 regions.
- Headers match the public schemas; complete file identity is NOT established.
- Most inspected schemas lack an explicit original complaint-text column.
- Akmola `request_subject` may help, but its meaning/content remains unverified.
- Do not treat `com_exp`, `result`, category, service, or executor as original
  intake text without a verified source definition; outcomes are leakage risks.
- Never reconstruct citizen text from category labels and call it real data.
- Do not use outcomes or assigned services as intake-classifier input features.
- RU/KK coverage, history completeness, unique complaint counts, and the official 20-region export are unverified. Local parsed-record counts do not resolve those gates.
- Some schemas expose names, applicant numbers, streets, or coordinates.
- Public metadata/license labels do not establish permission to publish these rows.
- Keep real citizen records out of GitHub, CI, prompts, logs, screenshots, and demos.
- Missing original text or decisions must remain null/unsupported, not invented.
- The team reports this is the complete package they received; do not repeatedly
  request the same folder or claim the missing 13 regions exist.
- Field meanings, rights, hosting and text availability still need confirmation;
  if no response is possible, document the constrained MVP and its unmet gates.
- `planning/data_contract_request.md` is the exact request and P109-03 closure
  checklist; do not mark the gate complete from a promise of future data.

## Exact initial architecture

- Python 3.11+, FastAPI, Uvicorn, SQLite, and NumPy.
- One FastAPI process serves `/api/*` and a vanilla HTML/CSS/JS static interface.
- Use Pydantic request validation and Python's `sqlite3`; parameterize SQL.
- No React, frontend build chain, ORM, microservices, message broker, or agent runtime.
- Add dependencies only when an assigned feature actually requires them.
- Initial files: `app.py`, `static/index.html`, `static/style.css`, `static/app.js`,
  `fixtures/demo.json`, `scripts/smoke.py`, and a small dependency file.
- Keep each file around 500 lines or less; split only when its real size warrants it.
- SQLite holds complaints, operator decisions, and a small append-only audit table.
- Audit events are a record of actions, not a separate event-sourcing architecture.
- Model training, document embedding, and forecast refresh run as offline batch jobs.
- Requests load prepared artifacts; never train a model inside an HTTP handler.
- Later retrieval starts with normalized vectors and exact NumPy dot-product search.
- Do not create random vectors and present their ranking as semantic retrieval.
- Benchmark latency/memory on the actual corpus before adding an approximate index.
- Review exact search at 100k vectors or p95 above 500 ms on target hardware.
- Those are provisional engineering triggers, not measured performance promises.
- If exact search fails the measured budget, consider FAISS before a vector service.
- Use a `ponytail:` comment where a deliberate implementation ceiling is introduced.

## Shared complaint contract

- Internal `id`: server-generated opaque string, never a citizen identifier.
- `data_origin`: `synthetic`, `organizer`, or `public`; assigned by the ingestion path.
- `source_system`, `source_record_id`: nullable provenance, never universal join keys.
- `text`: original/synthetic complaint text; nullable for imported metadata-only rows.
- Interactive intake requires nonblank `text` of at most 10,000 characters.
- `region_id`: stable ID from the agreed 20-region mapping; nullable for unknown imports.
- Interactive intake requires a known region; never confuse Almaty city and region.
- `received_at`: nullable source event time; `ingested_at`: known server timestamp.
- Use ISO 8601 with timezone; do not guess unknown source timezones.
- Preserve date-only precision separately; do not fabricate hourly event times.
- `language`: `ru`, `kk`, `mixed`, or `unknown`; preserve uncertainty.
- `source_category`, `source_service`, `source_status`: nullable unmodified source values.
- `topic`, `service_id`, `priority`: nullable proposed/confirmed canonical values.
- `priority`: `normal` or `urgent` (human-confirmed decision); a detected urgency signal is a proposal until confirmed; unknown urgency is `null`, never `needs_review`; no medical/emergency inference claims.
- `decision_status`: `pending` or `confirmed`; confirmation is explicit.
- `incident_id`, `duplicate_of`: nullable, populated only with a checked relationship.
- `resolution_text`, `resolved_at`: nullable; a closed status is not proof of resolution.
- Precise location/contact data are outside the synthetic skeleton contract.
- An unknown count/value is null or unavailable, never a fabricated zero.

## Audit and prediction contracts

- Event fields: `id`, `complaint_id`, `event_type`, `occurred_at`, `recorded_at`,
  `actor`, and structured `payload` containing only the required safe changes.
- Initial event types: `intake`, `classification_proposed`, `operator_confirmed`.
- Later incident-link and reply decisions are also explicit audited actions.
- Write the complaint change and its audit event in one SQLite transaction.
- Every model-like response declares `mode: mock|trained`, `checkpoint_id`, and
  `training_status: not_trained|trained`; mock checkpoint IDs are null.
- Mock confidence/probability is null; do not invent accuracy or calibrated scores.
- Keep proposals separate from the latest confirmed values in storage/results.
- Retrieval items expose complaint ID, excerpt, origin, and any available decision.
- Similarity is not duplicate probability; an analogous solution is not the same incident.
- Never return future resolutions when replaying an earlier intake time.
- Route mapping depends on region/topic and a versioned service table.
- Unknown/ambiguous mapping yields `needs_review`, not a made-up public authority.

## API contract

- `GET /api/health`: app status and active mock/trained modes; no secret/config dump.
- `POST /api/intake`: validate text/region, persist a pending complaint, return its ID.
- `POST /api/complaints/{id}/classify`: persist/return a proposal; never confirm it.
- `POST /api/complaints/{id}/confirm`: validate topic/service/priority and record the
  operator's decision; use demo identity only while explicitly in synthetic mode.
- `GET /api/complaints/{id}/similar?limit=5`: candidate cases, limited to 1–20 results.
- `GET /api/stats`: counts from stored rows; optional validated region/date filters.
- `GET /api/alerts`: alert list with observed period, supporting counts, and method.
- `GET /api/forecast?horizon_months=1`: only horizons 1–3; origin, interval, and method.
- `POST /api/query`: question plus optional filters; numbers/chart data and provenance.
- `GET /api/reports?format=pdf|xlsx`: a real validated export or explicit unimplemented error.
- Unknown IDs return 404; invalid inputs return 422; unexpected failures do not leak rows.
- Future endpoints may return 501 with `not_implemented`; do not return fake success.
- Natural-language queries must select supported aggregate operations, never execute
  arbitrary SQL or code supplied by a user/model. Unsupported questions are explicit.

## First task: one synthetic vertical slice

1. Add a small deterministic fixture set covering RU, KK, and 10 proposed topics.
2. Mark every fixture and every screen `SYNTHETIC DEMO — MODELS NOT TRAINED`.
3. Seed SQLite once using stable fixture IDs; restarting must not duplicate rows.
4. Serve one accessible screen: fictional intake, proposal, similar cases, confirmation,
   and a small region/topic summary. Include labels, keyboard access, and error states.
5. Mock classification/similarity use explicit fixture expectations; unknown input
   returns an unclassified proposal requiring review, not invented model output.
6. Let the operator select a topic and confirm a demo service/priority.
7. Persist confirmation and show updated counts from SQLite, not hardcoded UI totals.
8. Leave alerts/forecast/query/reports as honest 501 stubs until their own tasks.
9. Add `python scripts/smoke.py`: one runnable check using a temporary SQLite file,
   a locally started server, and standard-library HTTP requests.
10. Check blank intake rejection, successful intake, mock metadata, candidate origins,
    persisted confirmation, changed counts, and explicit 501 responses.
11. Isolate/clean up only resources the smoke check created; never reset a user's DB.
12. Finish with the run command, actual smoke output, changed files, and remaining scope.

## Model work after the skeleton

- Default base: `intfloat/multilingual-e5-small` for two separate training runs.
- Its model card declares MIT, RU/KK coverage, 12 layers, and 384-dimensional vectors.
- Pin the downloaded revision/license; claimed language coverage is not measured quality.
- Classifier: update encoder weights plus a classification head on real input texts.
- Retriever: contrastive fine-tuning on checked relevant pairs/triplets; avoid false negatives.
- Follow the E5 card's prefix, pooling, and normalization contract consistently.
- Preserve raw labels plus a reviewed system-to-canonical-topic mapping.
- Split by time and incident/near-duplicate family before augmentation or pair mining.
- Keep generated translations/paraphrases in train only, linked to their source family.
- Report classifier macro-F1 and class support separately for RU, KK, mixed, and regions.
- Compare lexical retrieval, original E5, and tuned E5 using human qrels and nDCG/Recall@k.
- Include RU→KK and KK→RU checks; report unjudged documents and sparse test slices.
- Tune abstention on development data; report accuracy together with accepted coverage.
- Save checkpoints, encoder weight-change evidence, split IDs, config, data hash, and metrics.
- No invented gains, no test-label tuning, and no synthetic-only completion claim.

## Task handoff and stopping rules

- Task format: `Issue / Goal / Allowed files / Inputs / Contract / Acceptance /
  Verification command / Dependencies / Evidence to return`.
- One task owns each edited file; read it first and preserve unrelated changes.
- Update the dated current-state note after a task; this document describes the
  initial planning-only state until real implementation evidence replaces it.
- Prefer a small branch/PR linked to its issue; never commit secrets or AI co-author trailers.
- Keep status and evidence in the agreed GitHub repository; do not create a second backlog.
- Do not publish, contact people, or change external configuration without task authority.
- Missing production text/labels/solutions blocks the dependent ML feature, not the skeleton.
- Unknown privacy/hosting conditions block real-data upload; continue with synthetic fixtures.
- Conflicting schemas block joins; keep unknown fields null and report the exact mismatch.
- Inadequate test coverage blocks a quality claim, not an honest experimental result.
- Do not reopen settled architecture/research unless new evidence changes the current task.
