# Deployment hardening: making the tested system the installable system

## Purpose

This change follows a clean-deployment failure in which Arisbe's web application
started successfully after the published setup procedure, but graph rendering did
not work. The immediate cause was small: the Node dependency `elkjs` had not been
installed. The process issue is more important: CI already knew to install that
dependency, while the supported user setup path did not.

This note records the failure, the reproduction method, and the release contract
that prevents a recurrence. It is intended as a constructive complement to the
project's existing test discipline. Arisbe already has substantial ELK integration
coverage and real Playwright E2E coverage; the missing layer was verification that
a clean checkout constructed the same environment those tests assumed.

## What failed

The published getting-started path instructed a new user to install Python
requirements with:

```bash
uv sync --extra dev --extra web
```

and then start Uvicorn. That was sufficient for the server and static UI to come
up. It was not sufficient for the production layout path. `ELKLayoutEngine`
invokes `src/elk_worker.js`, and that worker imports
`elkjs/lib/elk.bundled.js` from the Node dependency graph.

The canonical CI workflow, meanwhile, separately installed Node and ran
`npm install` before the test suite. Thus CI tested a manually completed
environment that the documented install procedure did not itself construct.

The resulting failure boundary was misleading:

1. Python installation succeeded.
2. The server started and the browser UI loaded.
3. A user reached a graphical operation.
4. Only then did the ELK worker discover that `elkjs` was unavailable.

This is a **deployment-contract / release-environment closure gap**, not an absence
of implementation tests. Existing tests correctly established that ELK and the
browser UI work when all runtime dependencies are present. They did not establish
that following the supported installation procedure produces that environment.

## Why the existing CI did not catch it

The workflow and the documentation independently encoded environment-construction
knowledge:

```text
CI                              documented install
--                              ------------------
uv sync                         uv sync
Node setup
npm install
Playwright install
pytest                          start server
```

Because CI repaired the missing Node side before testing, its green result could
not detect that the user-facing setup contract was incomplete.

The corrective invariant is:

> **CI must consume the supported installation procedure, not reproduce or augment
> it in workflow YAML.**

A production dependency added in the future therefore has one place to enter the
build contract. If bootstrap omits it, both a clean user install and CI fail the
same way instead of diverging.

## Reproduction and verification methodology

The hardening uses several independent observations so a green result is not just
"the server returned 200".

### 1. Filesystem / package evaluation

`tools/verify_deployment.py` checks the deployment inputs that must exist in a
checkout:

- `pyproject.toml` and `uv.lock`
- `package.json` and `package-lock.json`
- `src/elk_worker.js`
- an `elkjs` declaration consistent between `package.json` and the lockfile
- a SHA-256 receipt for the npm lockfile

It also records the actual Node and npm versions. CI retains this as
`build/deployment-artifacts/deployment-verification.json`.

This detects a class of failures that feature tests can obscure: missing files,
lockfile drift, or a package-manager graph that was never constructed.

### 2. Node module-resolution probe

The verifier executes the same module name used by the worker:

```bash
node -e "process.stdout.write(require.resolve('elkjs/lib/elk.bundled.js'))"
```

This is the smallest reproduction of the failed deployment. A checkout that ran
only the former Python setup step fails here; a correctly bootstrapped checkout
resolves the installed module.

### 3. Python -> Node -> ELK integration probe

The verifier then parses a real EGIF form, creates `ELKLayoutEngine`, invokes the
Node worker, and requires a complete non-empty `LayoutDTO`: positioned vertex,
positioned predicate, ligature, and positive viewport dimensions.

This proves more than `require.resolve`: the actual production boundary between
Python and Node is functioning and ELK returns geometry Arisbe can consume.

The same check is also a normal pytest gate in
`tests/test_deployment_contract.py`; it is intentionally non-skippable.

### 4. Browser E2E with screenshot

`tests/test_deployment_e2e.py` launches the real FastAPI application and a real
headless Chromium. It opens Agon, selects the same built-in model used by the
existing Agon E2E suite, requests interpretation, and requires:

- a visible SVG on the real canvas,
- non-trivial SVG dimensions and graphical marks,
- a rendered interpretation result from the actual application path.

The test takes a full-page screenshot in a `finally` block when
`ARISBE_DEPLOYMENT_SCREENSHOT` is set, so CI retains visual evidence on both
success and most browser-level failures. The canonical artifact is
`build/deployment-artifacts/deployment-smoke.png`.

This closes the original observable symptom: an install is not considered complete
merely because the web server starts; it must actually render a graph through the
production layout stack.

## The supported build/install contract

The canonical setup command is now:

```bash
python tools/bootstrap.py
```

The bootstrap is deliberately small and explicit. It:

1. requires Python 3.12+, `uv`, Node.js, and npm;
2. replays the locked Python graph with
   `uv sync --frozen --extra dev --extra web`;
3. replays the locked Node graph with `npm ci`;
4. runs `tools/verify_deployment.py` and writes a verification receipt.

For the full browser proof:

```bash
python tools/bootstrap.py --with-browser
```

CI uses:

```bash
python tools/bootstrap.py --ci
```

`--ci` is the same build contract plus Chromium installation with Playwright's
system dependencies and the screenshot-producing deployment E2E.

`npm ci` is intentional. A lockfile already exists, and deployment verification
should replay that committed graph rather than allow installation to rewrite or
renegotiate it.

## Recovery behavior

A partially installed or damaged checkout uses the same command as a clean one:

```bash
python tools/bootstrap.py
```

To diagnose without repairing:

```bash
uv run python tools/verify_deployment.py
```

The verifier does not install anything. It reports the failed boundary and exits
non-zero. Bootstrap is the explicit mutating operation. This keeps startup from
silently modifying an operator's environment while still providing a direct
recovery path.

## CI contract

`.github/workflows/canonical.yml` is intentionally no longer allowed to contain
its own `uv sync`, `npm install`, or `npm ci` steps. It provides platform
prerequisites (Python, uv, Node, Graphviz), invokes `tools/bootstrap.py --ci`,
uploads the deployment receipt and screenshot even if a later step fails, and then
runs the full suite.

`tests/test_deployment_contract.py` enforces this delegation as a repository-level
invariant. This is slightly unusual for a test, but the behavior under test is a
release process encoded across documentation and workflow files; allowing those
surfaces to drift independently was the defect.

## Guidance for future dependency changes

When a feature adds a production dependency outside the Python graph:

1. add it to a committed lockfile;
2. teach the canonical bootstrap how to construct it;
3. extend deployment verification with the smallest direct probe of that boundary;
4. add or extend an integration probe that exercises the production call path;
5. keep at least one browser smoke whose assertion is user-observable behavior;
6. make CI call the same bootstrap rather than installing the dependency itself;
7. update the user-facing prerequisite text in the same change.

The useful distinction is:

- **feature/integration tests:** "does this subsystem work in a correct environment?"
- **deployment verification:** "does the supported procedure create a correct environment?"

Both are necessary for a graphical application whose production path crosses
language/runtime boundaries.

## Scope

This hardening does not change the calculus, graph semantics, ELK algorithm, or
browser feature behavior. It makes an existing production dependency explicit and
makes the already-tested graphical stack part of the release gate.

A separate startup-readiness check could improve the error shown when an operator
starts Arisbe from a manually altered environment. It is not required to close the
root process failure: the first priority is that a supported installation is
constructed and verified identically in documentation and CI.
