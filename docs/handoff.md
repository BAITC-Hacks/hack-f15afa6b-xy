# Pulse 109 — текущая передача работы

## 2026-09-26: воспроизводимый Laya GPU pipeline

Ветка `feat/laya-gpu-training` добавляет детерминированные RU/KK симуляции, полное fine-tuning
encoder+head, отдельные validation/calibration/test splits, три независимых seed и проверяемый пакет
доказательств: логи, CUDA/NVIDIA inventory, история, температуры, метрики и SHA-256 исходных/обученных
весов. CI проверяет разбиение, PII-фильтр, детерминизм и обнаружение подмены артефактов.

Первый NVIDIA-прогон выполнен на 2×T4 для seed 17/29/43 и зафиксирован в
`training/evidence/laya-gpu-20260926.json` (commit `5ec3174`). Synthetic category accuracy выросла
с 0.45 до среднего 0.785417, но общий результат ухудшился: старое позиционное разбиение дало train
с 3.3% urgent и test со 100% urgent, поэтому urgency после обучения упал до 0%. Это доказало работу
pipeline, но заблокировало активацию checkpoint.

Phase 0 исправил разбиение, баланс loss и recall guardrails (`a7f7f80`, `b90ce83`, `638a124`). Повторный
2×T4 experiment проверил CE, CE+RLCD и CE с learning rate в 5 раз ниже. Все три seed-17 кандидата были
отклонены: лучший CE достиг 0.891369 validation accuracy и 0.7411 urgency macro-F1, но снизил поддержанный
`electricity` recall; RLCD дополнительно ухудшил NLL и signal recalls. Low-LR сохранил лучшую калибровку
на первой эпохе (ECE 0.040622), но не устранил category regression. Проверяемое rejected-run evidence
зафиксировано в `training/evidence/laya-gpu-phase0-rejected-20260926.json`; Brev VM остановлена после
43.5 минут, оценка compute $0.7737 при лимите $1.35.

