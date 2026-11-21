set shell := ["/bin/sh", "-cu"]

export UV_PROJECT_ENVIRONMENT := ".release-venv"
python := ".release-venv/bin/python"

# A separate environment leaves an existing application's .venv untouched.
setup:
    uv sync --locked --extra dev

validate: setup
    .release-venv/bin/ruff check garden tests scripts
    .release-venv/bin/ruff format --check garden tests scripts
    .release-venv/bin/mypy garden tests scripts
    mkdir -p reports
    {{python}} -m pytest --cov=garden --cov-report=term-missing --cov-report=xml:reports/coverage.xml --junitxml=reports/tests.xml --cov-fail-under=90
    {{python}} -m pytest tests/test_release.py --cov=scripts --cov-report=term-missing --cov-report=xml:reports/release-coverage.xml --cov-fail-under=90

repo-check:
    nix flake check --all-systems --no-build
    shaka whitespace check
    shaka preflight --keep-going

package: setup
    mkdir -p reports
    uv export --locked --no-dev --no-emit-project --output-file reports/runtime-requirements.txt > /dev/null
    {{python}} -m build --no-isolation --outdir dist
    {{python}} -m twine check --strict dist/token_garden-0.1.0.tar.gz dist/token_garden-0.1.0-py3-none-any.whl

smoke: package
    {{python}} -m scripts.release_check dist

# Read-only live-policy verification; requires GitHub authentication.
audit-repo:
    case "$(git remote get-url origin)" in git@github.com:kolohelios/token-garden.git|https://github.com/kolohelios/token-garden.git|https://github.com/kolohelios/token-garden) ;; *) printf '%s\n' 'Refusing to audit a different repository' >&2; exit 1 ;; esac
    GH_HOST=github.com shaka repo audit --json

# CI and local release readiness use exactly this finite gate.
check: validate smoke repo-check

serve:
    sh garden.sh serve

show:
    sh garden.sh show

ui:
    sh garden.sh ui

prometheus:
    nix shell nixpkgs#prometheus --command prometheus --config.file=prometheus.yml --web.listen-address=127.0.0.1:9097 --storage.tsdb.path=.state/prometheus --storage.tsdb.retention.time=90d --storage.tsdb.retention.size=2GB
