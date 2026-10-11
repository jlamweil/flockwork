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

const { Flockwork, getProtocol, FALLBACK_PROTOCOL, _resetProtocolCache } =
  await import("./flockwork.ts");

const mktmp = (): string => mkdtempSync(join(tmpdir(), "fw-c10-"));
const writeMarker = (d: string): void => {
  mkdirSync(join(d, ".flockwork"), { recursive: true });
  writeFileSync(join(d, ".flockwork", "origin"), "git@b.example:r.git\n");
};

// fake flockwork that answers `harness protocol` with the canonical marker
const FAKE = `#!/bin/bash
L="$FW_LOG"
printf 'ARGV:%s\\n' "$*" >> "$L"
case "$1" in
  harness) if [ "$2" = "protocol" ]; then
             echo "PROTOCOL-CANONICAL-TEXT Step 1 fw_brief fw_claim Step 2 fw_judge"
             exit 0; fi
           exit 0;;
  *) exit 0;;
esac
`;

let d: string;
let log: string;
const setup = (withMarker: boolean): void => {
  d = mktmp();
  if (withMarker) writeMarker(d);
  log = join(d, "fw.log");
  const bin = join(d, "fake-flockwork");
  writeFileSync(bin, FAKE);
  chmodSync(bin, 0o755);
  process.env.FLOCKWORK_BIN = bin;
  process.env.FW_LOG = log;
};
const logExists = (): boolean => {
  try {
    readFileSync(log, "utf8");
    return true;
  } catch {
    return false;
  }
};

beforeEach(() => {
  _resetProtocolCache();
  delete (globalThis as Record<string, unknown>).__flockworkRegistered;
});

describe("protocol injection (system.transform)", () => {
  test("appends the substrate's canonical protocol in an active workspace", async () => {
    setup(true);
    const hooks = await Flockwork({ directory: d, client: undefined });
    const transform = (hooks as Record<string, unknown>)["experimental.chat.system.transform"] as
      (i: unknown, o: { system: string[] }) => Promise<void>;
    const output = { system: ["base system"] };
    await transform({ sessionID: "s", model: {} }, output);
    expect(output.system.length).toBe(2);
    expect(output.system[1]).toContain("PROTOCOL-CANONICAL-TEXT");
    expect(output.system[1]).toContain("fw_claim");
  });

  test("injects nothing in a non-flockwork workspace", async () => {
    setup(false);
    const hooks = await Flockwork({ directory: d, client: undefined });
    const transform = (hooks as Record<string, unknown>)["experimental.chat.system.transform"] as
      (i: unknown, o: { system: string[] }) => Promise<void>;
    const output = { system: ["base system"] };
    await transform({ sessionID: "s", model: {} }, output);
    expect(output.system).toEqual(["base system"]);
  });

  test("the protocol is fetched from the CLI and cached (one fetch per process)", async () => {
    setup(true);
    getProtocol(d);
    getProtocol(d);
    getProtocol(d);
    const logged = readFileSync(log, "utf8");
    const fetches = logged.split("ARGV:harness protocol").length - 1;
    expect(fetches).toBe(1);
  });

  test("falls back to the embedded note when the CLI cannot run", () => {
    setup(true);
    process.env.FLOCKWORK_BIN = "/nonexistent/flockwork-xyz";
    const proto = getProtocol(d);
    expect(proto).toBe(FALLBACK_PROTOCOL);
    expect(proto).toContain("fw_claim");
    // the fallback is NOT cached: after the CLI 'comes back', a fetch wins
    process.env.FLOCKWORK_BIN = join(d, "fake-flockwork");
    const again = getProtocol(d);
    expect(again).toContain("PROTOCOL-CANONICAL-TEXT");
    expect(logExists()).toBe(true);
  });
});
