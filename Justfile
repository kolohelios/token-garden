set shell := ["/bin/sh", "-cu"]

python := ".venv/bin/python"

setup:
    python3 -m venv .venv
    {{python}} -m pip install -e '.[dev]'

validate:
    .venv/bin/ruff check garden tests
    .venv/bin/ruff format --check garden tests
    .venv/bin/mypy garden tests
    {{python}} -m pytest --cov=garden --cov-report=term-missing --cov-fail-under=90

serve:
    sh garden.sh serve

show:
    sh garden.sh show

ui:
    sh garden.sh ui

prometheus:
    nix shell nixpkgs#prometheus --command prometheus --config.file=prometheus.yml --web.listen-address=127.0.0.1:9097 --storage.tsdb.path=.state/prometheus --storage.tsdb.retention.time=90d --storage.tsdb.retention.size=2GB
