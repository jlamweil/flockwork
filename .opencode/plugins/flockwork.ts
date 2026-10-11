/**
 * flockwork — turn a habitual opencode session into a transparent
 * swarm-substrate run (design C, hybrid).
 *
 * The plugin is THIN GLUE. The policy lives in the substrate (l2/harness.py
 * + the l2/inrepo.py legs); the plugin only shells out to the `flockwork`
 * CLI. Two invariants (the oracle's corrections):
 *
 *   MARKER-ONLY activation — a local .flockwork/origin marker is the sole
 *   on-switch. A stray SWARM_ORIGIN env var must NEVER activate the plugin
 *   (the env-trigger was the production-push hazard class). When active, the
 *   marker's origin is passed to the CLI on the spawn env — not read from a
 *   global default.
 *
 *   FAIL-SAFE — any internal error (missing CLI, spawn failure, timeout,
 *   unexpected exit) degrades to a no-op + a stderr warning. A hook exception
 *   never escapes except a deliberate policy block.
 *
 * Scope: silent unless the workspace declares a .flockwork/origin marker.
 */
import type { Plugin } from "@opencode-ai/plugin";
import { spawnSync, type SpawnSyncReturns } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const TAG = "[flockwork]";

/** Upper bound on a single CLI invocation. `fw_judge` (the oracle leg) needs
 *  a generous budget; the matched plugin spawn timeout is set from this so a
 *  slow-but-legitimate oracle is not killed by a too-tight harness cap. */
const FW_JUDGE_TIMEOUT_MS = 600_000;
const FW_DEFAULT_TIMEOUT_MS = 60_000;

export const TAG_FW = TAG;

/** The local marker path for a workspace (the sole activation source). */
export const markerPath = (directory: string): string =>
  join(directory, ".flockwork", "origin");

/** Read the origin from the local marker, or null. NEVER consults the env —
 *  the env is not a legal activation source (see the module docstring). */
export const originOf = (directory: string): string | null => {
  try {
    const p = markerPath(directory);
    if (!existsSync(p)) return null;
    const v = readFileSync(p, "utf8").trim();
    return v ? v : null;
  } catch {
    return null;
  }
};

/** Marker-only workspace-detect. */
export const active = (directory: string): boolean => originOf(directory) !== null;

/** Resolve the flockwork CLI prefix: FLOCKWORK_BIN (a full path) or `flockwork`
 *  on PATH. Returns an argv prefix (the command + its fixed args). */
export const resolveFw = (): string[] => {
  const bin = process.env.FLOCKWORK_BIN;
  return bin ? [bin] : ["flockwork"];
};

export const warn = (msg: string): void => {
  try {
    process.stderr.write(`${TAG} ${msg}\n`);
  } catch {
    // stderr failing is not actionable; stay silent.
  }
};

/** The fail-safe shell-out to the substrate CLI. Returns the spawnSync result
 *  (status/stdout/stderr) on success, or undefined (after warning) if the CLI
 *  could not run. The marker's origin is passed on the spawn env so the CLI's
 *  origin resolution lands on the declared board — never a global default. */
export const runFw = (
  directory: string,
  argv: string[],
  timeoutMs = FW_DEFAULT_TIMEOUT_MS,
): SpawnSyncReturns<Buffer> | undefined => {
  const prefix = resolveFw();
  const origin = originOf(directory);
  try {
    const env: Record<string, string> = { ...process.env };
    if (origin) env.SWARM_ORIGIN = origin;
    const res = spawnSync(prefix[0], [...prefix.slice(1), ...argv], {
      cwd: directory,
      encoding: "utf8",
      timeout: timeoutMs,
      env,
    });
    if (res.error) {
      // ENOENT (missing CLI) or ETIMEDOUT (oracle budget exceeded): the leg
      // could not run. Fail safe — a clean undefined the caller no-ops on.
      warn(`CLI could not run (${res.error.code ?? res.error.message}) — failing safe (no-op)`);
      return undefined;
    }
    return res;
  } catch (e) {
    warn(`internal error: ${e instanceof Error ? e.message : String(e)} — failing safe (no-op)`);
    return undefined;
  }
};

export const Flockwork: Plugin = async ({ directory, client }) => {
  // Idempotency: opencode can load the repo-local copy and a global copy in
  // the same process; only the first registers hooks.
  const g = globalThis as Record<string, unknown>;
  if (g.__flockworkRegistered) return {};
  g.__flockworkRegistered = true;

  const debug = (msg: string): void => {
    try {
      void (client?.app?.log?.({ level: "debug", message: `${TAG} ${msg}` }));
    } catch {
      // logging must never break a turn
    }
  };

  debug(`plugin loaded; active=${active(directory)}`);

  // C8 skeleton: no hooks registered yet. C9 (tools), C10 (protocol), and
  // C11 (idle backstop) attach their hooks here.
  return {};
};

export default Flockwork;
