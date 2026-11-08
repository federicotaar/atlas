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

import { probeTauriBase, versionAtLeast } from "./api";

describe("versionAtLeast", () => {
  it("confronta le versioni semantiche", () => {
    expect(versionAtLeast("2.1.1", "2.1.1")).toBe(true);
    expect(versionAtLeast("2.2.0", "2.1.1")).toBe(true);
    expect(versionAtLeast("3.0.0", "2.1.1")).toBe(true);
    expect(versionAtLeast("2.1.0", "2.1.1")).toBe(false);
    expect(versionAtLeast("2.0.1", "2.1.1")).toBe(false);
    expect(versionAtLeast("1.9.9", "2.1.1")).toBe(false);
  });

  it("rifiuta versioni malformate", () => {
    expect(versionAtLeast("dev", "2.1.1")).toBe(false);
    expect(versionAtLeast("2.1", "2.1.1")).toBe(false);
  });
});

function fakeFetch(handlers: Record<number, () => Promise<Response> | Response | Error>) {
  return ((url: string) => {
    const m = /:(\d+)\//.exec(url);
    const port = Number(m![1]);
    const h = handlers[port];
    if (!h) return Promise.reject(new Error("connection refused"));
    const out = h();
    return out instanceof Error ? Promise.reject(out) : Promise.resolve(out);
  }) as unknown as typeof fetch;
}

const okBody = (v: Record<string, unknown>) =>
  new Response(JSON.stringify(v), { status: 200 });

describe("probeTauriBase", () => {
  const candidates = [8000, 8100, 8101];

  it("usa 8000 quando risponde un ATLAS compatibile", async () => {
    const f = fakeFetch({
      8000: () => okBody({ app: "atlas", api: "v1", version: "2.1.1" }),
    });
    expect(await probeTauriBase(f, candidates, "2.1.1")).toBe("http://127.0.0.1:8000");
  });

  it("MAI un vecchio backend 2.0.x senza versione: passa alla porta di riserva", async () => {
    const f = fakeFetch({
      8000: () => okBody({ status: "ok", db: "up", app: "atlas", api: "v1" }),
      8100: () => okBody({ app: "atlas", api: "v1", version: "2.1.1" }),
    });
    expect(await probeTauriBase(f, candidates, "2.1.1")).toBe("http://127.0.0.1:8100");
  });

  it("MAI un backend piu' vecchio del minimo", async () => {
    const f = fakeFetch({
      8000: () => okBody({ app: "atlas", api: "v1", version: "2.1.0" }),
      8100: () => okBody({ app: "atlas", api: "v1", version: "2.1.1" }),
    });
    expect(await probeTauriBase(f, candidates, "2.1.1")).toBe("http://127.0.0.1:8100");
  });

  it("ignora servizi estranei e porte spente", async () => {
    const f = fakeFetch({
      8000: () => okBody({ status: "ok" }),
      8100: () => new Response("no", { status: 500 }),
      8101: () => okBody({ app: "atlas", api: "v1", version: "2.1.1" }),
    });
    expect(await probeTauriBase(f, candidates, "2.1.1")).toBe("http://127.0.0.1:8101");
  });

  it("null se nessuna porta ha un ATLAS compatibile", async () => {
    const f = fakeFetch({
      8000: () => okBody({ app: "atlas", api: "v1" }),
    });
    expect(await probeTauriBase(f, candidates, "2.1.1")).toBeNull();
  });
});

// Il link "Agente ATLAS" della dashboard deve puntare alla pagina servita
// dal backend locale sotto /agent, sia in Tauri (origine tauri.localhost,
// base assoluta) sia in dev (proxy /api relativo).
describe("desktop agent entry", () => {
  it("compone l'URL della pagina agente dalla base risolta", () => {
    expect(`${resolveBase(undefined, true)}/agent/`).toBe("http://127.0.0.1:8000/agent/");
    expect(`${resolveBase("http://127.0.0.1:8103", true)}/agent/`).toBe("http://127.0.0.1:8103/agent/");
  });
});
