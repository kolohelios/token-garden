from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import release_check as release


def fixture_bytes(name: str) -> bytes:
    if Path(name).name in release.LICENSES:
        return (Path(__file__).resolve().parent.parent / Path(name).name).read_bytes()
    if name.endswith(("METADATA", "PKG-INFO")):
        return (
            b"License-Expression: MIT OR Apache-2.0\n"
            b"License-File: LICENSE-MIT\nLicense-File: LICENSE-APACHE\n\n"
        )
    return b"fixture"


def archives(
    directory: Path, extra: str | None = None, missing: bool = False, omit: str | None = None
) -> None:
    with zipfile.ZipFile(directory / "token_garden-0.1.0-py3-none-any.whl", "w") as wheel:
        for name in sorted(release.MODULES | release.DIST_INFO):
            if name != omit:
                wheel.writestr(name, fixture_bytes(name))
    with tarfile.open(directory / "token_garden-0.1.0.tar.gz", "w:gz") as sdist:
        names = release.SOURCE_FILES | release.EGG_INFO
        if missing:
            names -= {"garden/cli.py"}
        if extra:
            names |= {extra}
        for name in sorted(names):
            if name != omit:
                payload = fixture_bytes(name)
                member = tarfile.TarInfo(f"token_garden-0.1.0/{name}")
                member.size = len(payload)
                sdist.addfile(member, io.BytesIO(payload))


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


@pytest.mark.parametrize("license_name", sorted(release.LICENSES))
@pytest.mark.parametrize("wheel", [False, True])
def test_archives_require_both_license_texts(
    tmp_path: Path, license_name: str, wheel: bool
) -> None:
    member = f"token_garden-0.1.0.dist-info/licenses/{license_name}" if wheel else license_name
    archives(tmp_path, omit=member)
    with pytest.raises(ValueError, match="missing"):
        release.inspect_archives(tmp_path)


@pytest.mark.parametrize(
    "metadata",
    [
        b"License-Expression: MIT\nLicense-File: LICENSE-MIT\nLicense-File: LICENSE-APACHE\n",
        b"License-Expression: MIT AND Apache-2.0\n",
        b"License-Expression: MIT OR Apache-2.0\nLicense-File: LICENSE-MIT\n",
    ],
)
def test_license_metadata_preserves_recipient_choice(metadata: bytes) -> None:
    with pytest.raises(ValueError):
        release.check_license_metadata(metadata)


@pytest.mark.parametrize("license_name", sorted(release.LICENSES))
def test_license_text_must_match_source(license_name: str) -> None:
    with pytest.raises(ValueError, match="differs"):
        release.check_license_text(license_name, b"truncated license")


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


def test_just_pins_release_environment_outside_devshell(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    for inherited in (None, str(tmp_path / "wrong-environment")):
        environment = {
            key: value for key, value in os.environ.items() if key != "UV_PROJECT_ENVIRONMENT"
        }
        if inherited is not None:
            environment["UV_PROJECT_ENVIRONMENT"] = inherited
        result = subprocess.run(
            [
                "just",
                "--justfile",
                str(root / "Justfile"),
                "--command",
                sys.executable,
                "-c",
                "import os; print(os.environ['UV_PROJECT_ENVIRONMENT'])",
            ],
            cwd=tmp_path,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == ".release-venv"


def test_main_ruleset_requires_prs_without_single_maintainer_deadlock() -> None:
    root = Path(__file__).resolve().parent.parent
    ruleset = json.loads((root / ".github/main-ruleset.json").read_text())
    assert ruleset == {
        "name": "Protect main",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_approving_review_count": 0,
                    "required_review_thread_resolution": True,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": "Gate"},
                        {"context": "Python and Shaka checks (ubuntu-24.04)"},
                        {"context": "Python and Shaka checks (macos-14)"},
                    ],
                },
            },
        ],
    }


def test_required_checks_match_workflow_and_shaka_policy() -> None:
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github/workflows/validate.yml").read_text()
    policy = (root / ".shaka/repo.cue").read_text()
    assert "name: Python and Shaka checks (${{ matrix.os }})" in workflow
    assert "os: [ubuntu-24.04, macos-14]" in workflow
    assert "name: Gate\n    needs: [validate]\n    if: always()" in workflow
    assert "VALIDATION_RESULT: ${{ needs.validate.result }}" in workflow
    assert 'run: test "$VALIDATION_RESULT" = success' in workflow
    assert "paths:" not in workflow and "paths-ignore:" not in workflow
    checks = re.search(r"requiredChecks:\s*\[(.*?)\]", policy, re.DOTALL)
    assert checks is not None
    assert re.findall(r'"([^"]+)"', checks.group(1)) == [
        "Gate",
        "Python and Shaka checks (ubuntu-24.04)",
        "Python and Shaka checks (macos-14)",
    ]
    assert "strictStatusChecks: true" in policy
    assert "allowForcePush: false" in policy
    assert "allowDeletion: false" in policy


def test_main_inspects_before_installing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archives(tmp_path)
    smoke = Mock()
    monkeypatch.setattr(release, "installed_smoke", smoke)
    monkeypatch.setattr(sys, "argv", ["release_check", str(tmp_path)])
    release.main()
    assert smoke.call_args.args[0] == tmp_path / "token_garden-0.1.0-py3-none-any.whl"
