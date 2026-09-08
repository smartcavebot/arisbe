#!/usr/bin/env python3
"""Construct and verify the supported Arisbe development/web environment.

The important property of this script is that the command used by a new user is
the same deployment-construction command used by CI.  Runtime dependencies must
not be provisioned only in workflow YAML.

Normal installation::

    python tools/bootstrap.py

Full browser/deployment proof (also the CI mode)::

    python tools/bootstrap.py --with-browser
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = Path("build/deployment-artifacts")


class BootstrapFailure(RuntimeError):
    pass


def _require_python() -> None:
    if sys.version_info < (3, 12):
        raise BootstrapFailure(
            f"Arisbe requires Python 3.12+; bootstrap is running under {sys.version.split()[0]}"
        )


def _require_tools() -> None:
    missing = [name for name in ("uv", "node", "npm") if shutil.which(name) is None]
    if missing:
        raise BootstrapFailure(
            "missing prerequisite tool(s): "
            + ", ".join(missing)
            + ". Install uv and Node.js (npm ships with Node.js), then rerun this command."
        )


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    proc = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if proc.returncode != 0:
        raise BootstrapFailure(
            f"command failed with exit code {proc.returncode}: {' '.join(command)}"
        )


def _artifact_path(value: Path) -> Path:
    return value if value.is_absolute() else ROOT / value


def bootstrap(*, browser: bool, system_browser_deps: bool, artifact_dir: Path) -> None:
    _require_python()
    _require_tools()

    # Both dependency graphs are locked.  This is intentionally centralized here:
    # CI and documentation call this script instead of reproducing these commands.
    _run(["uv", "sync", "--frozen", "--extra", "dev", "--extra", "web"])
    _run(["npm", "ci"])

    artifact_dir = _artifact_path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    receipt = artifact_dir / "deployment-verification.json"

    _run(
        [
            "uv",
            "run",
            "python",
            "tools/verify_deployment.py",
            "--json",
            str(receipt),
        ]
    )

    if browser:
        install = ["uv", "run", "playwright", "install"]
        if system_browser_deps:
            install.append("--with-deps")
        install.append("chromium")
        _run(install)

        screenshot = artifact_dir / "deployment-smoke.png"
        env = dict(os.environ)
        env["ARISBE_DEPLOYMENT_SCREENSHOT"] = str(screenshot)
        _run(
            ["uv", "run", "pytest", "tests/test_deployment_e2e.py", "-q"],
            env=env,
        )

    print("\nArisbe deployment bootstrap passed.")
    print(f"Verification receipt: {receipt}")
    if browser:
        print(f"Browser smoke screenshot: {artifact_dir / 'deployment-smoke.png'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-browser",
        action="store_true",
        help="install Chromium and run the real browser/SVG deployment smoke",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: browser smoke plus Playwright system dependencies",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help=f"verification receipt/screenshot destination (default: {DEFAULT_ARTIFACT_DIR})",
    )
    args = parser.parse_args()

    try:
        bootstrap(
            browser=args.with_browser or args.ci,
            system_browser_deps=args.ci,
            artifact_dir=args.artifact_dir,
        )
    except BootstrapFailure as exc:
        print(f"BOOTSTRAP FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
