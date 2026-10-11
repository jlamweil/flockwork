/**
 * flockwork — swarm-substrate core (the opencode plugin's logic).
 *
 * Kept OUTSIDE the `.opencode/plugins/` directory so opencode does not try to
 * load it as a plugin (a plugin file must export exactly one Plugin function;
 * this module exports many helpers for the contract tests). The thin plugin
 * (`.opencode/plugins/flockwork.ts`) imports `makeHooks` + exposes only the
 * single `Flockwork` function opencode loads.
 *
 * The plugin is THIN GLUE. Policy lives in the substrate (`l2/harness.py` +
 * the `l2/inrepo.py` legs); this only shells out to the `flockwork` CLI.
 *
 *   MARKER-ONLY activation — a local `.flockwork/origin` marker is the sole
 *   on-switch. A stray `SWARM_ORIGIN` env var must NEVER activate (the
 *   env-trigger was the production-push hazard class). When active, the
 *   marker's origin is passed to the CLI on the spawn env — never a default.
 *
 *   FAIL-SAFE — any internal error (missing CLI, spawn failure, timeout,
 *   unexpected exit) degrades to a no-op + a stderr warning. A hook exception
 *   never escapes except a deliberate policy block.
 */
import { tool } from "@opencode-ai/plugin";
import { z } from "zod";
import { spawnSync, type SpawnSyncReturns } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const TAG = "[flockwork]";

/** Per-session model ID captured from the chat.message hook, so the substrate
 *  can report the model/harness that actually did the work (CORR-9). */
const sessionModels = new Map<string, string>();
export const captureModel = (sessionID: string, modelID: string | undefined): void => {
  if (sessionID && modelID) sessionModels.set(sessionID, modelID);
};
export const modelFor = (sessionID: string): string => sessionModels.get(sessionID) ?? "";

// ------------------------------------------------------------- idle backstop
//
// C11-lite. Keeps a live session's claims alive + legible WITHOUT ever writing
// a terminal verdict (that stays the TTL+heir's job). Two triggers:
//   - chat.message (a turn starts): the session is active -> renew its claims;
//   - session.idle (a turn ends): renew now + schedule a DELAYED abandonment
//     check. If the session is still quiet after the threshold, it is
//     abandoned -> stamp the non-terminal `stalled:` marker (rescue). A
//     resumed session (a new chat.message) cancels the pending check.
//
// The policy lives in the substrate (`flockwork harness backstop`); this is
// only the trigger + the quiet-since-activity decision.

const lastActivity = new Map<string, number>();
const pendingStall = new Map<string, ReturnType<typeof setTimeout>>();
let abandonThresholdMs = 3_600_000; // a human-paced session idles for hours

export const shouldStall = (
  lastActivityMs: number | undefined,
  nowMs: number,
  thresholdMs: number,
): boolean => (lastActivityMs != null && nowMs - lastActivityMs > thresholdMs);

export const _setAbandonThresholdForTest = (ms: number): void => {
  abandonThresholdMs = ms;
};
export const _setLastActivityForTest = (sessionID: string, ms: number): void => {
  lastActivity.set(sessionID, ms);
};

/** Mark a session as having just been active (turn start / tool use); cancels
 *  any pending abandonment check for it. */
export const touchSession = (sessionID: string): void => {
  if (!sessionID) return;
  lastActivity.set(sessionID, Date.now());
  const t = pendingStall.get(sessionID);
  if (t) {
    clearTimeout(t);
    pendingStall.delete(sessionID);
  }
};

/** Fire the substrate backstop for a session (idle=renew, or abandoned). The
 *  origin comes from the local marker; a missing marker/CLI is a no-op. */
