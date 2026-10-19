# flockwork 0.3.0 — Release run log

Ran 2026-10-19 by the orchestrator (TDD: metadata-gate tests written red-first in
`tests/test_packaging.py`, then made green by fixing the packaging).

## Final tree
- HEAD: `c6229c4 docs(pkg): …` (see §Commits). Worktree clean (except untracked
  `RELEASE-0.3.0-IMPL-PLAN.md`, `RELEASE-0.3.0-RUN.md`, `.zft/`).
- `pyproject.toml` is the single packaging source of truth (no `setup.cfg`/`setup.py`).

## Gates
| Gate | Result | Evidence |
|---|---|---|
| G1 — offline build green | ✅ | `python3 -m build` (isolated, CI-equivalent) → sdist + wheel OK; `twine check --strict dist/*` rc 0 |
| G2 — byte-reproducible wheel | ✅ | Build twice with `SOURCE_DATE_EPOCH=1720000000` → `cmp` wheel **byte-identical**; all 14 file contents identical (only zip-entry mtimes vary without the epoch) |
| G3 — clean-install smoke | ✅ | Fresh venv: `pip install ./dist/…whl`; `import l2.inrepo` OK (proves empty Requires-Dist is honest); console script wired. `--help` is **not** a valid verb (rc 2) — so the smoke is `import` + a real scratch-board round-trip: worker w1 claimed → fixed → landed on main → wrote `refs/swarm/verdicts/hello-demo` |
| G4 — metadata audit | ✅ | Wheel+PKG-INFO: `Author: J Lam` (no email), `Requires-Python: >=3.10`, `License-Expression: Apache-2.0`, no `Requires-Dist` |
| G5 — CI/paraity | ✅ | Full suite `pytest tests/` → **214 passed**; isolated build clean (no deprecation warnings) |

## Oracle corrections applied
- **W1** `flockwork --help` exits **rc 2** (`unknown mode`) — impossible as a smoke test;
  G3 uses `import l2.inrepo` + a scratch-board round-trip instead.
- **W2** "build twice → byte-identical" is only true with `SOURCE_DATE_EPOCH` pinned
  (wheels otherwise differ only in dist-info zip-entry mtimes; sdist timestamps vary).
- **License** needs two changes for a warning-free strict build: PEP 639 SPDX string
  `license = "Apache-2.0"` **and** `setuptools>=77` build req, with the deprecated
  `License ::` classifier removed (all three done).
- **403 root cause**: `${PYPI_API_TOKEN}` in `.pypirc` is not expanded by twine — the
  literal string is sent as the password → 403. Use `TWINE_PASSWORD` env / keyring.

## Commits (atomic)
1. `3cdba9c` `chore(pkg): make pyproject.toml the single packaging source of truth`
   — pyproject re-author + remove setup.cfg/setup.py + `.pypirc` gitignore + gate tests.
2. `c6229c4` `docs(pkg): document secure PyPI token handling; correct the 403 root cause`
   — rewritten `PYPI_SETUP.md`.
- Commit **C** (dist artifacts) **omitted** — `dist/` is gitignored; build products are
  not committed (this run log is the audit record instead).
- Commit **D** (CI drift-guard) **satisfied by** the metadata-gate tests, which the
  existing `.github/workflows/ci.yml` + `.gitlab-ci.yml` `pytest tests/` jobs already run.

## Remaining (owner-gated, NOT done here)
- **PyPI publish blocked — token scope (confirmed live 2026-10-19).** `twine upload` of
  `flockwork-0.3.0` returned **403 "Access was denied to this resource"** for project
  `flockwork`. The current `~/.pypirc` token authenticates (`__token__` accepted) but is
  **not an upload-scoped token** for the `flockwork` project. Package is **not** on PyPI.
  *Fix (owner, on pypi.org):* Account settings → API tokens → create a project-scoped
  token for `flockwork` with the **"Upload packages"** scope, then re-run
  `TWINE_USERNAME=__token__ TWINE_PASSWORD=<token> python3 -m twine upload dist/*`.
- npm `opencode-flockwork` publish — canonical plugin name (`flockwork-opencode` was a
  stale AGENTS.md label, never published; nothing to deprecate). Separate owner-gated step.
