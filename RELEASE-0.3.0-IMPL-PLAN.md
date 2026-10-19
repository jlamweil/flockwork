# flockwork 0.3.0 — PyPI release: Implementation Plan

**Status:** EXECUTED 2026-10-19 (impl commits landed via TDD; see
`RELEASE-0.3.0-RUN.md` for the gate evidence + oracle corrections).
**Date:** 2026-10-19
**Owner:** orchestrator
**Target:** finish and publish the `flockwork` Python package as **0.3.0** on PyPI,
with metadata that is *truthful*, a build that is *reproducible and verified*, and a
git history that is *atomic and reversible*. Optionally publish the npm
`opencode-flockwork` plugin (treated as a separate, owner-gated step).

This plan supersedes the current uncommitted release draft in the worktree (see §3 —
that draft has metadata defects and must be re-authored, not blindly committed).

---

## 1. Ground truth (verified 2026-10-19)

| Fact | Value | Where verified |
|---|---|---|
| Repo HEAD | `9cd8e25` `release: flockwork 0.3.0 + opencode-flockwork npm cut` (Oct 14) | `git log` |
| Version in committed `pyproject.toml` | `0.3.0` | `git show HEAD:pyproject.toml` |
| Version **on PyPI today** | **0.1.0, 0.2.0** (0.2.0 = latest). **0.3.0 NOT published yet** | `pip index versions flockwork` |
| `dist/` artifacts | `flockwork-0.3.0-py3-none-any.whl` + `.tar.gz` built **Oct 11** (stale → predate the dirty draft) | `ls -la dist/` |
| npm `opencode-flockwork` | **not published** (`E404`) | `npm view opencode-flockwork` |
| Entry point | `flockwork = l2.inrepo:main` | `pyproject.toml [project.scripts]` |
| Package layout | `l2/` present, has `__init__.py`; imports are **all stdlib** | import scan of `l2/*.py` |
| Build backend | `setuptools>=45 + wheel` builds cleanly in a dry run | `python3 -m build` (works) |
| twine | v7.0.0 available | `python3 -m twine --version` |
| `.pypirc` | project + `~/.pypirc` present (now gitignored) | `ls` |
| Prior publish history | AGENTS.md notes an earlier publish attempt hit **403 (token scope)** | `flockwork/AGENTS.md` |

---

## 2. Objectives

1. **Truthful metadata** — declare only real dependencies, honest `requires-python` floor
   (`>=3.10`), and an author **name only** — the placeholder author email is *dropped*
   (no `jlam@example.com`, no email field at all). No phantom deps.
2. **Reproducible, verified build** — `SOURCE_DATE_EPOCH`-pinned `python -m build` twice
   → byte-identical **wheels**; `twine check --strict` green; install from the built
   wheel in a clean venv and run the CLI.
3. **Atomic, reversible git history** — reviewed/verified work lands as a small number of
   cleanly-scoped commits, each independently reviewable; the final "publish" is a
   separate, owner-gated step (never bundled with a source change).
4. **Credential safety** — the real PyPI token stays out of the repo (`.pypirc` already
   gitignored); token resolved from env at publish time only.

---

## 3. Defects in the current uncommitted draft (must NOT be shipped as-is)

The worktree holds a half-finished packaging rework (`git status`): modified
`pyproject.toml`, `.gitignore`; new `setup.cfg`, `setup.py`, `PYPI_SETUP.md`. Review
found these problems:

1. **Phantom dependencies.** Draft `[project] dependencies = click, pydantic, rich` —
   but **zero** of them are imported anywhere in `l2/` (all imports stdlib). Shipping
   them bloats installs and is dishonest metadata.
2. **Placeholder author email** `jlam@example.com` in all three files — `example.com` is
   a reserved TLD; would ship a fake identity onto PyPI. **Resolution: drop the email
   field entirely** (`authors = [{name = "J Lam"}]` — PyPI allows author name with no
   email).
3. **`requires-python` downgraded to `>=3.8`** while `l2/` uses `from __future__ import
   annotations`, `list[str]`, `dict[str, ...]`, and `X | None` union syntax. Under
   `from __future__ import annotations` annotations are lazy strings, but claiming a
   floor below what the code was authored/CI'd for (committed HEAD said `>=3.10`) is
   risky and unproven. Keep `>=3.10` unless we actually CI 3.8/3.9.
4. **Triple-source packaging duplication** (`pyproject.toml` + `setup.cfg` + `setup.py`)
   — three files must agree; guarantees drift. `setup.py` is legacy-only. The pyproject
   is sufficient; carrying the other two is optional and must be *consistent or absent*.
