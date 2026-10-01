<div align="center">
  <img src="static/pulse109-mark.svg" width="72" alt="Pulse 109 logo">
  <h1>Pulse 109</h1>
  <p><strong>AI-assisted citizen request operations for Kazakhstan</strong></p>
  <p>Приём обращений, помощь оператору и ситуационная аналитика в одном приложении.</p>
  <p>
    <a href="#русский">Русский</a> ·
    <a href="#english">English</a> ·
    <a href="https://xy.govtech-kz.com">Live demo</a>
  </p>
  <p>
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
    <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white">
    <img alt="Security checked with OWASP ZAP" src="https://img.shields.io/badge/Security-OWASP%20ZAP-00549E">
    <img alt="Languages: Russian, Kazakh, English" src="https://img.shields.io/badge/UI-RU%20%7C%20KK%20%7C%20EN-2F6FA8">
  </p>
</div>

![Operator workspace](docs/images/operator-workspace.png)

## Русский

### Что такое Pulse 109

Pulse 109 — демонстрационная система для обработки обращений граждан. Она объединяет веб- и голосовой приём, очередь оператора, RU/KK-классификацию, поиск похожих решённых случаев, работу с дублями и инцидентами, карту, всплески, прогнозы и отчёты.

ИИ предлагает категорию, срочность и похожие случаи. Окончательное решение всегда подтверждает оператор. Каждое изменение сохраняется в журнале аудита.

### Основные возможности

| Модуль | Что он делает |
|---|---|
| Приём обращения | Веб- и голосовой сценарий, RU/KK/mixed, адрес, карта, фото и видео |
| Кабинет оператора | Приоритетная очередь, карточка обращения, уточнения и подтверждение решения |
| Laya | Предлагает категорию, срочность, необходимость уточнения и спам-сигнал |
| Similarity | Находит похожие решённые обращения и помогает проверить дубли |
| Radar и инциденты | Показывает всплески, связывает обращения и сохраняет исходные записи |
| Аналитика | 20 регионов в synthetic demo, прогноз на 1–3 месяца, RU/KK-вопросы |
| Отчёты | Экспорт проверенных агрегатов в PDF и XLSX |
| Безопасность | Авторизация, CSRF, серверные сессии, rate limit, CSP и HSTS |

### Интерфейс

| Кабинет оператора | Публичная карта |
|---|---|
| Приоритеты, очередь, инциденты и действия оператора | Только обращения с согласием и модерацией |
| ![Operator queue](docs/images/operator-workspace.png) | ![Public request map](docs/images/public-map.png) |

### Как работает обработка

```mermaid
flowchart LR
    A[Веб или голос] --> B[Обращение]
    B --> C[Laya: предложение]
    B --> D[Поиск похожих случаев]
    C --> E[Проверка оператором]
    D --> E
    E --> F[Категория и служба]
    E --> G[Дубль или инцидент]
    F --> H[Аудит и аналитика]
    G --> H
```

### Быстрый запуск

Требуется Python 3.11+.

```sh
git clone https://github.com/BAITC-Hacks/hack-f15afa6b-xy.git
cd hack-f15afa6b-xy
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

DATABASE_PATH=/tmp/pulse109-demo.db \
P109_DEMO_MODE=1 \
P109_AUTH_DISABLED=1 \
python -m uvicorn app:app --host 127.0.0.1 --port 8769
```

