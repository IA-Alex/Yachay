#!/usr/bin/env bash
# Canonical local gate: lint, tests and coverage with the real exit codes.
#
# The test runner is `unittest` from the project virtualenv. `pytest` is not a
# project dependency: it cannot be resolved from the offline uv cache, so the
# dev group must not declare it (D-003 in docs/seguimiento-local.md).
#
# `-t .` pins the repository root as the discovery top-level directory, so the
# tests are imported as `tests.*`.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" != "" ]]; then
    echo "uso: $0" >&2
    exit 2
fi

RUFF=.venv/bin/ruff
PYTHON=.venv/bin/python
COVERAGE=.venv/bin/coverage

"$RUFF" check src tests desktop scripts
"$PYTHON" scripts/sync_api_docs.py
"$COVERAGE" run -m unittest discover -s tests/unit -t .
"$COVERAGE" report