5. **Deprecated license classifier** + double license declaration (SPDX text + legacy
   classifier) triggers a build warning.
6. Scope creep: `PYPI_SETUP.md` documents env-token + keyring — fine as docs, but it must
   not silently promise a publish capability the token scope doesn't grant (AGENTS.md
   records a real 403).

**Decision:** re-author the packaging in `pyproject.toml` only (drop `setup.cfg`/
`setup.py` or keep them strictly derived), fix all metadata to be truthful, re-verify,
and only then commit. Do not commit the draft as found.

---

## 4. Scope of work

### In scope
- Rewrite `pyproject.toml` packaging metadata: truthful `dependencies` (empty list, since
  none are required), `requires-python` `>=3.10` (unchanged from HEAD), real author name
  with no email,
  single SPDX license expression, `[tool.setuptools.packages.find]` for `l2*`.
- Resolve the triple-packaging question: **prefer pyproject-only**; remove `setup.cfg`/
  `setup.py` if they add nothing, else reconcile them to the single source of truth.
- Keep or refine `PYPI_SETUP.md` and `.gitignore` (the `.pypirc` ignore is correct).
- Rebuild `dist/` (Oct 11 artifacts are stale), `twine check`, clean-venv install + CLI smoke.
- Commit atomically.

### Out of scope (this plan)
- **npm publish** of `opencode-flockwork` (separate owner-gated step; npm auth is
  independent of PyPI). Staged for a follow-up, noted in §9.
- Publishing a version other than `0.3.0`.
- Changing `l2/` runtime code (no behavior changes — release only).

---

## 5. Run-duration budget & checkpointing

All steps are **seconds to low-minutes**. No step approaches a "long run"; the only
non-trivial wall-clock items are the clean-venv installs and the final `twine upload`.

| Step | Est. wall-clock | Checkpoint needed? |
|---|---|---|
| Metadata re-author + edit | <1 min | No (edit tool) |
| `python -m build` (x2 for byte-repro) | ~5–15 s each | No |
| `twine check dist/*` | ~2 s | No |
| Clean-venv install + `flockwork --help` + `import l2.inrepo` + a scratch-board round-trip | 30 s–2 min | **Yes — T3/T4 gate** (see §6) |
| `twine upload` (final publish) | ~10–30 s | No (one-shot, idempotent: re-upload of same version is rejected, which is *safe*) |
| Optional npm publish + deprecate | 10–30 s | No |

**Checkpointing policy:** because every step is short, we do **not** add a bespoke
checkpoint/resume layer to the packaging itself. Instead the "checkpoint" IS the git
history: each atomic commit (§7) is a restart point, and `dist/` + `~/.pypirc` are
re-materializable/ignored (not source). If any gate (§6) fails, we stop at the last green
commit, fix, and re-run only from that step — nothing is re-done from scratch, and no
partial state is ever committed. For auditability the publish log is written to
`RELEASE-0.3.0-RUN.md` (or appended to the commit body) so a crash mid-publish leaves a
readable record rather than an ambiguous tree.

---

## 6. Verification gates (each blocks the next)

- **G1 — Offline build green:** `python3 -m build` succeeds (sdist + wheel); `twine check dist/*` returns 0.
- **G2 — Byte-reproducible (oracle-corrected):** build twice for the same source with
  `SOURCE_DATE_EPOCH` pinned; `sha256sum`/`cmp` of the produced **wheel** must match
  (wheels otherwise differ only in the 6 dist-info zip-entry mtimes; sdist timestamps
  can vary — documented drift). Without the pinned epoch, two wheel builds differ.
- **G3 — Clean-install smoke (T1/T3/T4; oracle-corrected — no `--help`):**
  - `python3 -m venv /tmp/fw-venv && /tmp/fw-venv/bin/pip install ./dist/flockwork-0.3.0-*.whl`
  - `/tmp/fw-venv/bin/python -c "import l2.inrepo"` imports clean with **no** missing-dep
    error (this is the proof the empty dependency list is honest). [`flockwork --help` is
    **not** a valid verb — it exits rc 2 — so it is not used as a smoke test.]
  - A real scratch-board round-trip via the installed CLI (claim → return → verdict) on a
    temp origin, mirroring the repo's T4 tier, confirms the packaged binary is the live CLI.
- **G4 — Metadata audit:** `twine check` plus a manual audit of the built `PKG-INFO` /
  wheel metadata asserting: deps == `[]`, requires-python == `>=3.10`, author has no
  email field at all (no `example.com`), license is a valid SPDX id.