export const fireBackstop = (directory: string, sessionID: string, abandoned: boolean): void => {
  if (!active(directory)) return;
  const origin = originOf(directory)!;
  runFw(directory, [
    "harness", "backstop", origin, sessionID,
    ...(abandoned ? ["abandoned"] : []),
  ]);
};

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
  extraEnv?: Record<string, string>,
): SpawnSyncReturns<Buffer> | undefined => {
  const prefix = resolveFw();
  const origin = originOf(directory);
  try {
    const env: Record<string, string> = { ...process.env, ...extraEnv };
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

// ------------------------------------------------------- protocol injection
//
// The canonical 2-step protocol lives in the substrate (l2/harness.py) as the
// single source of truth. We fetch it from the CLI (so the injected text can
// never drift from the skill/docs) and cache it (it is static +
// system.transform fires on every message). If the CLI cannot run we fall back
// to a minimal embedded note (we do NOT cache the fallback, so a later
// successful fetch overrides it).

export const FALLBACK_PROTOCOL =
  "flockwork board detected but the protocol could not be loaded from the " +
  "substrate; follow the 2-step loop: fw_brief -> fw_claim, then work -> " +
  "fw_judge -> fw_land -> fw_verdict -> fw_work_done. A failing oracle is an " +
  "honest fixed=false, not an error; never force-push.";

let protocolCache: string | null = null;
export const _resetProtocolCache = (): void => {
  protocolCache = null;
};

export const getProtocol = (directory: string): string | null => {
  if (protocolCache) return protocolCache;
  const res = runFw(directory, ["harness", "protocol"], FW_DEFAULT_TIMEOUT_MS);
  if (res && res.status === 0 && (res.stdout ?? "").trim()) {
    protocolCache = res.stdout!.trim();
    return protocolCache;
  }
  return FALLBACK_PROTOCOL;
};

// ---------------------------------------------------------- the fw_* tools
//
// Each is a thin shell-out to a substrate CLI verb (the policy lives in
// Python). The shared shape: refuse typed when the workspace is not a
// flockwork board or the CLI cannot run (fail-safe no-op); on success, record
// the session->claim mapping + the run ledger so the idle backstop (C11) and
// `flockwork debug` can see what this session did.

/** The model/harness env for a spawn (CORR-9): the substrate reports the model
 *  + harness that actually did the work, so a verdict is replayable from the
 *  refs alone (never "model: unknown"). */
const fwEnv = (sessionID: string): Record<string, string> => {
  const env: Record<string, string> = { SWARM_HOST: "opencode" };
  const model = modelFor(sessionID);
  if (model) env.SWARM_MODEL = model;
  return env;
};

const notWorkspace = {
  output: "not a flockwork workspace (no .flockwork/origin marker) — nothing done",
  metadata: { ok: false, reason: "not-a-workspace" },
};
const cliDown = {
  output: "flockwork CLI could not run — failing safe (no-op)",
  metadata: { ok: false, reason: "cli-unavailable" },
};

type ToolCtx = { sessionID: string; directory: string };

/** Parse the CLI's JSON stdout into an object ({} on a non-JSON body). */
const parseJson = (s: string | null | undefined): Record<string, unknown> => {
  try {
    const t = (s ?? "").trim();
    return t ? (JSON.parse(t) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
};

/** Record a live claim in the durable session map + run ledger. */
const recordClaim = (ctx: ToolCtx, origin: string, task: string, att: string, leg: string): void => {
  runFw(ctx.directory, ["harness", "session-put", origin, ctx.sessionID, task, att]);
  runFw(ctx.directory, [
    "harness", "ledger-run", origin,
    JSON.stringify({ session: ctx.sessionID, task, att, leg }),
  ]);
};

/** Settle a claim (drop it from the session map + log the terminal leg). */
const settleClaim = (ctx: ToolCtx, origin: string, task: string, leg: string, extra: Record<string, unknown> = {}): void => {
  runFw(ctx.directory, ["harness", "session-drop", origin, ctx.sessionID, task]);
  runFw(ctx.directory, [
    "harness", "ledger-run", origin,
    JSON.stringify({ session: ctx.sessionID, task, leg, ...extra }),
  ]);
};

export const fwTools = {
  fw_brief: tool({
    description:
      "Read the brief (spec) for a task on the swarm board: what to do + the " +
      "verify command that will judge the work. Call this before claiming.",
    args: { task: z.string() },
    execute: async (args: { task: string }, ctx: ToolCtx) => {
      if (!active(ctx.directory)) return notWorkspace;
      const origin = originOf(ctx.directory)!;
      const res = runFw(ctx.directory, ["brief", args.task], FW_DEFAULT_TIMEOUT_MS, fwEnv(ctx.sessionID));
      if (!res) return cliDown;
      if (res.status !== 0) {
        return { output: `brief unavailable (rc ${res.status}): ${(res.stderr ?? res.stdout ?? "").trim()}`, metadata: { ok: false } };
      }
      return { output: res.stdout?.trim() ?? "", metadata: { ok: true } };
    },
  }),

  fw_claim: tool({
    description:
      "Claim a task on the swarm board (takes a lease on it). Returns the " +
      "attestation token (att) that identifies THIS work attempt; pass it to " +
      "fw_judge / fw_land / fw_verdict. If the claim is lost (already claimed " +
      "or rejected), it reports a typed refusal.",
    args: { task: z.string() },
    execute: async (args: { task: string }, ctx: ToolCtx) => {
      if (!active(ctx.directory)) return notWorkspace;
      const origin = originOf(ctx.directory)!;
      const res = runFw(ctx.directory, ["claim", args.task], FW_DEFAULT_TIMEOUT_MS, fwEnv(ctx.sessionID));
      if (!res) return cliDown;
      const parsed = parseJson(res.stdout);
      const att = typeof parsed.att === "string" ? parsed.att : "";
      if (res.status !== 0 || !att) {
        return {
          output: `claim failed (rc ${res.status}): ${((res.stderr ?? res.stdout ?? "").trim()) || "lost the race or rejected"}`,
          metadata: { ok: false },
        };
      }
      recordClaim(ctx, origin, args.task, att, "claim");
      return { output: `claimed ${args.task}\natt=${att}\n${res.stdout?.trim() ?? ""}`, metadata: { ok: true, att } };
    },
  }),

  fw_work_started: tool({
    description:
      "Checkpoint that work on a claimed task has actually begun (updates the " +
      "claim so a crash is legible + keeps attribution honest).",
    args: { task: z.string(), att: z.string() },
    execute: async (args: { task: string; att: string }, ctx: ToolCtx) => {
      if (!active(ctx.directory)) return notWorkspace;
      const origin = originOf(ctx.directory)!;
      const res = runFw(ctx.directory, ["work-started", args.task, args.att], FW_DEFAULT_TIMEOUT_MS, fwEnv(ctx.sessionID));
      if (!res) return cliDown;
      const okd = res.status === 0;
      recordClaim(ctx, origin, args.task, args.att, "work_started");
      return { output: okd ? `work_started ${args.task}` : `work-started not recorded (rc ${res.status})`, metadata: { ok: okd } };
    },
  }),

  fw_judge: tool({
    description:
      "Run the task's verify command (the oracle) against your working tree. " +
      "Returns whether the oracle passed (rc 0) + its detail. THIS MAY BLOCK " +
      "FOR MINUTES (it runs the full verify suite) — it is the merit leg: a " +
      "failing oracle is an honest result, not an error.",
    args: { task: z.string(), tree: z.string(), verify: z.string().optional() },
    execute: async (args: { task: string; tree: string; verify?: string }, ctx: ToolCtx) => {
      if (!active(ctx.directory)) return notWorkspace;
      const origin = originOf(ctx.directory)!;
      const argv = args.verify
        ? ["judge", args.task, args.tree, args.verify]
        : ["judge", args.task, args.tree];
      const res = runFw(ctx.directory, argv, FW_JUDGE_TIMEOUT_MS, fwEnv(ctx.sessionID));
      if (!res) return cliDown;
      const parsed = parseJson(res.stdout);
      const passed = res.status === 0;
      const detail = typeof parsed.detail === "string" ? parsed.detail : (res.stderr ?? res.stdout ?? "").trim();
      return {
        output: `oracle ${passed ? "PASSED" : "FAILED"} (rc ${res.status})\n${detail}`.trim(),
        metadata: { ok: true, passed, rc: res.status },
      };
    },
  }),

  fw_land: tool({
    description:
      "Land the working-tree fix: commit the scoped changeset, push it to the " +
      "board main (with one bounded rebase retry), and push the H1 return ref. " +
      "Pass ONLY the files you changed (paths); if omitted the whole tree is " +
      "used. A rejected push (behind main) is reported, not forced.",
    args: { task: z.string(), att: z.string(), tree: z.string(), paths: z.array(z.string()).optional() },
    execute: async (args: { task: string; att: string; tree: string; paths?: string[] }, ctx: ToolCtx) => {
      if (!active(ctx.directory)) return notWorkspace;
      const origin = originOf(ctx.directory)!;
      const argv = ["land", args.task, args.att, args.tree, ...(args.paths ?? [])];
      const res = runFw(ctx.directory, argv, FW_DEFAULT_TIMEOUT_MS, fwEnv(ctx.sessionID));
      if (!res) return cliDown;
      const parsed = parseJson(res.stdout);
      const okd = res.status === 0;
      return {
        output: `land ${okd ? "succeeded" : "failed"} (rc ${res.status})\n${res.stdout?.trim() ?? (res.stderr ?? "")}`.trim(),
        metadata: { ok: okd, ...(typeof parsed.main_pushed === "boolean" ? { main_pushed: parsed.main_pushed } : {}), ...(typeof parsed.return_pushed === "boolean" ? { return_pushed: parsed.return_pushed } : {}) },
      };
    },
  }),

  fw_verdict: tool({
    description:
      "Write the terminal, create-once verdict for the claimed task (fixed true/" +
      "false + the harness/oracle rc + elapsed). Call this exactly once, after " +
      "fw_land, to settle the task. This is the ONLY tool that may write a " +
      "verdict from an interactive session.",
    args: { task: z.string(), att: z.string(), fixed: z.boolean(), oc_rc: z.number(), pytest_rc: z.number(), oc_elapsed: z.number().optional() },
    execute: async (args: { task: string; att: string; fixed: boolean; oc_rc: number; pytest_rc: number; oc_elapsed?: number }, ctx: ToolCtx) => {
      if (!active(ctx.directory)) return notWorkspace;
      const origin = originOf(ctx.directory)!;
      const argv = ["verdict", args.task, args.att, args.fixed ? "true" : "false", String(args.oc_rc), String(args.pytest_rc)];
      if (args.oc_elapsed != null) argv.push(String(args.oc_elapsed));
      const res = runFw(ctx.directory, argv, FW_DEFAULT_TIMEOUT_MS, fwEnv(ctx.sessionID));
      if (!res) return cliDown;
      const parsed = parseJson(res.stdout);
      const okd = res.status === 0 && parsed.verdict_pushed === true;
      if (okd) settleClaim(ctx, origin, args.task, "verdict", { fixed: args.fixed });
      return {
        output: okd ? `verdict written: ${args.task} fixed=${args.fixed}` : `verdict NOT written (rc ${res.status}): ${((res.stderr ?? res.stdout ?? "").trim()) || "create-once refusal (a verdict already exists)"}`,
        metadata: { ok: okd },
      };
    },
  }),

  fw_renew: tool({
    description:
      "Renew the lease on a claimed task (fresh timestamp, same att) so a " +
      "long-running interactive session is not reaped as stale. Call this " +
      "periodically during long work.",
    args: { task: z.string(), att: z.string() },
    execute: async (args: { task: string; att: string }, ctx: ToolCtx) => {
      if (!active(ctx.directory)) return notWorkspace;
      const origin = originOf(ctx.directory)!;
      const res = runFw(ctx.directory, ["renew", args.task, args.att], FW_DEFAULT_TIMEOUT_MS, fwEnv(ctx.sessionID));
      if (!res) return cliDown;
      const okd = res.status === 0;
      runFw(ctx.directory, ["harness", "ledger-gate", origin, JSON.stringify({ session: ctx.sessionID, task: args.task, decision: okd ? "renewed" : "renew-failed" })]);
      return { output: okd ? `renewed ${args.task}` : `renew failed (rc ${res.status})`, metadata: { ok: okd } };
    },
  }),

  fw_work_done: tool({
    description:
      "Checkpoint that work on a claimed task is complete (settles the " +
      "session's live-claim tracking so the idle backstop stops renewing it).",
    args: { task: z.string(), att: z.string() },
    execute: async (args: { task: string; att: string }, ctx: ToolCtx) => {
      if (!active(ctx.directory)) return notWorkspace;
      const origin = originOf(ctx.directory)!;
      const res = runFw(ctx.directory, ["work-done", args.task, args.att], FW_DEFAULT_TIMEOUT_MS, fwEnv(ctx.sessionID));
      if (!res) return cliDown;
      const okd = res.status === 0;
      settleClaim(ctx, origin, args.task, "work_done");
      return { output: okd ? `work_done ${args.task}` : `work-done not recorded (rc ${res.status})`, metadata: { ok: okd } };
    },
  }),
};

/** Build the opencode Hooks for an active workspace. `directory` is the
 *  workspace root (PluginInput.directory); `debug` is the safe logger. */
export const makeHooks = (directory: string, debug: (msg: string) => void) => {
  return {
    tool: fwTools,
    // C11: a turn starts -> the session is active. Renew its live claims
    // (the per-turn lease renewal so a long human-paced task is not reaped)
    // + cancel any pending abandonment check + capture the model.
    "chat.message": async (input: { sessionID: string; model?: { modelID?: string } }) => {
      try {
        captureModel(input.sessionID, input.model?.modelID);
        touchSession(input.sessionID);
        fireBackstop(directory, input.sessionID, false);
      } catch (e) {
        warn(`chat.message backstop error: ${e instanceof Error ? e.message : String(e)} — continuing`);
      }
    },
    // C11: a turn ends -> renew now (keeps the claim alive; NO terminal
    // verdict) + schedule a delayed abandonment check. If the session is
    // still quiet after the threshold, stamp the non-terminal `stalled:`
    // marker (rescue). A resumed session cancels it (see chat.message).
    event: async (input: { event: { type: string; properties: { sessionID: string } } }) => {
      try {
        if (input.event.type !== "session.idle") return;
        const sid = input.event.properties.sessionID;
        fireBackstop(directory, sid, false); // renew on the turn boundary
        if (lastActivity.has(sid)) {
          const t = setTimeout(() => {
            pendingStall.delete(sid);
            if (shouldStall(lastActivity.get(sid), Date.now(), abandonThresholdMs)) {
              fireBackstop(directory, sid, true); // abandoned -> rescue
            }
          }, abandonThresholdMs);
          pendingStall.set(sid, t);
        }
      } catch (e) {
        warn(`idle backstop error: ${e instanceof Error ? e.message : String(e)} — continuing`);
      }
    },
    // C10: append the canonical protocol to the agent's system prompt, but
    // ONLY in an active flockwork workspace (silent everywhere else).
    // Live-verified (2026-10-11): opencode reflects MUTATION of output.system,
    // not re-assignment — `output.system = [...output.system, proto]` silently
    // drops the protocol from the prompt; `.push()` is the only form that lands.
    "experimental.chat.system.transform": async (_input: unknown, output: { system: string[] }) => {
      try {
        if (!active(directory)) return;
        const proto = getProtocol(directory);
        if (proto) output.system.push(proto);
      } catch (e) {
        warn(`system.transform error: ${e instanceof Error ? e.message : String(e)} — skipping injection`);
      }
    },
  };
};
