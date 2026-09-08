"""Clean-deployment browser smoke for the production graphical path.

This test is intentionally narrower and stricter than the feature E2E suites:
Playwright is a hard requirement here (no importorskip), and success means a real
Arisbe page caused the server-side layout stack to produce a visible SVG.  A
screenshot is retained when ARISBE_DEPLOYMENT_SCREENSHOT is set.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

REPO = Path(__file__).parent.parent


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def app_url():
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "web_api.main:app",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        import urllib.request

        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                if urllib.request.urlopen(url + "/agon", timeout=2).status == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=10)
            raise RuntimeError(
                "Arisbe did not become ready for deployment smoke.\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        yield url
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _save_screenshot(page) -> None:
    target = os.environ.get("ARISBE_DEPLOYMENT_SCREENSHOT")
    if not target:
        return
    path = Path(target)
    if not path.is_absolute():
        path = REPO / path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception as exc:
        # Never replace the actual deployment failure with an artifact failure.
        print(f"deployment screenshot failed: {exc}", file=sys.stderr)


def test_supported_install_renders_a_real_graph(app_url):
    """Browser -> FastAPI -> Python layout -> Node/elkjs -> visible SVG."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            page.goto(app_url + "/agon", wait_until="domcontentloaded")
            page.wait_for_function(
                "document.querySelectorAll('#model-picker option').length > 1",
                timeout=10000,
            )

            # This same built-in model is exercised by the existing Agon E2E suite;
            # here it is the deployment canary because interpretation draws through
            # the production layout service rather than a fixture or mocked SVG.
            page.select_option("#model-picker", "ex:student-sea")
            page.click("#btn-interpret")
            page.wait_for_selector("#canvas svg", state="visible", timeout=15000)

            svg = page.locator("#canvas svg")
            box = svg.bounding_box()
            assert box is not None
            assert box["width"] > 20 and box["height"] > 20
            assert svg.locator("path, line, polyline, rect, ellipse, circle, text").count() > 0

            # Ensure the request reached a rendered interpretation, not merely an
            # empty shell whose static page happened to contain an SVG element.
            page.wait_for_selector("#interpret-result", state="visible", timeout=10000)
            text = page.text_content("#interpret-result") or ""
            assert "FALSE" in text and "Counterexample" in text
        finally:
            _save_screenshot(page)
            browser.close()
