#!/usr/bin/env bash
# ruff + mypy pineados al lock dentro de un contenedor efímero.
set -euo pipefail
cd /app
pip install -q 'ruff==0.8.6' 'mypy==1.20.2'
ruff check src/ tests/
mypy --strict src/
