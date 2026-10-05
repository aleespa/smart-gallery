# Repository Guidelines

## Project Structure & Module Organization

`smart_gallery/` contains the installable package. CLI entry points are in
`cli.py`; database code is in `models.py` and `db/`; workflows are in
`services/`; filtering and file organization are in `organize/`; analysis,
reporting, and the Streamlit UI are in their subpackages. `tests/` contains
unit, integration, and repository tests, with fixtures in `tests/resources/`.
Operational helpers and import scripts are in `custom/`. Read `README.md` and
`FACES.md` before changing CLI or face-processing behavior.

## Build, Test, and Development Commands

Use `uv` for environment and dependency management:

```bash
uv sync                 # install runtime and development dependencies
uv run pytest           # run the complete test suite
uv run pytest tests/unit # run fast unit tests only
uv run ruff check .     # lint Python files
uv run black --check .  # verify formatting
uv run smart-gallery --help
```

Install optional functionality when needed with `uv sync --extra faces` or
`uv sync --extra figures`. Tests marked `exiftool` require ExifTool on `PATH`;
`faces` tests require the optional face stack and suitable GPU setup.

## Coding Style & Naming Conventions

Target Python 3.13 and use four-space indentation. Use `snake_case` for
modules, functions, and variables; `PascalCase` for classes; and descriptive
lowercase CLI command names with hyphens where appropriate. Format with Black
and keep Ruff clean. Preserve the project’s type hints, dataclass/model
patterns, and logging conventions.

## Testing Guidelines

Pytest is the test framework. Name files `test_*.py` and test functions
`test_*`; place focused behavior tests in `tests/unit/` and workflow tests in
the appropriate top-level or integration test file. Add regression coverage
for changed database, filtering, import/export, and CLI behavior. Run the full
suite before submitting changes and explicitly run any relevant marker, such
as `uv run pytest -m exiftool` or `uv run pytest -m faces`.

## Commit & Pull Request Guidelines

Use short, imperative commit subjects that describe one focused change, such
as `Add split-person command` or `Fix export filtering`. Pull requests should
explain the user-visible effect, implementation scope, and validation commands;
link related issues when available. Include screenshots for dashboard or
reporting changes, and call out new dependencies, GPU requirements, migration
effects, or CLI compatibility concerns.

## Security & Configuration Tips

Do not commit photo libraries, generated catalogs, credentials, or machine
specific paths. Treat drive roots and SQLite catalogs as user data. Keep the
`onnxruntime` override intact: the GPU extra must use `onnxruntime-gpu`, not
the CPU package, which can silently disable GPU processing.
