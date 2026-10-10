"""Regression tests for GitHub Actions' ephemeral profile encryption keys."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_ci_profile_encryption_key.py"
WORKFLOWS = ROOT / ".github" / "workflows"


def _run_generator(destination: Path, *, in_actions: bool) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GITHUB_ACTIONS"] = "true" if in_actions else "false"
    environment["GITHUB_ENV"] = str(destination)
    return subprocess.run(
        [sys.executable, str(GENERATOR)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_ci_keys_are_valid_unique_and_not_logged(tmp_path: Path) -> None:
    destination = tmp_path / "github_env"
    for _ in range(2):
        result = _run_generator(destination, in_actions=True)
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    keys = [line.removeprefix("PROFILE_ENCRYPTION_KEY=") for line in lines]
    assert all(line.startswith("PROFILE_ENCRYPTION_KEY=") for line in lines)
    assert keys[0] != keys[1]

    for key in keys:
        cipher = Fernet(key.encode("ascii"))
        assert cipher.decrypt(cipher.encrypt(b"CI test data")) == b"CI test data"


def test_ci_key_generator_refuses_non_actions_runtime(tmp_path: Path) -> None:
    destination = tmp_path / "outside_env"
    result = _run_generator(destination, in_actions=False)
    assert result.returncode != 0
    assert not destination.exists()


def test_ci_workflows_never_embed_literal_profile_keys() -> None:
    literal_key = re.compile(r"""^\s*PROFILE_ENCRYPTION_KEY:\s*["']?[A-Za-z0-9_-]{43}=""", re.MULTILINE)
    for path in WORKFLOWS.glob("*.yml"):
        workflow = path.read_text(encoding="utf-8")
        assert not literal_key.search(workflow), path.name
        if "PROFILE_ENCRYPTION_KEY_VERSION:" in workflow:
            assert workflow.count(
                "run: python3 scripts/generate_ci_profile_encryption_key.py"
            ) == workflow.count("uses: actions/checkout@"), path.name
