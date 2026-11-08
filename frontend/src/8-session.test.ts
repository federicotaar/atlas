import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, hasToken, setOnUnauthorized, setToken } from "./api";

/** Routing della sessione morta: un 401 del backend a una richiesta
 *  autenticata (mai /auth/*) cancella il token e avvisa l'app, cosi'
 *  l'utente torna al login invece di restare in una sessione zombie. */

const store = new Map<string, string>();

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status });
}

const err401 = () =>
  jsonResponse(401, { error: { message: "Token mancante, scaduto o non valido." } });

beforeEach(() => {
  store.clear();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
  });
  setToken(null);
  setOnUnauthorized(null);
});

describe("sessione rifiutata dal backend (401)", () => {
  it("401 su endpoint autenticato: token cancellato e app avvisata", async () => {
    setToken("vecchio");
    vi.stubGlobal("fetch", vi.fn(async () => err401()));
    const spy = vi.fn();
    setOnUnauthorized(spy);

    await expect(api.getModelSettings()).rejects.toThrow("Token mancante");
    expect(hasToken()).toBe(false);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("401 in volo mentre e' avvenuto un nuovo login: la sessione NUOVA resta", async () => {
    setToken("vecchio");
    // Il backend risponde 401 alla richiesta col token vecchio, ma nel
    // frattempo l'utente ha gia' fatto un nuovo login: il token corrente
    // non e' piu' quello rifiutato e NON va toccato.
    vi.stubGlobal("fetch", vi.fn(async () => {
      setToken("nuovo");
      return err401();
    }));
    const spy = vi.fn();
    setOnUnauthorized(spy);

    await expect(api.getModelSettings()).rejects.toThrow("Token mancante");
    expect(hasToken()).toBe(true);
    expect(spy).not.toHaveBeenCalled();
  });

  it("401 da /auth/* (password sbagliata): niente logout forzato", async () => {
    setToken("sessione");
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse(401, { error: { message: "Email o password non valide." } })));
    const spy = vi.fn();
    setOnUnauthorized(spy);

    await expect(api.login("a@b.c", "sbagliata")).rejects.toThrow("non valide");
    expect(hasToken()).toBe(true);
    expect(spy).not.toHaveBeenCalled();
  });

  it("richiesta senza token: niente instradamento", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => err401()));
    const spy = vi.fn();
    setOnUnauthorized(spy);

    await expect(api.getModelSettings()).rejects.toThrow();
    expect(spy).not.toHaveBeenCalled();
  });
});
