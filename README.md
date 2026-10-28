# Meiti Shangcheng Employee Management System

Group 2 coursework project · release `2.1.0-flask` · 28 October 2026

A local, single-store employee management application for the Meiti Shangcheng Zhengzhou store scenario: HR records and workforce totals, manual team scheduling, personal schedules and a source-restricted check-in. Built with Python, Flask, Jinja, SQLite and CSS; the running application uses no JavaScript.

## Stack

| Layer | Technology |
| --- | --- |
| Runtime | Python 3.12 (64-bit) |
| Web | Flask 3.1.3 (WSGI), Jinja2 3.1.6, Werkzeug 3.1.9 |
| Data | SQLite via the standard `sqlite3` module, 9 tables, foreign keys on |
| Front end | Server-rendered HTML and CSS, no application JavaScript (CSP `script-src 'none'`) |
| Tests | `unittest`, 40 core + 26 enhancement checks on an isolated temporary database |

## Run

Windows x64 with CPython 3.12 installed (keep pip and venv):

```
cd website
Start.cmd
```

Open http://127.0.0.1:5000/login. The launcher creates a virtual environment and installs the seven locked wheels from `website/wheelhouse` offline. First start creates `instance/workforce.sqlite`, 500 fictional employee records, five accounts and two sample weeks.

| Role | Account | Scope |
| --- | --- | --- |
| HR | `hr` | full store, 500 fictional records |
| Supervisor | `supervisor` / `supervisor2` | own team only |
| Employee | `employee` / `employee2` | own schedule and check-in |

Initial password for every demo account: `Demo!2026`. All people and schedules are fictional.

The internal Scrum board is separate: `cd scrum` then `Start_Board.cmd`, http://127.0.0.1:5001.

## Structure

```
website/            Flask application, templates, static CSS, tests, offline wheels
  app.py            request preparation, role decorator, 20 routes
  models.py         User, Employee, Team, ScheduleWeek, CheckIn
  workspace.py      read-only queries, filters, summaries, safe CSV
  database.py       schema, constraints, fictional seed data
  manage.py         installation commands (accounts, team, backup)
scrum/              internal Scrum board (Flask + SQLite)
docs/               five coursework documents and the code guide
presentation/       final deck and its PDF preview
```

## Tests

```
cd website
.venv\Scripts\python.exe tests\test_acceptance.py
.venv\Scripts\python.exe tests\test_enhancements.py
```

The suites use temporary databases. They write result JSON under `evidence/` when run from the full delivery package; inside this repository they are source for review.

## Scope and boundaries

- 500 is a record volume, not a concurrent-user promise.
- The release binds to loopback only; no HTTPS, real store network or physical-device acceptance is claimed.
- Check-in is a classroom source simulation (loopback allow-list), not proof of physical presence.
- Out of scope: automatic scheduling, inventory, payroll calculation, approvals/leave, multi-store, Jupiter integration, native apps, migration and managed hosting.

## Repository note

This repository was initialised on 28 October 2026 for the final submission, so the commit history starts on that date. Earlier work was tracked with versioned documents and evidence files, not with Git commits; the history here records the submission snapshot and the workflow used to assemble it. AI assisted source comparison, implementation, test scripting and document preparation; the team remains responsible for reviewing the code and explaining it.

Coursework: Project Plan (v2.2), URS 2.1, class diagram, GUI test plan, Scrum board and process record. See `docs/`.
