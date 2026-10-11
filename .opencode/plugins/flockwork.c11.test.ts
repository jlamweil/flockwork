import { describe, expect, test, beforeEach } from "bun:test";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mock } from "bun:test";

const zstub: unknown = new Proxy(function () {} as never, {
  get: (_t, prop) => (prop === "then" ? undefined : zstub),
  apply: () => zstub,
});
mock.module("@opencode-ai/plugin", () => ({ tool: (x: unknown) => x }));
mock.module("zod", () => ({ z: zstub, string: zstub, default: zstub }));

const { Flockwork } = await import("./flockwork.ts");
const {
  shouldStall,
  touchSession,
  fireBackstop,
  _setAbandonThresholdForTest,
  _setLastActivityForTest,
} = await import("../lib/flockwork.ts");

const mktmp = (): string => mkdtempSync(join(tmpdir(), "fw-c11-"));
const writeMarker = (d: string): void => {
  mkdirSync(join(d, ".flockwork"), { recursive: true });
  writeFileSync(join(d, ".flockwork", "origin"), "git@b.example:r.git\n");
};

// fake flockwork that records every invocation (argv + no side effects)
const FAKE = `#!/bin/bash
L="$FW_LOG"
printf 'ARGV:%s\\n' "$*" >> "$L"
exit 0
`;

const wait = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

let d: string;
let log: string;

const setup = (): void => {
  d = mktmp();
  writeMarker(d);
  log = join(d, "fw.log");
  const bin = join(d, "fake-flockwork");
  writeFileSync(bin, FAKE);
  chmodSync(bin, 0o755);
  process.env.FLOCKWORK_BIN = bin;
  process.env.FW_LOG = log;
};

const logText = (): string => {
  try {
    return readFileSync(log, "utf8");
  } catch {
    return "";
  }
};

beforeEach(() => {
  setup();
  delete (globalThis as Record<string, unknown>).__flockworkRegistered;
  _setAbandonThresholdForTest(30);
  fireBackstop(d, "__warmup__", false); // ensure the fake is exercised once
});

describe("shouldStall decision", () => {
  test("no activity yet -> no stall", () => {
    expect(shouldStall(undefined, 1000, 500)).toBe(false);
  });
  test("recent activity -> no stall", () => {
    expect(shouldStall(900, 1000, 500)).toBe(false); // 100ms ago < 500
  });
  test("activity past the threshold -> stall", () => {
    expect(shouldStall(300, 1000, 500)).toBe(true); // 700ms ago > 500
  });
});

describe("backstop triggers", () => {
  test("a turn start (chat.message) renews with NO abandonment", async () => {
    const hooks = await Flockwork({ directory: d, client: undefined });
    const cm = (hooks as Record<string, unknown>)["chat.message"] as
      (i: { sessionID: string; model?: { modelID: string } }) => Promise<void>;
    await cm({ sessionID: "sess-1", model: { modelID: "m" } });
    const t = logText();
    expect(t).toContain("ARGV:harness backstop git@b.example:r.git sess-1");
    expect(t).not.toContain("abandoned");
  });

  test("a turn boundary (session.idle) renews now and renews nothing terminal", async () => {
    const hooks = await Flockwork({ directory: d, client: undefined });
    const ev = (hooks as Record<string, unknown>)["event"] as
      (i: { event: unknown }) => Promise<void>;
    _setLastActivityForTest("sess-2", Date.now());
    await ev({ event: { type: "session.idle", properties: { sessionID: "sess-2" } } });
    const t = logText();
    expect(t).toContain("ARGV:harness backstop git@b.example:r.git sess-2");
    expect(t).not.toContain("abandoned"); // a plain idle never abandons / never writes a verdict
  });

  test("an abandoned session (quiet past the threshold) gets the non-terminal stall", async () => {
    const hooks = await Flockwork({ directory: d, client: undefined });
    const ev = (hooks as Record<string, unknown>)["event"] as
      (i: { event: unknown }) => Promise<void>;
    // the session was active a bit ago, then went quiet
    _setLastActivityForTest("sess-3", Date.now() - 50);
    await ev({ event: { type: "session.idle", properties: { sessionID: "sess-3" } } });
    const before = logText();
    expect(before).not.toContain("ARG:harness backstop"); // sanity
    expect(before).not.toContain(" sess-3 abandoned");
    await wait(90); // > the 30ms threshold
    const after = logText();
    expect(after).toContain("ARGV:harness backstop git@b.example:r.git sess-3 abandoned");
  });

  test("a resumed session cancels the pending stall (no abandoned backstop)", async () => {
    const hooks = await Flockwork({ directory: d, client: undefined });
    const ev = (hooks as Record<string, unknown>)["event"] as
      (i: { event: unknown }) => Promise<void>;
    const cm = (hooks as Record<string, unknown>)["chat.message"] as
      (i: { sessionID: string; model?: { modelID: string } }) => Promise<void>;
    _setLastActivityForTest("sess-4", Date.now());
    await ev({ event: { type: "session.idle", properties: { sessionID: "sess-4" } } });
    // the user comes back -> the abandonment check is cancelled
    await cm({ sessionID: "sess-4", model: { modelID: "m" } });
    await wait(90);
    const t = logText();
    expect(t).not.toContain(" sess-4 abandoned");
  });

  test("the substrate backstop is the policy: fireBackstop only shells out", () => {
    const before = logText().split("backstop").length;
    fireBackstop(d, "sess-5", true);
    const after = logText();
    expect(after).toContain("ARGV:harness backstop git@b.example:r.git sess-5 abandoned");
    expect(after.split("backstop").length).toBe(before + 1);
  });
});
