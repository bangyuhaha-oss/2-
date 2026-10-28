# Git workflow used for this submission

The repository was created on 28 October 2026, so every commit is dated that day. The history records how the submission snapshot was assembled; it does not claim day-by-day development commits from earlier weeks.

## Branching

- `main` is the reviewed line. It receives changes through merge commits, not direct commits.
- Short-lived branches carry one topic each, for example `docs/git-workflow`.
- A topic branch is merged with `--no-ff` so the integration point stays visible in `git log --graph`.

## Commit convention

```
<type>(<scope>): <summary>
```

Types: `feat`, `fix`, `docs`, `test`, `chore`. Scope is the area: `backend`, `ui`, `tests`, `scrum`, `docs`, `presentation`.

Examples from this repository:

- `feat(backend): Flask routes, domain models and SQLite schema`
- `feat(ui): role workspaces, schedule editor and print views`
- `test: 40 core and 26 enhancement checks`
- `docs: five coursework documents and code guide`

## Reviewing the history

```
git log --oneline --graph --decorate --all
git show --stat HEAD
git show v2.1.0 --stat
git branch -a
git tag -n
git diff HEAD~1 --stat
```

## Release identification

Annotated tag `v2.1.0` marks the demonstrated release. The application reads its own version from `website/app.py` (`RELEASE = '2.1.0-flask'`).

## Scope of the history

The repository contains source, tests, coursework documents and the presentation. Runtime state (`.venv`, `instance/`, databases, secrets) is ignored. The history does not establish teacher acceptance, peer testing or real-store operation.
