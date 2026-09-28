# Pulse 109 — operations delivery

Текущий checkout: `feat/laya-gpu-training`. Production: https://xy.govtech-kz.com. Backend — FastAPI и SQLite, frontend — vanilla JS. Все решения, изменяющие обращение или инцидент, подтверждаются оператором и записываются в аудит.

## Delivered

- Citizen intake: RU/KK текст, область → город, адрес, координаты, фото/видео и согласие на публикацию.
- Public map: только синтетические либо согласованные и прошедшие модерацию обращения.
- Operator workspace: очередь, карточка, уточнения, категория, приоритет, routing, reply draft и audit.
- Similarity: обученный multilingual E5 ранжирует решённые аналоги; оператор решает, является ли обращение дублем или частью общего инцидента.
- Laya: обученный и откалиброванный checkpoint предлагает категорию, уточнение, spam и urgency в shadow; существующее решение и human review остаются authoritative.
- Qwen Copilot: приватный self-hosted draft assistant с PII sanitation, pinned revision, строгой JSON schema и запретом выдуманных действий/сроков.
- Radar / incidents: объяснимый сигнал, просмотр исходных обращений, создание/связь инцидента, владелец, статус, severity, следующее обновление и version conflict protection.
- Analytics: alerts, 1–3 month forecast с rolling backtest, ограниченные RU/KK data questions, source/freshness metadata и настоящие PDF/XLSX.
- Operations: native authentication, private service endpoints, health timer и ежедневная SQLite backup rotation.

## Analytics contract

`synthetic_demo` — рекомендуемый режим выступления. Он содержит 24 месяца детерминированных синтетических агрегатов всех 20 регионов до текущего месяца и нужен для демонстрации национальной витрины, alerts и свежего прогноза. Это не официальная статистика и не смешивается с данными организаторов.

`organizer` содержит устаревший time-series aggregate шести регионов. В исходном пакете организаторов есть 7 регионов / 8 CSV, но седьмой регион пока не представлен проверенными временными агрегатами. Назначение режима — честно раскрыть coverage/freshness; недостающие регионы не восстанавливаются выдуманными нулями.

API:

```sh
curl -fsS 'http://127.0.0.1:8769/api/alerts?data_origin=synthetic_demo'
curl -fsS 'http://127.0.0.1:8769/api/forecast?horizon_months=3&data_origin=synthetic_demo'
curl -fsS -X POST http://127.0.0.1:8769/api/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Какие категории лидируют в Карагандинской области?","data_origin":"synthetic_demo"}'
curl -fLo /tmp/pulse109.pdf 'http://127.0.0.1:8769/api/reports?format=pdf&data_origin=synthetic_demo'
curl -fLo /tmp/pulse109.xlsx 'http://127.0.0.1:8769/api/reports?format=xlsx&data_origin=synthetic_demo'
```

## AI runtime and fallback

| Компонент | GPU online | GPU offline / invalid response |
|---|---|---|
| Laya | trained checkpoint, `shadow` audit | existing classifier remains visible; failure recorded |
| Similarity | trained multilingual E5 | explicit `lexical_fallback` |
| Qwen | private self-hosted copilot | deterministic summary/reply draft |
| STT/TTS | private RU/KK voice services | voice UI reports unavailability; text form remains available |

`/api/health` is the source of truth. A top-level `status: ok` means the web application is alive; inspect the nested component statuses before claiming that a GPU model is online.

Selected checkpoints:

- Laya shadow: `f56bcae3d1270eb4ca271569eb7e3c3ce4b997c7ae0bd6f510f8f79c42480062`.
- E5 similarity: `8df810a25f82e17aaa47ef07479c1be50f11efb4521b6b73c594b448acdc755e`.
- Qwen: `Qwen/Qwen3-4B-Instruct-2507`, pinned revision `cdbee75f17c01a7cc42f958dc650907174af0554`.

## Exact runbook

Local application:

