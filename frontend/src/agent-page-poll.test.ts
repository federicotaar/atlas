/* @ts-nocheck */
// Regressione per il bug del poll della pagina /agent (backend/app/webui.py):
// il flag `polling` non veniva rilasciato mentre lo stato non era idle, quindi
// il secondo tick usciva subito e la pagina restava su "sta lavorando..." per
// sempre, con la risposta gia' in history ma MAI renderizzata (run CI
// 42550530371). Test endpoint Python non possono vedere questo difetto: il JS
// della pagina viene ESTRATTO dal template in webui.py ed eseguito in una vm
// Node con DOM/fetch/setTimeout stubbati, in modo deterministico.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import vm from "node:vm";
import { describe, expect, it } from "vitest";

function pageScript(): string {
  const py = readFileSync(resolve(__dirname, "../../backend/app/webui.py"), "utf8");
  const m = /<script>([\s\S]*?)<\/script>/.exec(py);
  if (!m) throw new Error("blocco <script> non trovato in webui.py");
  return m[1];
}

function makeEl() {
  return {
    style: {} as Record<string, string>,
    textContent: "",
    innerHTML: "",
    className: "",
    value: "",
    children: [] as any[],
    onclick: null as any,
    onkeydown: null as any,
    scrollTop: 0,
    scrollHeight: 0,
    appendChild(c: any) {
      this.children.push(c);
    },
    focus() {},
  };
}

type StateResp = { state: string; seq: number; reply: string | null; question: string | null };

function boot(statePlan: Array<StateResp | Error>) {
  const els: Record<string, ReturnType<typeof makeEl>> = {};
  const timers: Array<() => void> = [];
  const stateCalls = { n: 0 };
  const ctx: any = {
    console,
    document: {
      referrer: "",
      getElementById: (id: string) => (els[id] ||= makeEl()),
      createElement: () => makeEl(),
    },
    history: { length: 1, back() {} },
    fetch: async (url: string, opts?: any) => {
      if (url === "api/history") return { json: async () => ({ messages: [] }) };
      if (url === "api/chat") return { json: async () => ({ status: "running" }) };
      if (url === "api/state") {
        stateCalls.n += 1;
        const next = statePlan.length > 1 ? statePlan.shift()! : statePlan[0];
        if (next instanceof Error) throw next;
        return { json: async () => next };
      }
      throw new Error("fetch inattesa: " + url);
    },
  };
  ctx.setTimeout = (fn: () => void, _ms: number) => {
    timers.push(fn);
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(pageScript(), ctx);
  const flush = async () => {
    for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r));
  };
  const drain = async (cap = 25) => {
    for (let i = 0; i < cap && timers.length > 0; i++) {
      const batch = timers.splice(0, timers.length);
      for (const fn of batch) fn();
      await flush();
    }
    return timers.length;
  };
  return { ctx, els, stateCalls, flush, drain };
}

const running: StateResp = { state: "running", seq: 0, reply: null, question: null };
const idleReply = (text: string): StateResp => ({ state: "idle", seq: 1, reply: text, question: null });

describe("pagina /agent: poll di stato", () => {
  it("risposta oltre il primo tick: continua a pollare e renderizza la bolla", async () => {
    const { ctx, els, stateCalls, flush, drain } = boot([
      { ...running },
      { ...running },
      idleReply("Risposta finale visibile"),
    ]);
    els["in"].value = "ciao";
    const sent = ctx.send();
    await flush();
    await sent;
    await flush();
    await drain();
    // Il poll NON si e' fermato dopo il primo tick (bug: una sola chiamata).
    expect(stateCalls.n).toBeGreaterThanOrEqual(3);
    expect(els["st"].textContent).toBe("pronto");
    const rendered = els["log"].children.map((c: any) => c.innerHTML).join("\n");
    expect(rendered).toContain("Risposta finale visibile");
  });

  it("un fetch fallito non uccide il poll: riprova e renderizza", async () => {
    const { ctx, els, stateCalls, flush, drain } = boot([
      new Error("rete giu"),
      idleReply("Ripresa dopo errore"),
    ]);
    els["in"].value = "ciao";
    const sent = ctx.send();
    await flush();
    await sent;
    await flush();
    await drain();
    // Vecchio comportamento: catch -> polling=false e stop (1 sola chiamata).
    expect(stateCalls.n).toBeGreaterThanOrEqual(2);
    expect(els["st"].textContent).toBe("pronto");
    const rendered = els["log"].children.map((c: any) => c.innerHTML).join("\n");
    expect(rendered).toContain("Ripresa dopo errore");
  });
});
