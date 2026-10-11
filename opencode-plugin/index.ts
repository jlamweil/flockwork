/**
 * flockwork — opencode plugin (thin glue).
 *
 * opencode loads this file and expects it to export EXACTLY ONE Plugin
 * function (the zft-gate convention). All the logic lives in
 * `../lib/flockwork.ts` (kept out of this directory so opencode does not try
 * to load it as a plugin); this only wires the single `Flockwork` function
 * to `makeHooks`, with the idempotency guard + a safe logger.
 *
 * MARKER-ONLY activation + FAIL-SAFE: see `../lib/flockwork.ts`.
 */
import type { Plugin } from "@opencode-ai/plugin";
import { makeHooks, active, TAG_FW } from "./lib/flockwork";

export const Flockwork: Plugin = async ({ directory, client }) => {
  // Idempotency: opencode can load the repo-local copy and a global copy in
  // the same process; only the first registers hooks.
  const g = globalThis as Record<string, unknown>;
  if (g.__flockworkRegistered) return {};
  g.__flockworkRegistered = true;

  const debug = (msg: string): void => {
    try {
      void (client?.app?.log?.({ level: "debug", message: `${TAG_FW} ${msg}` }));
    } catch {
      // logging must never break a turn
    }
  };

  debug(`plugin loaded; active=${active(directory)}`);
  return makeHooks(directory, debug);
};
