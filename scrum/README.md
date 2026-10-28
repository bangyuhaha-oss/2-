# Group 2 local Scrum board

This is a separate Flask/SQLite internal board at **http://127.0.0.1:5001**. It does not use JavaScript and does not modify the employee website on port 5000.

**The coordinator starts an internal review today, 26 October 2026 at 15:00 (Asia/Shanghai, Beijing time, UTC+8).** This reviews the other chats' deliverables; it is not client or classroom acceptance, and no pass is pre-recorded. Preparation and verification continue before then, with reviewable artifacts, evidence and handoff notes supplied where possible and incomplete items stated honestly. The classroom demonstration remains on 28 October 2026; its specific time has not been confirmed. Existing task states and historical events are unchanged by this scheduling clarification.

## Install and start

Double-click `Start_Board.cmd`. It calls `..\website\Setup.cmd` to verify or install the shared Windows CPython 3.12 x64 environment and its offline wheels, then runs `board.py` with `..\website\.venv\Scripts\python.exe`. No separate board dependency installation is needed. The website does not have to be running. Close the terminal or press Ctrl+C to stop the board.

Manual start after environment setup:

```powershell
cd "交付_20261028\scrum"
& "..\website\.venv\Scripts\python.exe" board.py
```

The board listens only on loopback. It is a local internal workspace with no account authentication. Operator names are self-reported; they are not verified identities.

## Records and truthful process history

- `board.sqlite` is authoritative. Existing task states and historical entries are retained. A schema migration adds owner/evidence change details and identity-source labels; it does not rewrite old task states, timestamps, actor names, or notes.
- An empty database may import existing `tasks.csv` and `events.csv` snapshots. Importing them does not certify the underlying historical claims, and no past operation is inferred from a task's current status.
- The only proposed owners are 梁邦羽, 李远向, 柳懿轩, 刘帅, and 全组. Changing the proposed owner does **not** identify that person as the operator or prove their personal contribution.
- Each accepted update requires an explicit operator label and change note. The server appends its current UTC+8 time, old/new status, old/new proposed owner, old/new evidence, and a `self-reported-local` identity-source label in the same SQLite transaction.
- Legacy/imported entries are displayed as unverified and retain their original content. The board does not fabricate past Scrum ceremonies, sprint progress, personal reflection, rehearsal, peer execution, or teacher/client acceptance.
- Every POST requires the current session's CSRF token. Invalid requests do not change tasks or logs. Repeating a form without changing task fields is rejected.

## Evidence before Done

Enter delivery-relative file paths, separated by semicolons, such as:

```text
website/app.py; evidence/automated_results.json
```

To save Done, every reference must resolve to an existing file inside `交付_20261028`. Absolute paths, missing files, directories, parent traversal outside the delivery, and free text such as “all tests passed” are rejected. Symlinks/junctions resolving outside the delivery are rejected. Other statuses can keep an explanatory pending reference.

A passing file check establishes file availability only. It does not establish that the file proves a task's full criteria, that a proposed owner executed the work, or that independent peer testing/teacher acceptance happened. Review the referenced artifact separately. Existing Done tasks whose evidence later becomes unavailable are flagged without silently rewriting their stored status.

## CSV snapshots and backup

After a successful change, `tasks.csv` and `events.csv` are exported using individual atomic file replacements. The two exports are not a single filesystem transaction. SQLite remains authoritative; if an export fails, the UI explicitly reports that the change is saved in SQLite but the snapshots could not be refreshed. Preserve `board.sqlite`, both CSVs, and `.secret` when backing up a stopped board. Do not publish the secret.

## Isolated checks

```powershell
& "..\website\.venv\Scripts\python.exe" test_board.py
```

Tests create disposable databases, source snapshots, evidence files, and a test secret in a temporary directory. They hash the real `board.sqlite`, CSVs, and `.secret` before/after and fail if those files change. Results are written to `../evidence/website_scrum_results.json`. These are local simulated checks, not team contribution or classroom acceptance evidence.

The optional development-only browser check is `..\website\.venv\Scripts\python.exe browser_qa.py` and needs Playwright plus an installed Chrome browser. It serves a copied snapshot with a disposable database on a free temporary port, disables page JavaScript, and checks real form updates/rejections and desktop/mobile layouts. It appends browser observations to the same JSON and saves `scrum_*.png` screenshots under `../evidence/screenshots/`. Playwright is not needed to run the board.

For a separate configuration, use the `create_app(config)` factory with `DATABASE`, `DELIVERY_ROOT`, `IMPORT_DIR`, `EXPORT_DIR`, and `SECRET_KEY`. Importing `board` alone does not initialize or alter a database/secret. The optional `SCRUM_DB_PATH` and `SCRUM_SECRET_KEY` environment variables override the normal database/secret settings; use the full factory configuration for tests so exports and seed snapshots are also isolated.
