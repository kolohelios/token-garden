"""Inspect release archives and exercise an installed wheel with synthetic usage only."""

from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

VERSION = "0.1.0"
MODULES = frozenset(
    f"garden/{name}.py"
    for name in (
        "__init__",
        "cli",
        "collector",
        "core",
        "display",
        "ledger",
        "runtime",
        "service",
        "ui",
    )
)
SOURCE_ROOT_FILES = frozenset(
    (
        "README.md",
        "architecture.md",
        "architecture-decision-record.md",
        "pyproject.toml",
        "MANIFEST.in",
        "Justfile",
        "garden.sh",
        "herdr-config.example.toml",
        "herdr-plugin.toml",
        "prometheus.yml",
        "flake.nix",
        "flake.lock",
        "uv.lock",
        "taplo.toml",
        "PKG-INFO",
        "setup.cfg",
    )
)
SOURCE_FILES = (
    SOURCE_ROOT_FILES
    | MODULES
    | frozenset(
        (
            "tests/__init__.py",
            "tests/helpers.py",
            "tests/test_collection.py",
            "tests/test_core.py",
            "tests/test_display_ui.py",
            "tests/test_runtime.py",
            "tests/test_service_cli.py",
            "tests/test_token_only.py",
            "tests/test_release.py",
            "scripts/__init__.py",
            "scripts/release_check.py",
            "specs/token-garden.md",
            "specs/public-release.md",
            ".shaka/repo.cue",
            ".github/workflows/validate.yml",
        )
    )
)
EGG_INFO = frozenset(("token_garden.egg-info/SOURCES.txt",))
DIST_INFO = frozenset(
    f"token_garden-{VERSION}.dist-info/{name}"
    for name in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD")
)


def check_inventory(
    names: frozenset[str], allowed: frozenset[str], required: frozenset[str]
) -> None:
    unexpected, missing = names - allowed, required - names
    if unexpected or missing:
        raise ValueError(f"Archive inventory mismatch: unexpected={unexpected}, missing={missing}")


def inspect_archives(directory: Path) -> Path:
    wheel = directory / f"token_garden-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        names = frozenset(archive.namelist())
        check_inventory(names, MODULES | DIST_INFO, MODULES | DIST_INFO)
        if len(names) != len(archive.namelist()):
            raise ValueError("Duplicate wheel entries")
    with tarfile.open(directory / f"token_garden-{VERSION}.tar.gz") as archive:
        files = tuple(member for member in archive.getmembers() if not member.isdir())
        if any(not member.isfile() for member in files):
            raise ValueError("Non-regular sdist entries")
        prefix = f"token_garden-{VERSION}/"
        if any(not member.name.startswith(prefix) for member in files):
            raise ValueError("Invalid sdist prefix")
        names = frozenset(member.name.removeprefix(prefix) for member in files)
        if any(".." in PurePosixPath(name).parts for name in names) or len(names) != len(files):
            raise ValueError("Unsafe or duplicate sdist entries")
        check_inventory(names, SOURCE_FILES | EGG_INFO, SOURCE_FILES | EGG_INFO)
    return wheel


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout


def installed_smoke(wheel: Path, directory: Path) -> None:
    environment = directory / "env"
    venv.create(environment, with_pip=True)
    python = environment / "bin/python"
    requirements = wheel.parent.parent / "reports/runtime-requirements.txt"
    run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--require-hashes",
            "--requirements",
            str(requirements),
        ],
        directory,
    )
    run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], directory)
    run([str(python), "-m", "pip", "check"], directory)
    command = [str(python), "-I", str(environment / "bin/token-garden")]
    assert "--state" in run([*command, "--help"], directory)
    root, state = directory / "sessions", directory / "state"
    root.mkdir()
    source = root / "synthetic.jsonl"
    records = (
        {"type": "session", "version": 3},
        {
            "type": "message",
            "id": "release-fixture",
            "timestamp": datetime.now(UTC).isoformat(),
            "message": {
                "role": "assistant",
                "provider": "fixture",
                "model": "fixture-model",
                "content": [{"type": "text", "text": "SYNTHETIC PRIVATE BODY"}],
                "usage": {"input": 10, "output": 2, "cacheRead": 30, "cacheWrite": 4},
            },
        },
    )
    payload = "".join(json.dumps(record) + "\n" for record in records)
    source.write_text(payload)
    run([*command, "scan", "--state", str(state), "--sessions", str(root)], directory)
    for window in ("1h", "24h"):
        output = run(
            [*command, "show", "--offline", "--state", str(state), "--window", window],
            directory,
        )
        assert "46 tokens" in output and "fixture-model" in output
        assert "SYNTHETIC PRIVATE BODY" not in output
    assert source.read_text() == payload
    assert b"SYNTHETIC PRIVATE BODY" not in (state / "usage.sqlite3").read_bytes()
    assert (state / "usage.sqlite3").stat().st_mode & 0o777 == 0o600


def main() -> None:
    wheel = inspect_archives(Path(sys.argv[1]).resolve())
    # The temporary working directory prevents imports from the source checkout.
    with tempfile.TemporaryDirectory(prefix="token-garden-release-") as temporary:
        installed_smoke(wheel, Path(temporary))
    print("Archive inventories and isolated installed-wheel smoke passed")


if __name__ == "__main__":
    main()
