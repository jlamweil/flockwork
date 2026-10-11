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

import {
  Flockwork,
  active,
  originOf,
  runFw,
  markerPath,
} from "./flockwork.ts";

const mktmp = (): string => mkdtempSync(join(tmpdir(), "fw-plugin-"));
const mockInput = (directory: string) => ({ directory, client: undefined });
const writeMarker = (directory: string, origin: string): void => {
  mkdirSync(join(directory, ".flockwork"), { recursive: true });
  writeFileSync(join(directory, ".flockwork", "origin"), origin + "\n");
};

describe("marker-only workspace-detect", () => {
  test("silent with no marker, even under a stray SWARM_ORIGIN env", () => {
    const d = mktmp();
    process.env.SWARM_ORIGIN = "ssh://prod.example.invalid/board.git";
    try {
      expect(active(d)).toBe(false);
      expect(originOf(d)).toBe(null);
    } finally {
      delete process.env.SWARM_ORIGIN;
    }
  });

  test("a local marker activates and is the sole origin source", () => {
    const d = mktmp();
    writeMarker(d, "git@board.example:repo.git");
    expect(active(d)).toBe(true);
    expect(originOf(d)).toBe("git@board.example:repo.git");
    expect(markerPath(d)).toBe(join(d, ".flockwork", "origin"));
  });
});

describe("plugin load (C8 skeleton)", () => {
  beforeEach(() => {
    delete (globalThis as Record<string, unknown>).flockworkRegistered;
    delete (globalThis as Record<string, unknown>).__flockworkRegistered;
  });

  test("loads and returns an empty hooks object without throwing", async () => {
    const d = mktmp();
    const hooks = await Flockwork(mockInput(d));
    expect(hooks).toEqual({});
  });

  test("idempotency guard: a second load is a no-op (no double-register)", async () => {
    const d = mktmp();
    await Flockwork(mockInput(d));
    const again = await Flockwork(mockInput(d));
    expect(again).toEqual({});
  });
});

describe("fail-safe runFw shell-out", () => {
  test("passes the marker's origin on the spawn env and returns the result", () => {
    const d = mktmp();
    writeMarker(d, "git@board.example:repo.git");
    const log = join(d, "spawn.log");
    const bin = join(d, "fake-flockwork");
    writeFileSync(
      bin,
      `#!/bin/bash\nprintf '%s\\n' "$*" >> "${log}"\nprintf 'ORIGIN=%s\\n' "$SWARM_ORIGIN" >> "${log}"\nexit 0\n`,
    );
    chmodSync(bin, 0o755);
    process.env.FLOCKWORK_BIN = bin;
    try {
      const res = runFw(d, ["harness", "detect", d]);
      expect(res).toBeDefined();
      expect(res?.status).toBe(0);
      const logged = readFileSync(log, "utf8");
      expect(logged).toContain("harness detect");
      expect(logged).toContain("ORIGIN=git@board.example:repo.git");
    } finally {
      delete process.env.FLOCKWORK_BIN;
    }
  });

  test("does not pass a global origin default when there is no marker", () => {
    const d = mktmp();
    const log = join(d, "spawn.log");
    const bin = join(d, "fake-flockwork");
    writeFileSync(
      bin,
      `#!/bin/bash\nprintf 'ORIGIN=%s\\n' "$SWARM_ORIGIN" >> "${log}"\nexit 0\n`,
    );
    chmodSync(bin, 0o755);
    process.env.FLOCKWORK_BIN = bin;
    delete process.env.SWARM_ORIGIN;
    try {
      const res = runFw(d, ["harness", "detect", d]);
      expect(res).toBeDefined();
      const logged = readFileSync(log, "utf8");
      expect(logged).toContain("ORIGIN=");
      expect(logged).not.toContain("ORIGIN=ssh://");
    } finally {
      delete process.env.FLOCKWORK_BIN;
    }
  });

  test("a missing CLI fails safe (undefined, no throw)", () => {
    const d = mktmp();
    process.env.FLOCKWORK_BIN = "/nonexistent/flockwork-xyz";
    try {
      const res = runFw(d, ["harness", "detect", d]);
      expect(res).toBeUndefined();
    } finally {
      delete process.env.FLOCKWORK_BIN;
    }
  });
});
