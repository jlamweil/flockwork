# Flockwork PyPI Setup

How to publish the `flockwork` Python package to PyPI.

## Package structure

Flockwork is a single-source, `pyproject.toml`-only package (PEP 621):

- `l2/` — the implementation (imported by the `flockwork` console script via the
  `l2.inrepo:main` entry point)
- `pyproject.toml` — the **only** packaging metadata file
- `dist/` — build output (gitignored)
- `.pypirc` / `~/.pypirc` — PyPI publish credentials (gitignored, see below)

There is intentionally **no** `setup.cfg` or `setup.py` — pyproject is the single
source of truth (kept that way to avoid triple-file metadata drift).

Metadata is audited by the test suite (`tests/test_packaging.py`): empty (honest)
dependency list, `requires-python >=3.10`, author name with **no** email, SPDX
license `Apache-2.0`, and single-source packaging.

## Installing from PyPI

```bash
pip install flockwork
```

## Building

Uses `setuptools>=77` (PEP 639 SPDX string license). For a byte-reproducible wheel,
pin the source epoch:

```bash
SOURCE_DATE_EPOCH=1720000000 python3 -m build   # wheel is byte-identical across builds
python3 -m twine check --strict dist/*
```

## Publishing

The PyPI credential is **never** committed. Two secure options:

1. **Environment variables (recommended)** — twine reads `TWINE_USERNAME` /
   `TWINE_PASSWORD`, which we source from the host env:
   ```bash
   export PYPI_API_TOKEN="pypi-<project-scoped token>"
   TWINE_USERNAME="__token__" TWINE_PASSWORD="$PYPI_API_TOKEN" \
     python3 -m twine upload dist/*
   ```

2. **keyring** — store the token once and let twine fetch it:
   ```bash
   python3 -m keyring set pypi-token __token__   # paste token when prompted
   python3 -m twine upload dist/*
   ```

### Why NOT `${PYPI_API_TOKEN}` inside `.pypirc`

The `.pypirc` `password = ${PYPI_API_TOKEN}` placeholder is **not** expanded by
distutils/twine. If the literal string `${PYPI_API_TOKEN}` is sent as the password,
PyPI rejects the publish with **403** (this is the historical failure recorded in
`AGENTS.md`). Prefer the `TWINE_PASSWORD` env-var or keyring forms above; never rely
on shell-style substitution inside `.pypirc`.

## Security considerations

- Never commit a real token — `.pypirc` is gitignored.
- Scope the token to the `flockwork` project with **upload** permission before
  publishing (an upload-scoped token is required to avoid the historical 403).
- Rotate tokens regularly; keep them out of logs and CI caches.
- The actual publish is **owner-gated** (per ADR-0002): a coding agent does not
  silently upload — a human countersigns the release before `twine upload`.
