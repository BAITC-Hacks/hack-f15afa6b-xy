# Pulse 109 — текущая передача

Актуальная ветка: `feat/laya-gpu-training`. Runtime: FastAPI, SQLite, vanilla JS. Production: https://xy.govtech-kz.com. GovTech / Pulse 109 не связан с HackAlem.

## Текущее состояние

- Полный demo flow работает от citizen intake до operator confirmation, Radar/incident, аналитики и PDF/XLSX. Действия оператора и происхождение данных сохраняются в аудите.
- Основная национальная витрина использует `synthetic_demo`: детерминированные текущие агрегаты для всех 20 регионов. Пользователь явно видит, что это синтетика.
- Режим `organizer` сохраняет неполный и устаревший time-series срез шести регионов. Исходный пакет содержит 7 регионов / 8 CSV, но только шесть сейчас представлены проверенными временными агрегатами; остальные регионы не подменяются нулями.
- Laya checkpoint `f56bca…80062` прошёл synthetic validation guardrails и используется только в `shadow`. Видимое решение остаётся за существующим классификатором и оператором.
- multilingual E5 checkpoint `8df810…755e` обучен для RU/KK similarity. При недоступном приватном endpoint приложение явно использует lexical fallback.
- self-hosted Qwen Copilot работает только через private/loopback endpoint. Невалидный ответ, timeout или выключенный GPU дают deterministic fallback; действия и сроки не выдумываются.
- GPU можно выключать: основное приложение, очередь, операторские решения и аналитика продолжают работать. Health честно показывает fallback.
- P0 operations layer работает в текущем FastAPI/SQLite: live transcript semantic checkpoints → Laya shadow/fallback signal → `INC-204` candidate → operator Apply/Ignore; затем internal `local_hex_v1` map, 15/30/60/120-minute deterministic forecast, staffing recommendation и seeded what-if simulation.
- Live partials не попадают в основной audit. `live_sessions` хранит текущий transcript, а `live_session_events` — только meaningful state changes. Основной audit создаётся после Apply; category остаётся pending до существующего human confirmation flow.

## Проверяемые ML evidence

- `training/evidence/laya-gpu-hardcases-shadow-20260926.json`: два seed, 2×T4, immutable shadow; средняя synthetic category accuracy `0.7031`, macro-F1 `0.6744`, calibrated ECE `0.0440`.
- `training/evidence/similarity-e5-gpu-20260928.json`: selected seed-29-best; synthetic test nDCG@10 `0.7302 → 0.9662`, cross-language Recall@5 `0.357 → 0.625`, веса изменились.

Это доказательство запуска обучения, калибровки и serving path. Оно не доказывает production accuracy на обращениях граждан. До появления разрешённого real holdout Laya остаётся shadow, E5 — operator-assist, а любые решения подтверждает человек.

## Важные границы конфигурации

- `P109_DEMO_MODE=1` действует только вместе с `P109_AUTH_DISABLED=1` в локальном loopback demo. При включённой авторизации intake всегда гражданский и приватный до согласия и модерации; оба флага запрещены на публичном deployment.
- Номер-only tracking и подписка доступны только для синтетического local demo. Для гражданской production-заявки интерфейс честно сообщает о необходимости защищённого owner-auth кабинета; публичная карта открывается только после согласия и операторской модерации.
- `synthetic_demo` / `organizer` — отдельный API/UI-фильтр аналитики и не зависит от demo mode.
- `P109_AUTH_DISABLED=1` разрешён только для локального loopback demo; production auth включён.
- Laya, similarity, Qwen, STT и TTS endpoints должны оставаться private/loopback. API keys — только в `.env`, вне Git.

## Быстрая проверка

```sh
python3 scripts/smoke.py
python3 scripts/check_demo.py
python3 scripts/check_laya.py
python3 scripts/check_similarity.py
python3 scripts/check_copilot.py
python3 scripts/check_auth.py
python3 scripts/check_voice.py
python3 scripts/check_operations.py
python3 scripts/check_coverage.py
git diff --check
```

Запуск текущей сохранённой demo-базы:

```sh
DATABASE_PATH=data/pulse109.db P109_DEMO_MODE=1 P109_AUTH_DISABLED=1 \
P109_DECISION_PROVIDER=shadow \
P109_LAYA_BASE_URL=http://127.0.0.1:8001 \
P109_LAYA_CHECKPOINT_ID=f56bcae3d1270eb4ca271569eb7e3c3ce4b997c7ae0bd6f510f8f79c42480062 \
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8769
```

Проверка production после delivery:

```sh
ssh xy@82.115.43.223
cd ~/pulse109
systemctl --user restart pulse109.service
curl -fsS http://127.0.0.1:8025/api/health | python3 -m json.tool
curl -fsS https://xy.govtech-kz.com/api/health | python3 -m json.tool
journalctl --user -u pulse109.service -n 100 --no-pager
```

## Demo acceptance

Главный проход: **Live Call** → partial transcript → Laya shadow/fallback signal → `INC-204` candidate → Apply → **Operations Map** hotspot → **Command Center** 60-minute overload → staffing action → seeded what-if. Затем показать существующий human confirmation, 1–3 month analytics и source switch `synthetic_demo` / `organizer`. Подробный сценарий находится в [operations-delivery.md](operations-delivery.md).

Не заявлять: официальное национальное покрытие, production accuracy, автоматическое принятие решений, реальную доставку службе или доказанную экономию времени.
