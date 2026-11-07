import { describe, expect, it } from "vitest";
import { resolveBase } from "./api";

describe("resolveBase", () => {
  it("rispetta l'override esplicito", () => {
    expect(resolveBase("https://api.example.com/api", false)).toBe("https://api.example.com/api");
    expect(resolveBase("https://api.example.com/api", true)).toBe("https://api.example.com/api");
  });

  it("nel web usa il proxy relativo /api", () => {
    expect(resolveBase(undefined, false)).toBe("/api");
  });

  it("in Tauri chiama direttamente il backend locale", () => {
    expect(resolveBase(undefined, true)).toBe("http://127.0.0.1:8000");
  });
});
