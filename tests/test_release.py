from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import release_check as release


def archives(directory: Path, extra: str | None = None, missing: bool = False) -> None:
    with zipfile.ZipFile(directory / "token_garden-0.1.0-py3-none-any.whl", "w") as wheel:
        for name in sorted(release.MODULES | release.DIST_INFO):
            wheel.writestr(name, "fixture")
    with tarfile.open(directory / "token_garden-0.1.0.tar.gz", "w:gz") as sdist:
        names = release.SOURCE_FILES | release.EGG_INFO
        if missing:
            names -= {"garden/cli.py"}
        if extra:
            names |= {extra}
        for name in sorted(names):
            member = tarfile.TarInfo(f"token_garden-0.1.0/{name}")
            member.size = 7
            sdist.addfile(member, io.BytesIO(b"fixture"))


def test_archive_inventory_accepts_only_reviewed_files(tmp_path: Path) -> None:
    archives(tmp_path)
    assert release.inspect_archives(tmp_path).suffix == ".whl"


@pytest.mark.parametrize(
    "extra", [".state/usage.sqlite3", ".venv/config", "garden/private.json", "../secret"]
)
def test_sdist_rejects_private_or_unreviewed_content(tmp_path: Path, extra: str) -> None:
    archives(tmp_path, extra)
    with pytest.raises(ValueError):
        release.inspect_archives(tmp_path)


def test_sdist_requires_application_source(tmp_path: Path) -> None:
    archives(tmp_path, missing=True)
    with pytest.raises(ValueError, match="missing"):
        release.inspect_archives(tmp_path)


def test_wheel_rejects_unreviewed_content(tmp_path: Path) -> None:
    archives(tmp_path)
    with zipfile.ZipFile(tmp_path / "token_garden-0.1.0-py3-none-any.whl", "a") as wheel:
        wheel.writestr("private.sqlite3", "private")
    with pytest.raises(ValueError, match="unexpected"):
        release.inspect_archives(tmp_path)


@pytest.mark.parametrize("kind", ["link", "prefix", "duplicate"])
def test_sdist_rejects_unsafe_members(tmp_path: Path, kind: str) -> None:
    archives(tmp_path)
    with tarfile.open(tmp_path / "token_garden-0.1.0.tar.gz", "w:gz") as sdist:
        member = tarfile.TarInfo("wrong/README.md" if kind == "prefix" else "token_garden-0.1.0/x")
        if kind == "link":
            member.type = tarfile.SYMTYPE
            member.linkname = "/private"
        sdist.addfile(member)
        if kind == "duplicate":
            sdist.addfile(member)
    with pytest.raises(ValueError):
        release.inspect_archives(tmp_path)


def test_run_propagates_failure_and_uses_explicit_directory(tmp_path: Path) -> None:
    assert release.run([sys.executable, "-c", "print('fixture')"], tmp_path) == "fixture\n"
    with pytest.raises(subprocess.CalledProcessError):
        release.run([sys.executable, "-c", "raise SystemExit(4)"], tmp_path)


def test_smoke_uses_only_explicit_synthetic_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("scripts.release_check.venv.create", Mock())
    commands: list[list[str]] = []

    def fake_run(command: list[str], cwd: Path) -> str:
        assert cwd == tmp_path
        commands.append(command)
        if "scan" in command:
            state = Path(command[command.index("--state") + 1])
            state.mkdir()
            ledger = state / "usage.sqlite3"
            ledger.write_bytes(b"fixture")
            ledger.chmod(0o600)
        return "--state 46 tokens fixture-model"

    monkeypatch.setattr(release, "run", fake_run)
    release.installed_smoke(tmp_path / "fixture.whl", tmp_path)
    scan = next(command for command in commands if "scan" in command)
    assert scan[scan.index("--sessions") + 1] == str(tmp_path / "sessions")
    assert len([command for command in commands if "--offline" in command]) == 2


def test_main_inspects_before_installing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archives(tmp_path)
    smoke = Mock()
    monkeypatch.setattr(release, "installed_smoke", smoke)
    monkeypatch.setattr(sys, "argv", ["release_check", str(tmp_path)])
    release.main()
    assert smoke.call_args.args[0] == tmp_path / "token_garden-0.1.0-py3-none-any.whl"
