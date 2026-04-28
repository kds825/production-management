import { describe, it, expect, vi, afterEach } from "vitest";

describe("FEATURE_FLAG_CASCADE_V2", () => {
  const ORIG = process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2;
  afterEach(() => {
    if (ORIG === undefined)
      delete process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2;
    else process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2 = ORIG;
    vi.resetModules();
  });

  async function load() {
    const mod = await import("../featureFlags");
    return mod.FEATURE_FLAG_CASCADE_V2;
  }

  it("disabled when env undefined", async () => {
    delete process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2;
    expect(await load()).toBe(false);
  });

  it.each(["on", "ON", "On", "1", "true", "TRUE", "yes"])(
    "enabled when env = %s",
    async (val) => {
      process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2 = val;
      vi.resetModules();
      expect(await load()).toBe(true);
    },
  );

  it.each(["off", "0", "false", "no", ""])(
    "disabled when env = %s",
    async (val) => {
      process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2 = val;
      vi.resetModules();
      expect(await load()).toBe(false);
    },
  );

  it("trims whitespace", async () => {
    process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2 = "  on  ";
    vi.resetModules();
    expect(await load()).toBe(true);
  });
});
