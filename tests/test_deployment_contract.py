"""Tests for the release/deployment contract itself.

Feature tests answer whether Arisbe works in an already-correct environment.
These checks answer whether the supported setup path constructs and verifies that
environment, and keep CI/documentation from silently learning extra setup steps.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent


def test_deployment_verifier_exercises_installed_runtime(tmp_path):
    receipt = tmp_path / "deployment.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "tools" / "verify_deployment.py"),
            "--json",
            str(receipt),
        ],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    report = json.loads(receipt.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert {check["name"] for check in report["checks"]} == {
        "filesystem-and-lockfiles",
        "node-and-npm",
        "elkjs-module-resolution",
        "python-node-elk-layout",
    }


def test_ci_uses_the_same_bootstrap_contract_as_users():
    workflow = (REPO / ".github" / "workflows" / "canonical.yml").read_text(
        encoding="utf-8"
    )
    assert "python tools/bootstrap.py --ci" in workflow

    # Dependency-construction knowledge belongs in bootstrap.py.  If CI starts
    # carrying these commands again it can mask a broken documented install.
    forbidden = ("uv sync", "npm install", "npm ci")
    for command in forbidden:
        assert command not in workflow, f"CI bypasses bootstrap with {command!r}"


def test_getting_started_uses_the_canonical_bootstrap():
    getting_started = (REPO / "docs" / "GETTING_STARTED.md").read_text(encoding="utf-8")
    assert "python tools/bootstrap.py" in getting_started
    assert "Node.js" in getting_started
