#!/usr/bin/env python3
"""Verify that a checkout is complete enough to render Arisbe graphs.

This is deliberately a deployment check rather than a unit test.  It exercises
requirements that live across package-manager boundaries: repository files,
Node/elkjs resolution, and the Python -> Node -> ELK layout path used by the web
application.

Run after bootstrap::

    uv run python tools/verify_deployment.py

Use ``--json PATH`` to retain a machine-readable receipt (CI does this).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class VerificationFailure(RuntimeError):
    """A deployment-contract check failed."""


def _run(command: list[str]) -> str:
    proc = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise VerificationFailure(f"{' '.join(command)}: {message}")
    return proc.stdout.strip()


def _required_files() -> dict[str, Any]:
    paths = [
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / "package.json",
        ROOT / "package-lock.json",
        ROOT / "src" / "elk_worker.js",
    ]
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    if missing:
        raise VerificationFailure("missing required deployment files: " + ", ".join(missing))

    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    declared = package.get("dependencies", {}).get("elkjs")
    locked_root = lock.get("packages", {}).get("", {}).get("dependencies", {}).get("elkjs")
    if not declared:
        raise VerificationFailure("package.json does not declare production dependency 'elkjs'")
    if declared != locked_root:
        raise VerificationFailure(
            f"package.json/lockfile elkjs mismatch: package={declared!r}, lock={locked_root!r}"
        )

    return {
        "files": [str(path.relative_to(ROOT)) for path in paths],
        "package_lock_sha256": hashlib.sha256(
            (ROOT / "package-lock.json").read_bytes()
        ).hexdigest(),
        "elkjs_spec": declared,
    }


def _tool_versions() -> dict[str, str]:
    missing = [name for name in ("node", "npm") if shutil.which(name) is None]
    if missing:
        raise VerificationFailure(
            "missing required runtime tool(s): "
            + ", ".join(missing)
            + ". Install Node.js (which includes npm), then rerun tools/bootstrap.py."
        )
    return {
        "node": _run(["node", "--version"]),
        "npm": _run(["npm", "--version"]),
    }


def _elkjs_resolution() -> dict[str, str]:
    resolved = _run(
        [
            "node",
            "-e",
            "process.stdout.write(require.resolve('elkjs/lib/elk.bundled.js'))",
        ]
    )
    path = Path(resolved)
    if not path.is_file():
        raise VerificationFailure(f"Node resolved elkjs to a non-file path: {resolved}")
    return {"resolved_module": resolved}


def _elk_integration() -> dict[str, Any]:
    # Import from src exactly as the application and its tests do.
    sys.path.insert(0, str(SRC))
    from egif_parser_dau import parse_egif
    from elk_layout_engine import ELKLayoutEngine
    from style_loader import load_default_style

    egi = parse_egif("(Human *x)")
    dto = ELKLayoutEngine().generate_layout(egi, load_default_style())

    if len(dto.vertex_positions) != 1:
        raise VerificationFailure(
            f"ELK smoke expected 1 positioned vertex, got {len(dto.vertex_positions)}"
        )
    if len(dto.predicate_positions) != 1:
        raise VerificationFailure(
            f"ELK smoke expected 1 positioned predicate, got {len(dto.predicate_positions)}"
        )
    if not dto.ligature_paths:
        raise VerificationFailure("ELK smoke produced no ligature path")
    if dto.viewport_bounds.width <= 0 or dto.viewport_bounds.height <= 0:
        raise VerificationFailure("ELK smoke produced a non-positive viewport")

    return {
        "vertices": len(dto.vertex_positions),
        "predicates": len(dto.predicate_positions),
        "ligatures": len(dto.ligature_paths),
        "viewport": {
            "width": dto.viewport_bounds.width,
            "height": dto.viewport_bounds.height,
        },
    }


def _check(name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        detail = fn()
        print(f"PASS {name}")
        return {"name": name, "ok": True, "detail": detail}
    except Exception as exc:  # receipt should retain every independent failure
        print(f"FAIL {name}: {exc}", file=sys.stderr)
        return {"name": name, "ok": False, "error": str(exc)}


def verify() -> dict[str, Any]:
    checks = [
        _check("filesystem-and-lockfiles", _required_files),
        _check("node-and-npm", _tool_versions),
        _check("elkjs-module-resolution", _elkjs_resolution),
        _check("python-node-elk-layout", _elk_integration),
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=Path,
        metavar="PATH",
        help="write a machine-readable verification receipt",
    )
    args = parser.parse_args()

    report = verify()
    if args.json:
        output = args.json if args.json.is_absolute() else ROOT / args.json
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Deployment receipt: {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")

    if report["ok"]:
        print("Deployment verification passed: the production ELK layout path is usable.")
        return 0

    print(
        "Deployment verification failed. Run `python tools/bootstrap.py` after fixing "
        "the prerequisite reported above.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
