# GFont Converter — задачи

## Progress

| ID | Задача | Статус | Факт |
| --- | --- | --- | --- |
| TSK-001 | Подтвердить исходник и зафиксировать исходное состояние | Готово | Исходный `main`, коммит `2aa8910`; bundled exe совпал с копией на Desktop; Gitleaks tree/history: 0 |
| TSK-002 | Небольшой UI polish без изменения сценариев | Готово | Обновлены палитра, контраст, фокус, отступы; собранный GUI открыл синтетический demo и показал сетку/карточку |
| TSK-003 | Русская документация и безопасный скриншот | Готово | README и `docs/format.md` обновлены; `docs/screenshot.jpg` снят на synthetic demo, старый PNG удалён; MIT-атрибуция сохранена |
| TSK-004 | Проверки, сборка и финальный секрет-скан | Готово | финальный pytest/Ruff прошли; smoke: 5/5; PyInstaller и exe smoke/convert прошли; Gitleaks tree 30.51 MB: 0; published tree/history: 0; GitHub Actions зелёные |
| TSK-005 | Создать GitHub-репозиторий и отправить изменения | Готово | `Ka1nnnn/gfont-converter` создан private; затем владелец явно запросил public, текущая видимость проверена через GitHub |
| TSK-006 | Удалить машинно-локальные пути из smoke-режима | Готово | Старые fallback-пути удалены; поиск по актуальному дереву: совпадений нет; smoke 5/5 |
| TSK-007 | Исключить личные шрифты и сохранить их тестовые регрессии синтетическими данными | Готово | 3 файла из AyuGram не попали в publish tree; V5/V8/V9 builders добавлены; pytest прошёл |
| TSK-008 | Сделать GUI layout-проверку устойчивой к разрешению Windows runner | Готово | убрана фиксированная ширина 500 px; targeted/full pytest и GitHub Windows CI прошли |
| TSK-009 | Подготовить beginner README, проверить секреты и выпустить v1.1.1 | В работе | README обновлён; Gitleaks: дерево и история этого проекта — 0, четыре доступных соседних репозитория — 0; в кэше истории удалённого FoodBridge повторён один `generic-api-key` match без раскрытия значения; release asset/CI ещё не подтверждены |

## Задачи

- [x] **TSK-001** — Requirement: FR-4. Deliverables: точный проект и исходная Git-ревизия. Acceptance: exe совпадает с копией на рабочем столе, дерево чистое. Факт: см. `2aa8910` и Gitleaks history/tree = 0.
- [x] **TSK-002** — Requirement: FR-1, NFR-1, NFR-2, AC-1. Deliverables: ограниченные изменения стилей/отступов Tkinter. Acceptance: действия GUI и логика конвертации не меняются. Факт: GUI собранного exe открыл синтетический V5 и показал 16 глифов.
- [x] **TSK-003** — Requirement: FR-2, FR-3, FR-5, NFR-3, AC-2, AC-3, AC-6. Deliverables: русская документация, техническая справка и актуальный безопасный скриншот. Acceptance: шаги GUI/CLI соответствуют исходнику, MIT-атрибуция сохранена. Факт: `README.md`, `docs/format.md`, `docs/screenshot.jpg`; прежний PNG удалён.
- [x] **TSK-004** — Requirement: FR-4, FR-6, AC-4, AC-6. Deliverables: результаты pytest, Ruff, PyInstaller, packaged CLI, SHA-256 и Gitleaks. Acceptance: все команды зелёные, финальное дерево/история без находок. Факт: pytest PASS; Ruff PASS; source smoke 5/5; PyInstaller exit 0; packaged smoke/convert exit 0; SHA-256 `B8E81A72CBD750DED9FCA32FF796CB3ADE63CCF6A1E47C1EFFBF0F0495F38724`; Gitleaks tree/history 0; [GitHub Actions PASS](https://github.com/Ka1nnnn/gfont-converter/actions/runs/41686934891).
- [x] **TSK-005** — Requirement: FR-5, AC-5. Deliverables: GitHub-репозиторий и push. Acceptance: remote main содержит проверенный commit, без утечки секретов. Факт: репозиторий создан private, после прямого запроса владельца видимость переключена на public.
- [x] **TSK-006** — Requirement: FR-7, AC-7. Deliverables: smoke-пути только к комплектным fixtures; подтверждённый `--smoke`. Acceptance: исходник без локальных абсолютных путей, smoke проходит. Факт: путь-скан совпадений не нашёл; smoke 5/5.
- [x] **TSK-007** — Requirement: FR-8, AC-8. Deliverables: synthetic builders для V5/V8/V9 и регрессионные тесты без AyuGram-файлов. Acceptance: тесты проходят, каталог пользовательских шрифтов отсутствует в publish-снимке. Факт: `tests/fixtures/ayugram/` исключён; тесты используют `tests/gfont_helpers.py`; pytest PASS.
- [x] **TSK-008** — Requirement: NFR-4, AC-9. Deliverables: адаптивное утверждение ширины сетки в `tests/test_gui_layout.py`. Acceptance: локальные и GitHub Windows-тесты проходят при разной ширине виртуального экрана. Факт: targeted test PASS; полный pytest PASS; Ruff PASS; [GitHub Windows CI PASS](https://github.com/Ka1nnnn/gfont-converter/actions/runs/41686934891).
- [ ] **TSK-009** — Requirement: FR-2, FR-9, FR-10, AC-2, AC-10..AC-12. Deliverables: понятные инструкции для обычного пользователя и `v1.1.1` release с Windows `.exe`. Acceptance: публичная страница релизов содержит проверенный asset, его SHA-256 совпадает с локальной сборкой, GitHub Actions зелёные, секрет-сканы завершены. Факт: README обновлён; репозиторий подтверждён как public; секрет-сканы завершены для этого проекта и четырёх доступных репозиториев. В истории локального кэша удалённого `FoodBridge` воспроизводится один `generic-api-key` match в `dist/assets/index-CiFiCJYW.js` (`bdfd927...`), значение отредактировано; репозиторий сейчас недоступен на GitHub. Release asset и CI ещё ожидают подтверждения.
