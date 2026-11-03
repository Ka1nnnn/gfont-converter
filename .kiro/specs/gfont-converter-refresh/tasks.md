# GFont Converter — задачи

## Progress

| ID | Задача | Статус | Факт |
| --- | --- | --- | --- |
| TSK-001 | Подтвердить исходник и зафиксировать исходное состояние | Готово | Исходный `main`, коммит `2aa8910`; bundled exe совпал с копией на Desktop; Gitleaks tree/history: 0 |
| TSK-002 | Небольшой UI polish без изменения сценариев | Готово | Обновлены палитра, контраст, фокус, отступы; собранный GUI открыл синтетический demo и показал сетку/карточку |
| TSK-003 | Русская документация и безопасный скриншот | Готово | README и `docs/format.md` обновлены; `docs/screenshot.jpg` снят на synthetic demo, старый PNG удалён; MIT-атрибуция сохранена |
| TSK-004 | Проверки, сборка и финальный секрет-скан | Готово | pytest и Ruff прошли после перехода на synthetic-регрессии; smoke: 5/5; PyInstaller и exe smoke/convert прошли; Gitleaks tree/history: 0 |
| TSK-005 | Создать закрытый GitHub-репозиторий и отправить изменения | Ожидает | — |
| TSK-006 | Удалить машинно-локальные пути из smoke-режима | Готово | Старые fallback-пути удалены; поиск по актуальному дереву: совпадений нет; smoke 5/5 |
| TSK-007 | Исключить личные шрифты и сохранить их тестовые регрессии синтетическими данными | Готово | 3 файла из AyuGram не попали в publish tree; V5/V8/V9 builders добавлены; pytest прошёл |

## Задачи

- [x] **TSK-001** — Requirement: FR-4. Deliverables: точный проект и исходная Git-ревизия. Acceptance: exe совпадает с копией на рабочем столе, дерево чистое. Факт: см. `2aa8910` и Gitleaks history/tree = 0.
- [x] **TSK-002** — Requirement: FR-1, NFR-1, NFR-2, AC-1. Deliverables: ограниченные изменения стилей/отступов Tkinter. Acceptance: действия GUI и логика конвертации не меняются. Факт: GUI собранного exe открыл синтетический V5 и показал 16 глифов.
- [x] **TSK-003** — Requirement: FR-2, FR-3, FR-5, NFR-3, AC-2, AC-3, AC-6. Deliverables: русская документация, техническая справка и актуальный безопасный скриншот. Acceptance: шаги GUI/CLI соответствуют исходнику, MIT-атрибуция сохранена. Факт: `README.md`, `docs/format.md`, `docs/screenshot.jpg`; прежний PNG удалён.
- [x] **TSK-004** — Requirement: FR-4, FR-6, AC-4, AC-6. Deliverables: результаты pytest, Ruff, PyInstaller, packaged CLI, SHA-256 и Gitleaks. Acceptance: все команды зелёные, финальное дерево/история без находок. Факт: pytest PASS; Ruff PASS; source smoke 5/5; PyInstaller exit 0; packaged smoke/convert exit 0; SHA-256 `B8E81A72CBD750DED9FCA32FF796CB3ADE63CCF6A1E47C1EFFBF0F0495F38724`; Gitleaks tree/history 0.
- [ ] **TSK-005** — Requirement: FR-5, AC-5. Deliverables: закрытый GitHub-репозиторий и push. Acceptance: remote main содержит проверенный commit, без утечки секретов.
- [x] **TSK-006** — Requirement: FR-7, AC-7. Deliverables: smoke-пути только к комплектным fixtures; подтверждённый `--smoke`. Acceptance: исходник без локальных абсолютных путей, smoke проходит. Факт: путь-скан совпадений не нашёл; smoke 5/5.
- [x] **TSK-007** — Requirement: FR-8, AC-8. Deliverables: synthetic builders для V5/V8/V9 и регрессионные тесты без AyuGram-файлов. Acceptance: тесты проходят, каталог пользовательских шрифтов отсутствует в publish-снимке. Факт: `tests/fixtures/ayugram/` исключён; тесты используют `tests/gfont_helpers.py`; pytest PASS.