`P109_DECISION_PROVIDER=mock` остаётся default, ни один новый checkpoint не активирован. Следующий этап —
добавить независимые, проверенные RU/KK semantic groups прежде всего для electricity, sewerage,
housing maintenance и urgent RU, затем повторить минимальный CE preflight на seed 17/29 и только
после прохождения guardrails — три seed. Команды и границы доказательств находятся в
[Laya integration](laya-integration.md#nvidia-fine-tuning-and-calibration).

Добавлен review gate `scripts/prepare_laya_review_batch.py`: приватные кандидаты превращаются в
редактируемую очередь, а в training JSONL экспортируются только завершённые human review с отдельным
подтверждением права на обучение. PII, изменение исходного текста после подготовки, незавершённые
решения, дубли и совпадающие пути блокируются. GPU runner требует и проверяет manifest экспорта и
сохраняет его в evidence bundle. Минимальный следующий набор: по 6 независимых RU и 6 KK групп для
`electricity`, `sewerage` и `housing_maintenance` (36 групп), включая срочные примеры; до получения
этих разрешённых текстов новый GPU-прогон не нужен.

## 2026-09-24: операции, Radar и координация

Актуальная ветка: `feat/operator-demo`, основана на GitHub main `ba5d8fd`.
GovTech / Pulse 109 не относится к HackAlem. Предыдущие разделы ниже — исторические передачи.
Новый интерфейс `/`, прежний `/legacy`; runtime остаётся FastAPI + SQLite + vanilla JS.
[Текущий отчёт](operations-delivery.md) описывает функции, архитектуру, проверки, демо и ограничения.

- Playbooks: preview → подтверждение → одна транзакция с аудитом, повтором запроса и проверкой устаревшего состояния. Код этапа зафиксирован в `73d2106`.
- Radar: недавние синтетические обращения, индекс времени, тема/район/характер проблемы, ручное объединение и отклонение. Пороги `P109_RADAR_MIN_CASES` / `P109_RADAR_WINDOW_MINUTES`.
- Инцидент: ответственный, статус, важность, срок обновления, история, связанные оригиналы; конфликт версий возвращает 409. Завершение не закрывает обращения автоматически.
- Маршрутизация: четыре прежних уровня, прогнозная нагрузка, объяснение альтернатив и общая очередь. Проверяются 1600 комбинаций; это структура демо, не покрытие реальных служб.
- Presence: polling 5 с, TTL 20 с, уведомление об изменении, сохранение черновика. Команды Ctrl/Cmd+K всегда открывают preview.
- Проверено: backend playbooks 15, incidents 18, routing/presence 13; smoke 14, clarification 16, queue 8, demo 10, coverage 10, audit 8; UI demo 7 и три новых набора по 4. Планирование и diff-check проходят. Перезапуск и конкурентные изменения входят в серверные сценарии.
- Прежние operator UI 32 и operator flow 6 прошли в этом сеансе. Coverage UI на macOS/headless останавливается на старой проверке End в native select; тест сохранён, новый удалённый CI не запускался.
- Локальные коммиты не отправлены в GitHub. Следующий этап — проверенные реальные тексты/метки и интеграционные контракты, затем обучение двух отдельных моделей. Нельзя выдавать демо-правила за обученный AI.

## 2026-09-11: безопасный и клавиатурный операторский список

Ветка: `fix/operator-text-keyboard`, база: `e48902f` из открытого [PR #43](https://github.com/Eliasans02/pulse109/pull/43). Открыт [PR #44](https://github.com/Eliasans02/pulse109/pull/44) в `olga/data-coverage`, не в main; оба PR не слиты. Код: [24e6e21](https://github.com/Eliasans02/pulse109/commit/24e6e21ce510cb92b926664ddfe3b4c5b247731f); следующий commit обновляет только передачу. Актуальные HEAD/CI проверять через GitHub.

- **Задача:** только часть P109-09/P109-36: безопасный вывод строк и клавиатурный выбор. Полные задачи не закрыты.
- **Контракт до правки:** `static/app.js`, `static/style.css`, синтетический `scripts/check_operator_ui.cjs`, эта передача. Приёмка: HTML показывается буквально во всех динамических путях оператора; нативные кнопки доступны Tab/Enter/Space; фокус сохраняется; выбор не отправляет подтверждение.
- **Уточнение проверки:** `.github/workflows/ci.yml` запускает CI и для зависимых PR, оба браузерных теста — на Linux с Playwright 1.62.1 вне runtime приложения. Сценарии `check_coverage_ui.cjs` не ослаблялись и не изменялись. Установка браузера следует [официальному CI-руководству](https://playwright.dev/docs/ci).
- **Исправлено:** текст/атрибут title в очереди, похожие обращения и решения, предложение темы/службы/приоритета и сводка тем выводятся через текстовые DOM-узлы. Оставшийся innerHTML содержит только константную разметку. Выбор меняет выделение и aria-pressed, не пересоздаёт очередь; фокус виден и не теряется.
- **Не менялось:** API, SQLite, аудит подтверждений, ML, ingestion, реальные CSV, fixtures, research и runtime-зависимости. Навыки security-review/frontend-a11y использованы для текстовых DOM-узлов и нативных кнопок; это не полный security/accessibility audit.
- **TDD:** на базе e48902f все 4 новые UI-проверки дали FAIL; после правки все 4 PASS, exit 0. Локально также PASS: check_plan (42/17/35), 10 coverage, 14 smoke; git diff --check и JS syntax checks — exit 0. Chrome 152.0.7977.84, Playwright 1.62.1; синтетический скриншот просмотрен, HTML буквальный, фокус виден.
- **CI:** [run 34565794005](https://github.com/Eliasans02/pulse109/actions/runs/34565794005) для 24e6e21 — completed/success. Linux/Python 3.11: план, 14 smoke, 10 coverage, 8 synthetic audit, 4 operator UI и все 6 coverage UI сценариев — PASS. Исходные клавиатурные проверки покрытия сохранены.
- **Ограничение локальной QA:** исходный coverage UI на этом macOS/headless Chrome и Chromium Headless Shell останавливается на UI 2: End не меняет значение нативного select. Это не засчитано как локальный PASS; тот же неизменённый тест полностью прошёл в Linux CI.
- **Окружение:** Python 3.14.4, FastAPI 0.141.1, Uvicorn 0.52.4, NumPy 2.5.3 установлены в отдельную .venv по существующему requirements.txt. Песочница запрещала bind порта/запуск Chrome; сервер, smoke и браузерные тесты запускались с разрешением вне неё.

Checkout: `/Users/eliasansariy/.config/soloterm/demo/pulse109-operator`. Локальная проверка из его корня (сервер в отдельной консоли, отдельная синтетическая БД):
```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/check_plan.py
.venv/bin/python scripts/check_coverage.py
.venv/bin/python scripts/smoke.py
DATABASE_PATH=/private/tmp/pulse109-operator.qslzDd/ui.db .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8766
export NODE_PATH=/Users/eliasansariy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules
P109_BROWSER=chrome node scripts/check_operator_ui.cjs http://127.0.0.1:8766
P109_BROWSER=chrome node scripts/check_coverage_ui.cjs http://127.0.0.1:8766
```
Путь временной БД относится к этому запуску; в новом окружении создать свою временную папку через mktemp. NODE_PATH указывает на установленный вне проекта Playwright. CI воспроизводит оба UI-теста на Linux без этих локальных путей.

## Предыдущая передача: компонент покрытия

Дата: 2026-09-10. Ветка: `olga/data-coverage`, база: `a6b9443`.
Код и проверенный research: [5ceec95](https://github.com/Eliasans02/pulse109/commit/5ceec958eacb217f41b666f3fe69aee28a741e5a). [PR #43](https://github.com/Eliasans02/pulse109/pull/43) открыт, не слит; последующий commit обновляет только ссылки этой передачи. Актуальный HEAD проверять через git/PR.
Задача: [P109-26](https://github.com/Eliasans02/pulse109/issues/26), только компонент покрытия.

## Контракт до изменения кода

- **Goal:** руководитель различает наличие регионального файла, проверку полей и демо; неизвестное не становится нулём.
- **Allowed files:** `data_coverage.py`, `app.py` (только новый read-only endpoint), `static/index.html`, `static/coverage.js`, `static/style.css`, `scripts/check_coverage.py`, `scripts/smoke.py`, `.github/workflows/ci.yml`; точечные README/AGENTS/research/backlog/handoff updates. Без изменений ML, ingestion, complaint schema или аудита решений.
- **Inputs:** `planning/data_inventory.json`, `planning/data_matrix.json`, существующий `REGIONS`; исходные CSV не читает веб-приложение.
- **Acceptance:** 20 регионов, 7 поставленных/13 отсутствующих и 8 файлов выводятся из инвентаря; фильтр региона; статусы заголовков не объявляются проверкой содержимого; неизвестные строки/история/свежесть — null; отдельная подпись синтетических счётчиков; загрузка/пустое состояние/ошибка/повтор; управление клавиатурой.
- **Tests:** свежие `python scripts/check_plan.py`, `python scripts/smoke.py`; новые проверки источников, null, фильтра, рассогласования файлов и безопасного 503; браузерная проверка клавиатуры и состояний.
- **Dependencies:** P109-09 API доступен, issue #9 ещё открыт; P109-25 остаётся зависимостью полной витрины. Этот компонент не закрывает P109-26, P109-25 или национальное покрытие.
- **Evidence:** команды, exit status, синтетический скриншот, независимое ревью, focused PR без merge.

Уточнение контракта после локального аудита: разрешены `scripts/audit_received_csv.py`, его синтетические тесты, необязательный `scripts/check_coverage_ui.cjs` и числовой `local_profile` в существующем inventory. UI показывает проверенный подсчёт **CSV-записей**; уникальность обращений, значения полей и полнота истории остаются непроверенными. Исходные файлы и полный промежуточный отчёт не входят в Git.

## Передано и границы

- **Работает:** read-only `/api/data-coverage`, 20 регионов из существующих источников, 7/13/8, регион/наличие файла, клавиатура, загрузка/пустое состояние/503/повтор, защита от позднего ответа. Подсчитано 1 036 858 CSV-записей; личные строки не публикуются.
- **Mock:** классификация по правилам и кандидаты по теме, 20 синтетических fixtures, операторское подтверждение/аудит/SQLite-счётчики. Ни одна из двух моделей не обучена; настоящих метрик классификации/поиска нет.
- **Недоступно:** проверенные темы/агрегаты организаторов, общий инцидент/повтор, прогноз, alerts, NL, PDF/XLSX. Последние четыре модуля возвращают 501. Нет production deployment, полного покрытия или завершённого P109-26.
- **Данные:** все 8 файлов прочитаны локально, 0 blank/width/parser errors. Совпали размеры/заголовки; бинарная идентичность Drive, уникальность/перекрытие частей Павлодара, полнота истории, RU/KK и права на публикацию не установлены. Akmola `request_subject`: 3475/3506 заполнено, медиана 24, p95 50 символов — кандидат, не проверенный intake text. Караганда: оба формата времени неподдержаны; Акмола: 32 непустые даты не разобраны.
- **Решения:** стек и две отдельные будущие fine-tuning ветки E5 сохранены; код/активы аналогов не включены. Полезность прецедента оценивается независимо от типа связи, исходные обращения сохраняются. Никаких измеренных улучшений времени/качества не заявлено.

## Воспроизведение и фактические проверки

Linux/macOS setup и обычный запуск — README. В этом Windows-сеансе из корня workspace:
```powershell
$runtime = 'C:/Users/oarka/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $runtime -m venv work/pulse109/.venv
# Bundled runtime не содержит ensurepip; venv создан, pip bootstrap завершился ошибкой.
& $runtime -m pip --python work/pulse109/.venv/Scripts/python.exe install -r work/pulse109-olga/requirements.txt
$env:PYTHONUTF8='1'
Set-Location work/pulse109-olga
& ../pulse109/.venv/Scripts/python.exe scripts/check_plan.py
& ../pulse109/.venv/Scripts/python.exe scripts/check_coverage.py
& ../pulse109/.venv/Scripts/python.exe scripts/test_audit_received_csv.py
& ../pulse109/.venv/Scripts/python.exe scripts/smoke.py
```
Все четыре финальные команды **exit 0**: план 42 задачи/17 требований/35 групп источников; **10 coverage**, **8 audit**, **14 smoke** проверок. Исходный `a6b9443` отдельно дал 13 smoke, exit 0. На ограниченном Windows запуске tempfile/SQLite получал Access denied; те же тесты вне песочницы прошли. Это сбой окружения, код приложения ради него не меняли. Версии установленных библиотек записаны в README; исходные диапазоны requirements не менялись.

GitHub CI для implementation commit `5ceec95`: **success**, [test job](https://github.com/Eliasans02/pulse109/actions/runs/34498662422/job/102943543932) (Linux/Python 3.11; plan, smoke, coverage, synthetic audit). Браузерная проверка — локальная.

Повторный запуск адаптированного `scripts/audit_received_csv.py --source-dir <локальная папка> --output <путь вне папки CSV>`: **exit 0**, 8 complete, те же количества. Источник в этом сеансе: `C:/Users/oarka/OneDrive/Рабочий стол/Хакатон/GovTechCampPulse109`. Полный промежуточный отчёт остаётся вне Git.

Браузерная проверка (сначала сервер, отдельная synthetic БД):
```powershell
$env:DATABASE_PATH='C:/Users/oarka/Documents/Codex/2026-09-10/10-09-2026-18-10-your/work/coverage-ui-demo.db'
& ../pulse109/.venv/Scripts/python.exe -m uvicorn app:app --host 127.0.0.1 --port 8765
# В другой консоли; из корня репозитория:
$env:NODE_PATH='C:/Users/oarka/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
& 'C:/Users/oarka/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' scripts/check_coverage_ui.cjs http://127.0.0.1:8765
```
**Exit 0, 6 UI-сценариев:** loading, клавиатура/empty, Алматы city/region и поля, 503 + повреждённый JSON/повтор, поздний ответ, 7/13 фильтры и ширина 390px. Playwright 1.62.1 + установленный Edge; исполняемая browser-зависимость не добавлена в runtime. В первой версии QA-теста Enter открывал нативный select Edge; тест исправлен на End/Tab, повтор прошёл. Desktop screenshot просмотрен: читаемый, без гражданских записей. Независимое code review не нашло блокирующих новых дефектов; замечание о валидации даты источника исправлено и покрыто тестом.

## Исследование и интеграция

В `docs/research.md`, раздел «Аналоги»: **8 продуктов / 3 репозитория**, проверка 10.09.2026. Ключевые первичные ссылки: [OneService FAQ](https://www.oneservice.gov.sg/oschatbot-faqs/), [SeeClickFix duplicate lifecycle](https://www.civicplus.help/seeclickfix/docs/mark-a-request-as-a-duplicate), [NYC единица service request](https://home4.nyc.gov/site/311reporting/311-reports/service-requests.page). Три паттерна: свидетельства рядом с выводом, подтверждённая связь с сохранением оригиналов, локальная/языковая оценка. Рабочие установки аналогов не тестировали; рекламные эффекты не выдаются за метрики.

Проверенные кодовые базы: [FixMyStreet](https://github.com/mysociety/fixmystreet) и [Ushahidi backend](https://github.com/ushahidi/platform) — AGPL-3.0-or-later; [Mark-a-Spot](https://github.com/markaspot/mark-a-spot) — GPL-2.0-or-later. Pinned LICENSE/commit/release/зависимости приведены в research; для копирования понадобятся notices, проверка отдельных лицензий и соответствующие исходники, включая сетевое условие модифицированной AGPL-версии. Сейчас копирования нет. [E5 card](https://huggingface.co/intfloat/multilingual-e5-small): MIT, RU/KK заявлены, качество на 109 не измерено.

Изменения для Ильяса: новый endpoint и `data_coverage.py`, `static/coverage.js`, компонент в index/style; `local_profile` в inventory, новые scripts/CI и документы. **SQLite, intake/confirm и trained-model контракты не изменены.** При упаковке сервера включить оба `planning/data_*.json`; рассогласованные/отсутствующие файлы дадут 503. Даты проверки inventory не являются свежестью обращений. `record_count` — только CSV-записи, не использовать как готовый P109-25 aggregate. PR требует ревью Ильяса, merge не выполнен.

## Следующие три задачи

1. **Ильяс / независимый reviewer — проверить PR #43 и зависимый PR #44.** Сверить актуальные HEAD и CI, просмотреть минимальный diff, подтвердить отсутствие динамического HTML и работу Tab/Enter/Space. Оба UI-теста уже прошли в Linux CI для implementation commit; проверить статус финального HEAD. Не выполнять merge без отдельного решения Ильяса и не закрывать полностью P109-09/P109-26/P109-36.
2. **Ильяс — P109-03/P109-04, локальная семантическая проверка.** Проверить с человеком смысл Akmola `request_subject` на небольшой разрешённой выборке; фиксировать долю исходных текстов, RU/KK/mixed/unknown, связь с метками/исходом и условия использования. Приёмка: только сводный вердикт о пригодности и конкретных ограничениях, без публикации примеров; затем решение о двух обучающих корпусах. Наличие 13 недостающих регионов не блокирует корректный пилот на пригодной части.
3. **Нурали с Ольгой — QA покрытия, поддержка P109-26.** За 20–30 минут найти поставленный/отсутствующий регион, непроверенное поле и историю, отличить demo от CSV. Приёмка-цель: пять правильных ответов, записанные время/ошибки; не называть это ML-оценкой. Затем 12 синтетических пар для проверки формулировок связи по research.

## Первый запрос следующей модели

> Проведи независимое ревью Pulse 109: актуальная ветка fix/operator-text-keyboard поверх olga/data-coverage (PR #43). Проверь status/HEAD/CI, сохрани более новые правки. Прочитай AGENTS.md, docs/handoff.md, diff относительно базы, static/app.js, static/style.css, scripts/check_operator_ui.cjs и .github/workflows/ci.yml; остальные файлы только по необходимости. Проверь безопасный текстовый вывод, Tab/Enter/Space, сохранение фокуса и отсутствие автоматического подтверждения. Повтори проверки в подходящем окружении; отдельно отметь известное ограничение native select в macOS headless QA. Верни findings с файлами/строками или отсутствие находок с границами проверки. Не меняй код, не делай merge, не читай CSV и не повторяй research. После ревью следующая работа — локальная семантическая проверка P109-03/P109-04 с человеком, а не запуск обучения на неподтверждённом тексте.