- **G5 — Lint/CI parity:** repo pre-commit hooks + the existing CI build job pass on the
  new metadata (catches the deprecated-classifier warning being treated as error).

**Stop condition:** a gate failure halts the plan at the last green commit. Never bypass
a gate with a force flag.

---

## 7. Atomic commit plan

Land as four small, independently-reviewable commits, each leaving the tree fully green
(G1–G5 pass at every step). Follow the repo's existing conventional-commit style.

| Commit | Message | Contents |
|---|---|---|
| **A** | `chore(pkg): make pyproject.toml the single packaging source of truth` | Rewrite `pyproject.toml` (truthful deps, `>=3.10`, author name with **no email**, SPDX license, package-find for `l2*`). Remove `setup.cfg`/`setup.py` (or reconcile if kept) so there is one source of truth. |
| **B** | `docs(pkg): document PyPI setup incl. secure token handling` | Finalize `PYPI_SETUP.md` (env-token + keyring + `.pypirc` gitignore rationale), remove any claim the token scope can't support. |
| **C** | `release(0.3.0): rebuild + verify dist artifacts` | (If repo tracks `dist/`) refreshed sdist/wheel + receipts (build log, `twine check`, clean-venv smoke). If `dist/` is gitignored, **omit** — do not commit build products. |
| **D** | `chore(ci): keep packaging drift-guard from regressing` | (Optional, recommended) a tiny CI check asserting `requires-python`, empty deps, and no `example.com` in pyproject — so truthfulness can't silently regress. |

Each commit = one logical concern, fully green. No mega-commit mixing metadata + docs +
build + CI.

---

## 8. Release flow (post-commit, owner-gated)

1. Confirm HEAD == last commit (D) and `git status` clean.
2. `python3 -m build` fresh `dist/`.
3. Run G1–G4 again on the tagged tree (tags move only after green).
4. **Owner countersign** to publish (per project ADR-0002 convention, publish is
   owner-gated — a coding agent does not silently upload). Record the countersign in the
   publish log.
5. `TWINE_*`/`~/.pypirc` token from **env only** → `twine upload dist/*` (version
   `0.3.0`; a repeat upload is idempotent-safe — same version is rejected).
6. Tag `v0.3.0` on the published commit; push tag + branch to github/gitlab origin.
7. Verify: `pip install flockwork==0.3.0` in a fresh venv + `flockwork --help`.

---

## 9. Follow-ups (not part of this plan's core exec)

- **npm `opencode-flockwork`** publish + deprecate the old `flockwork-opencode` name (npm
  auth/OTP is independent; separate owner-gated step, noted here so it isn't lost).
- **Optional:** CI matrix for `3.8/3.9` only if `requires-python >=3.8` is actually
  desired — otherwise keep `>=3.10` and don't claim coverage we don't test.
- Investigate the **historical 403 (token scope)** so the publish credential is confirmed
  to have `upload` permission *before* the owner countersigns (fail earlier than upload).

---

## 10. Risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Shipping phantom deps (click/pydantic/rich) | Med | §3 fix + G3/G4 assert empty dep list |
| R2 | Fake author email on PyPI | Med | **Drop the email field** (name only); G4 audit |
| R3 | `requires-python` floor claim mismatch | Med | Keep `>=3.10` (matches CI); don't claim 3.8 |
| R4 | Triple-file metadata drift | Low-Med | Single source of truth (pyproject-only) |
| R5 | Publishing to PyPI without upload scoped token | High | §9 investigate 403 + owner countersign before §8.5 |
| R6 | Accidental publish from a dirty tree | High | §8.1 clean-status gate + owner countersign |
| R7 | Deprecated license classifier fails CI | Low | G5 catches; use SPDX expression |

---

## 11. Definition of done

- [ ] `pyproject.toml` is the single packaging source of truth with truthful metadata.
- [ ] No `example.com`, no phantom deps anywhere in shipped metadata.
- [ ] G1–G5 all green on the final tree (build, byte-repro wheel, clean-venv CLI+scratch
  round-trip, metadata audit, CI/lint parity).
- [ ] Four atomic commits (A–D) landed, each green; `git status` clean.
- [ ] `flockwork 0.3.0` published to PyPI on the tagged, owner-countersigned tree.
- [ ] `pip install flockwork==0.3.0` + `flockwork --help` verified in a fresh venv.
- [ ] npm follow-up (§9) tracked, not silently dropped.
