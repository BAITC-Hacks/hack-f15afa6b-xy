# Pulse 109

Pulse 109 — демонстрационный ситуационный центр для обращений граждан Казахстана. Он объединяет веб- и голосовой приём обращения, RU/KK-классификацию, поиск похожих решённых случаев, контроль дублей и инцидентов, очередь оператора, всплески, прогноз и выгрузку отчётов. Окончательные решения о категории, приоритете, связи обращений и ответе подтверждает оператор.

GovTech / Pulse 109 не связан с HackAlem. Канонический репозиторий: https://github.com/Eliasans02/pulse109.

## Что работает

- форма гражданина с регионом, городом, координатами и фото/видео;
- публичная карта только для обращений с согласием и модерацией;
- операторская очередь, playbooks с preview, Radar и управление инцидентами;
- обученный Laya RU/KK checkpoint в `shadow`: категория, уточнение, спам и срочность;
- обученный multilingual E5 для похожих решённых обращений и проверки дублей;
- приватный self-hosted Qwen Copilot с проверкой схемы и безопасным fallback;
- голосовой RU/KK flow через приватные STT/TTS endpoints;
- live operator call: semantic checkpoints, Laya shadow signal, incident candidate and human Apply/Ignore;
- internal hex Operations Map, 15–120 minute queue forecast, staffing advice and seeded what-if simulation;
- alerts, прогноз на 1–3 месяца, ограниченные RU/KK-вопросы и PDF/XLSX.

Обучение Laya и E5 действительно выполнялось на NVIDIA GPU. Evidence хранится в `training/evidence/`. Метрики получены на group-separated синтетических RU/KK наборах и показывают работоспособность ML pipeline, но не production accuracy на реальных обращениях.

## Два режима аналитики

| Режим | Покрытие | Назначение |
|---|---:|---|
| `synthetic_demo` | 20 регионов, 24 месяца до текущего | Основной национальный demo; детерминированная синтетика, не официальная статистика |
| `organizer` | 6 регионов в time-series, устаревшие агрегаты | Честный просмотр пригодной части пакета; отсутствующие регионы не заменяются нулями |

Режим передаётся параметром `data_origin` в `/api/alerts`, `/api/forecast`, `/api/query` и `/api/reports`. Он не связан с `P109_DEMO_MODE`: аналитический источник выбирается отдельно.

`P109_DEMO_MODE=1` действует только вместе с `P109_AUTH_DISABLED=1` на локальном loopback demo: intake помечается как синтетический и может быть показан на карте. При включённой авторизации новое обращение всегда считается гражданским и остаётся приватным до согласия и модерации. Оба флага запрещены на публичном deployment.

## Локальный запуск

Python 3.11+:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
DATABASE_PATH=data/pulse109.db \
P109_DEMO_MODE=1 \
P109_AUTH_DISABLED=1 \
P109_DECISION_PROVIDER=shadow \
P109_LAYA_BASE_URL=http://127.0.0.1:8001 \
P109_LAYA_CHECKPOINT_ID=f56bcae3d1270eb4ca271569eb7e3c3ce4b997c7ae0bd6f510f8f79c42480062 \
python -m uvicorn app:app --host 127.0.0.1 --port 8769
```

Открыть http://127.0.0.1:8769. Для чистого прогона задайте новый `DATABASE_PATH` в `/tmp`; не удаляйте рабочую SQLite-базу. `P109_AUTH_DISABLED=1` допустим только на loopback demo.

Проверка:

```sh
curl -fsS http://127.0.0.1:8769/api/health | python3 -m json.tool
curl -fsS 'http://127.0.0.1:8769/api/alerts?data_origin=synthetic_demo' | python3 -m json.tool
curl -fsS 'http://127.0.0.1:8769/api/forecast?horizon_months=3&data_origin=synthetic_demo' | python3 -m json.tool
python scripts/smoke.py
python scripts/check_laya.py
python scripts/check_similarity.py
python scripts/check_copilot.py
python scripts/check_operations.py
```

Laya, E5 и Qwen запускаются отдельно на приватном GPU или через SSH tunnel. Если GPU выключен, Pulse остаётся доступен: Laya shadow не меняет решение существующего классификатора, E5 явно переходит в `lexical_fallback`, Qwen — в `deterministic_fallback`. `/api/health` показывает фактический режим каждого сервиса.

Пример всех переменных находится в `.env.example`; секреты и `.env` не коммитятся.

## Production run

На VPS приложение работает из `~/pulse109`, слушает только `127.0.0.1:8025` и публикуется через TLS reverse proxy на https://xy.govtech-kz.com. После доставки проверенного commit:

```sh
cd ~/pulse109
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p ~/.config/systemd/user
cp deploy/pulse109*.service deploy/pulse109*.timer ~/.config/systemd/user/
chmod 600 .env
systemctl --user daemon-reload
systemctl --user enable --now pulse109.service pulse109-health.timer pulse109-backup.timer
curl -fsS http://127.0.0.1:8025/api/health | python3 -m json.tool
curl -fsS https://xy.govtech-kz.com/api/health | python3 -m json.tool
systemctl --user --no-pager --full status pulse109.service
```

Production `.env` должен оставлять авторизацию включённой, использовать сильный invite code и Secure cookies. GPU endpoints должны быть loopback/private. После перезапуска проверьте в health не только `status: ok`, но также `laya`, `similarity` и `copilot`; fallback допустим и должен быть видимым.

## Live demo

1. **Гражданин:** в локальном demo создать RU/KK-обращение с адресом, точкой на карте и фото. В production заявка остаётся приватной; оператор отдельно показывает согласие, редактирование PII и модерацию публикации.
2. **Очередь:** открыть карточку и запустить анализ; показать предложение Laya и его shadow-границу.
3. **Похожие:** показать решённые аналоги E5, затем подтвердить дубль/общий инцидент либо оставить отдельно.
4. **Решение:** выбрать категорию, приоритет и оператора; проверить preview и подтвердить человеком.
5. **Ситуационный центр:** показать alert/Radar, исходные обращения и создать либо обновить инцидент.
6. **Аналитика:** выбрать `Синтетика · 20 регионов`, показать прогноз на три месяца и задать RU/KK-вопрос.
7. **Отчёт:** скачать PDF и XLSX; затем переключить на `Организаторы · 6 регионов` и показать явную неполноту и устаревание источника.

Для повторяемого выступления используйте отдельную demo-базу. Данные, решения и аудит сохраняются между перезапусками, поэтому кнопки demo не должны сбрасывать рабочую базу.

Готовая презентация: [`deliverables/pulse109-govtech-demo.pptx`](deliverables/pulse109-govtech-demo.pptx).

## Честные ограничения

- национальная аналитика — синтетическая демонстрация возможностей, не статистика госорганов;
- Laya и E5 обучены и оценены на синтетике; canary/production quality на гражданах не доказана;
- Qwen работает в приватном контуре, предлагает черновик и не выполняет действие за оператора;
- delivery во внешние службы и гражданам остаётся demo-only;
- маршруты, нагрузки и пороги всплесков демонстрационные;
- пакет организаторов содержит 7 регионов / 8 CSV, но текущий проверенный time-series aggregate пригоден только для 6 регионов и не доказывает полноту истории.

Текущий технический статус и проверка: [docs/handoff.md](docs/handoff.md) и [docs/operations-delivery.md](docs/operations-delivery.md).
