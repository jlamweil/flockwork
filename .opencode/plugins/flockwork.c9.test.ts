import { describe, expect, test, beforeEach, mock } from "bun:test";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// The plugin imports @opencode-ai/plugin (the `tool` helper, which is an
// identity at runtime) and zod (arg shapes, unused when we call execute
// directly). Stub both so the contract test is hermetic and needs no network /
// opencode node_modules. Registered BEFORE the dynamic import below.
const zstub: unknown = new Proxy(function () { /* shape */ } as never, {
  get: (_t, prop) => (prop === "then" ? undefined : zstub),
  apply: () => zstub,
});
mock.module("@opencode-ai/plugin", () => ({ tool: (x: unknown) => x }));
mock.module("zod", () => ({ z: zstub, string: zstub, default: zstub }));

const { Flockwork } = await import("./flockwork.ts");
const { fwTools, captureModel } = await import("../lib/flockwork.ts");

const mktmp = (): string => mkdtempSync(join(tmpdir(), "fw-c9-"));
const writeMarker = (d: string, origin: string): void => {
  mkdirSync(join(d, ".flockwork"), { recursive: true });
  writeFileSync(join(d, ".flockwork", "origin"), origin + "\n");
};

// A fake `flockwork` that records every invocation (argv + the harness env it
// received) and answers each verb the way the real CLI would.
const FAKE = `#!/bin/bash
L="$FW_LOG"
printf 'ARGV:%s\\n' "$*" >> "$L"
printf 'MODEL=%s HOST=%s\\n' "$SWARM_MODEL" "$SWARM_HOST" >> "$L"
case "$1" in
  claim)   if [ -n "$FW_LOST_RACE" ]; then echo "lost the race" >&2; exit 1; fi
           echo '{"att": "att-xyz"}'; exit 0;;
  brief)   echo "brief body: fix the bug
verify: pytest -q"; exit 0;;
  judge)   echo '{"rc": 0, "detail": "ok"}'; exit 0;;
  land)    echo '{"main_pushed": true, "return_pushed": true}'; exit 0;;
  verdict) echo '{"verdict_pushed": true}'; exit 0;;
  *)       exit 0;;
esac
`;

let d: string;
let log: string;
let bin: string;

const setup = (): void => {
  d = mktmp();
  writeMarker(d, "git@board.example:repo.git");
  log = join(d, "fw.log");
  bin = join(d, "fake-flockwork");
  writeFileSync(bin, FAKE);
  chmodSync(bin, 0o755);
  process.env.FLOCKWORK_BIN = bin;
  process.env.FW_LOG = log;
  delete process.env.FW_LOST_RACE;
  delete process.env.SWARM_ORIGIN;
};

const ctx = (): { sessionID: string; directory: string } => ({
  sessionID: "sess-1",
  directory: d,
});

const ran = (substring: string): boolean => {
  try {
    return readFileSync(log, "utf8").includes(substring);
  } catch {
    return false; // no shell-out happened -> the log was never created
  }
};

beforeEach(() => {
  setup();
  delete (globalThis as Record<string, unknown>).__flockworkRegistered;
});

describe("fw_claim", () => {
  test("shells to claim, records the live claim + run ledger, returns the att", async () => {
    const res = await fwTools.fw_claim.execute({ task: "T1" }, ctx());
    expect(res.metadata?.ok).toBe(true);
    expect(res.output).toContain("att=att-xyz");
    expect(ran("ARGV:claim T1")).toBe(true);
    // the session map + run ledger were recorded with the session + att
    expect(ran("ARGV:harness session-put git@board.example:repo.git sess-1 T1 att-xyz")).toBe(true);
    expect(ran("harness ledger-run")).toBe(true);
  });

  test("passes the model + harness on the spawn env (replayable verdicts)", async () => {
    captureModel("sess-1", "router/t3/unistra-coder");
    await fwTools.fw_claim.execute({ task: "T1" }, ctx());
    const logged = readFileSync(log, "utf8");
    expect(logged).toContain("MODEL=router/t3/unistra-coder HOST=opencode");
  });

  test("a lost claim is a typed refusal and records NOTHING", async () => {
    process.env.FW_LOST_RACE = "1";
    const res = await fwTools.fw_claim.execute({ task: "T1" }, ctx());
    expect(res.metadata?.ok).toBe(false);
    expect(res.output).toContain("claim failed");
    expect(ran("ARGV:claim T1")).toBe(true);
    expect(ran("session-put")).toBe(false);
    expect(ran("ledger-run")).toBe(false);
  });
});

describe("fw_brief / fw_judge / fw_land / fw_verdict", () => {
  test("fw_brief returns the brief body (brief + verify)", async () => {
    const res = await fwTools.fw_brief.execute({ task: "T1" }, ctx());
    expect(res.metadata?.ok).toBe(true);
    expect(res.output).toContain("verify: pytest -q");
    expect(ran("ARGV:brief T1")).toBe(true);
  });

  test("fw_judge reports the oracle result + records nothing terminal", async () => {
    const res = await fwTools.fw_judge.execute(
      { task: "T1", tree: "/abs/tree" }, ctx());
    expect(res.metadata?.ok).toBe(true);
    expect(res.metadata?.passed).toBe(true);
    expect(ran("ARGV:judge T1 /abs/tree")).toBe(true);
    expect(ran("session-drop")).toBe(false);
  });

  test("fw_land forwards the scoped changeset as separate args", async () => {
    const res = await fwTools.fw_land.execute(
      { task: "T1", att: "att-xyz", tree: "/abs/tree", paths: ["a.py", "b.py"] },
      ctx());
    expect(res.metadata?.ok).toBe(true);
    expect(res.metadata?.main_pushed).toBe(true);
    expect(ran("ARGV:land T1 att-xyz /abs/tree a.py b.py")).toBe(true);
  });

  test("fw_verdict writes the terminal verdict + settles the claim", async () => {
    const res = await fwTools.fw_verdict.execute(
      { task: "T1", att: "att-xyz", fixed: true, oc_rc: 0, pytest_rc: 0, oc_elapsed: 3.5 },
      ctx());
    expect(res.metadata?.ok).toBe(true);
    expect(ran("ARGV:verdict T1 att-xyz true 0 0 3.5")).toBe(true);
    expect(ran("ARGV:harness session-drop git@board.example:repo.git sess-1 T1")).toBe(true);
  });
});

describe("workspace gating", () => {
  test("tools refuse typed in a non-flockwork workspace (and shell out to nothing)", async () => {
    const bare = mktmp(); // no marker
    const res = await fwTools.fw_claim.execute(
      { task: "T1" },
      { sessionID: "sess-x", directory: bare });
    expect(res.metadata?.ok).toBe(false);
    expect(res.output).toContain("not a flockwork workspace");
    expect(ran("ARGV:claim T1")).toBe(false);
  });

  test("the plugin registers the tools + a chat.message hook", async () => {
    const hooks = await Flockwork({ directory: d, client: undefined });
    expect(hooks.tool).toBe(fwTools);
    expect(typeof (hooks as Record<string, unknown>)["chat.message"]).toBe("function");
  });
});
