# AGENTS.md

A Hypothesis backend plugin (`backend="crosshair"`) that runs
[CrossHair](https://github.com/pschanely/CrossHair) symbolic execution under
[Hypothesis](https://hypothesis.readthedocs.io/). The provider lives in
`hypothesis_crosshair_provider/crosshair_provider.py`.

## Dev setup

This project uses [uv](https://docs.astral.sh/uv/).

```
uv sync
```

## Checks

Run all three before committing; CI runs them across Python 3.9–3.14.

```
uv run black --check .   # formatting (uv run black . to fix)
uv run isort --check-only .
uv run pytest
```

Tests live in `hypothesis_crosshair_provider/` (`*_test.py`).

## Conventions

- **Linting**: imports sorted with isort, format with black (line length 88).
- **Changelog**: User-facing changes go in the `## Changelog` section of `README.md`, under the `### Next Version` heading. Bump the version in `pyproject.toml` at release time.
- **Naming and doc strings**: Name functions and parameters by what they **do**, not by how they're used. Rename as function behaviors evolve. Doc strings should not include historical context or litigate design decisions. Describe current behaviors only.
- **Code comments**: Use a **very high bar** - very surpising or confusing behaviors only. Same rules as naming: current behaviors only, never explain decisions or changes in comments. (you can and should justify changes in PR descriptions, however)