Откройте [http://127.0.0.1:8769](http://127.0.0.1:8769). Флаги `P109_DEMO_MODE=1` и `P109_AUTH_DISABLED=1` разрешены только для локального demo на loopback.

### Проверка безопасности

1 октября 2026 года публичный сайт прошёл ограниченную пассивную проверку OWASP ZAP 2.17.0: 22 GET-запроса к точному origin, без входа в аккаунт, форм, загрузок, изменения данных и активных атак.

Проверка обнаружила два отсутствующих защитных заголовка. Оба исправлены в общем middleware, поэтому защита применяется к страницам, API, статическим файлам и ответам с ошибками.

| Результат проверки | Изменение |
|---|---|
| CSP отсутствовал на HTML-страницах | Добавлен `Content-Security-Policy`: только разрешённые скрипты, стили, карты, медиа и WebSocket; inline JavaScript удалён |
| HSTS отсутствовал на HTTPS-ответах | Добавлен `Strict-Transport-Security: max-age=31536000` для HTTPS |
| Низкий контраст зелёной кнопки | Цвет затемнён до уровня, подходящего для белого текста |

Это не означает, что система полностью защищена от всех классов атак. Пассивная проверка не охватывала авторизованные роли, IDOR/BOLA, активные injection-тесты, загрузки и бизнес-логику.

### Проверка проекта

```sh
python scripts/check_auth.py
python scripts/smoke.py
python scripts/check_demo.py
python scripts/check_privacy.py
python scripts/check_reports.py
python scripts/check_laya.py
python scripts/check_similarity.py
python scripts/check_copilot.py
```

### Данные и модели

- `synthetic_demo` содержит детерминированную демонстрацию для 20 регионов. Это не официальная статистика.
- `organizer` содержит пригодный временной срез только для 6 регионов; отсутствующие регионы не заменяются нулями.
- Laya и multilingual E5 обучались на GPU, но опубликованные метрики относятся к разделённым синтетическим наборам.
- Hosted OpenAI, private Qwen и deterministic fallback дают оператору черновик. Они не принимают окончательных решений.
- Реальные персональные данные, секреты и `.env` нельзя добавлять в Git.

Подробный технический статус: [`docs/handoff.md`](docs/handoff.md). Сценарий демонстрации и production runbook: [`docs/operations-delivery.md`](docs/operations-delivery.md).

---

## English

### What is Pulse 109?

Pulse 109 is a demonstration platform for citizen request operations. It combines web and voice intake, an operator queue, Russian/Kazakh classification, similar-case retrieval, duplicate and incident review, maps, surge detection, forecasts, and reports.

AI proposes categories, urgency, and related cases. A human operator confirms every decision, and the application records changes in an audit trail.

### Core capabilities

| Area | Capability |
|---|---|
| Intake | Web and voice flows, RU/KK/mixed language, address, map, photo, and video |
| Operator workspace | Prioritized queue, case review, clarification, and confirmation |
| Laya | Category, urgency, clarification, and spam proposals |
| Similarity | Related resolved cases and duplicate review |
| Radar and incidents | Surge detection while preserving every original request |
| Analytics | 20-region synthetic demo, 1–3 month forecasts, RU/KK questions |
| Reporting | Validated PDF and XLSX aggregate exports |
| Security | Authentication, CSRF, server-side sessions, rate limits, CSP, and HSTS |

### Architecture

Pulse 109 uses one FastAPI application, SQLite, and a vanilla HTML/CSS/JavaScript interface. Model serving endpoints are optional and private. The main application remains usable through explicit fallbacks when GPU or hosted model providers are unavailable.

### Local setup

```sh
git clone https://github.com/BAITC-Hacks/hack-f15afa6b-xy.git
cd hack-f15afa6b-xy
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

DATABASE_PATH=/tmp/pulse109-demo.db \
P109_DEMO_MODE=1 \
P109_AUTH_DISABLED=1 \
python -m uvicorn app:app --host 127.0.0.1 --port 8769
```

Open [http://127.0.0.1:8769](http://127.0.0.1:8769). Authentication bypass and demo mode are restricted to local loopback demonstrations.

### Security assessment and remediation

On 1 October 2026, the public site received a bounded OWASP ZAP 2.17.0 passive assessment: 22 exact-origin GET requests, unauthenticated, with no form submissions, uploads, mutations, or active attacks.

The assessment found missing CSP and HSTS headers. This repository now applies a restrictive, application-compatible CSP to every response, removes inline JavaScript, and sends one-year HSTS on HTTPS responses. The end-to-end authentication check verifies these headers together with session storage, CSRF protection, role enforcement, logout revocation, generic login errors, and rate limiting.

The assessment was intentionally limited. It does not rule out authorization, IDOR/BOLA, injection, upload, or business-logic vulnerabilities outside the passive unauthenticated scope.

### Honest limits

- National analytics use deterministic synthetic data and are not government statistics.
- The organizer time series currently covers six regions.
- Published model metrics are synthetic validation evidence, not production accuracy on citizen requests.
- AI output is operator assistance; a human remains responsible for decisions.
- Secrets, `.env`, and real citizen records must stay outside Git.

See [`docs/handoff.md`](docs/handoff.md) for current technical status and [`docs/operations-delivery.md`](docs/operations-delivery.md) for the demo and production runbook.

---

Pulse 109 is a GovTech project. It is not affiliated with HackAlem.