```sh
cd /Users/eliasansariy/Documents/Codex/2026-09-24/new-chat/outputs/pulse109
. .venv/bin/activate
DATABASE_PATH=data/pulse109.db P109_DEMO_MODE=1 P109_AUTH_DISABLED=1 \
P109_DECISION_PROVIDER=shadow \
P109_LAYA_BASE_URL=http://127.0.0.1:8001 \
P109_LAYA_CHECKPOINT_ID=f56bcae3d1270eb4ca271569eb7e3c3ce4b997c7ae0bd6f510f8f79c42480062 \
python -m uvicorn app:app --host 127.0.0.1 --port 8769
```

Optional private GPU services, each in its own shell and with model files already present:

```sh
.venv/bin/python scripts/serve_pulse_laya.py \
  --checkpoint /srv/pulse109/laya/f56bcae3d1270eb4ca271569eb7e3c3ce4b997c7ae0bd6f510f8f79c42480062 \
  --device cuda --host 127.0.0.1 --port 8001
P109_SIMILARITY_CHECKPOINT_ID=8df810a25f82e17aaa47ef07479c1be50f11efb4521b6b73c594b448acdc755e \
P109_SIMILARITY_MODEL_PATH=/srv/pulse109/similarity/seed-29-best \
  .venv/bin/python -m uvicorn scripts.serve_similarity:app --host 127.0.0.1 --port 8004
P109_COPILOT_PORT=8005 P109_COPILOT_API_KEY="$P109_COPILOT_API_KEY" \
  scripts/run_qwen_copilot.sh
```

Production application:

```sh
ssh xy@82.115.43.223
cd ~/pulse109
systemctl --user daemon-reload
systemctl --user restart pulse109.service
systemctl --user enable --now pulse109-health.timer pulse109-backup.timer
curl -fsS http://127.0.0.1:8025/api/health | python3 -m json.tool
curl -fsS https://xy.govtech-kz.com/api/health | python3 -m json.tool
systemctl --user --no-pager --full status pulse109.service
journalctl --user -u pulse109.service -n 100 --no-pager
```

Production `.env` must keep authentication enabled, leave `P109_DEMO_MODE` unset and keep secrets outside Git. The national `synthetic_demo` analytics source remains available independently.

## Live-demo sequence

1. In the local demo, open **Гражданин**, select region/city, describe a RU/KK issue, place the map point and attach a photo; submit and copy the tracking ID. In production, show that citizen intake remains private and requires consent plus authenticated operator moderation before public-map publication; number-only tracking is synthetic-demo only until owner authentication is added.
2. Open **Очередь**, select the new card and run analysis. Explain that Laya is trained but shadow: category/spam/urgency are proposals.
3. Show **Похожие решённые обращения** and its scoring mode. Review evidence and choose either duplicate/common incident or separate issue; do not auto-link.
4. Select category, priority and recommended operator. Open preview and confirm. Show the audit event with proposed and confirmed values.
5. Open **Радар** or the alert panel, inspect supporting cases, then create/update an incident and set its next update. Original complaints remain intact.
6. Open **Ситуационный центр**, choose **Синтетика · 20 регионов**, show an alert and the three-month forecast, then ask: «Какие категории лидируют в Карагандинской области?»
7. Download PDF and XLSX. Switch to **Организаторы · 6 регионов** and point out stale freshness and incomplete coverage.

Presentation artifact: `deliverables/pulse109-govtech-demo.pptx` (10 slides, all ML and national metrics marked as synthetic).

## Verification gate

```sh
python3 scripts/smoke.py
python3 scripts/check_demo.py
python3 scripts/check_laya.py
python3 scripts/check_similarity.py
python3 scripts/check_copilot.py
python3 scripts/check_auth.py
python3 scripts/check_voice.py
python3 scripts/check_coverage.py
python3 scripts/check_backup.py
git diff --check
```

Expected safety behavior: existing SQLite data survives restart; failed GPU calls use named fallback; no model confirms a decision; organizer and synthetic aggregates never merge; downloads include source and freshness; public-server citizen text stays private until consent and moderation.

## Limits to state during review

- Synthetic ML metrics and national analytics demonstrate capability, not field accuracy.
- Laya is shadow-only and E5 is decision support; both need approved human holdouts for production QA.
- Qwen drafts are operator-reviewed; no external service action or deadline is inferred.
- External message/service delivery is demo-only.
- Organizer coverage is incomplete and stale; no claim of complete national history is made.
- Economic/time savings have not been measured on real operators.
