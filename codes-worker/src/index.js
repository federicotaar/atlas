// ATLAS codes API - email verification codes (register / login), NOVA-pattern:
// Cloudflare Worker + D1, Brevo transactional email. Free tier only.
// Trust model: TUTTI i segreti stanno nel worker (BREVO_API_KEY, CODE_PEPPER,
// CODE_SIGNING_JWK). Il client pubblico ATLAS non ha chiavi: il backend locale
// verifica la firma Ed25519 dell'attestazione con la CHIAVE PUBBLICA (non un segreto).
// Scopo: purpose register|login SOLI (password-reset = change separato da rivedere).
// Footer: NESSUN indirizzo postale - Brevo non lo richiede per email transazionali
// (lo richiede solo l'onboarding account e le campagne marketing) e contiene
// dati personali del proprietario. Non reinserirlo senza decisione dell'utente.

import { LOGO_PNG_B64 } from "./logo.js";

const CODE_TTL_MS = 10 * 60 * 1000;      // 10 minuti
const ATTEST_TTL_S = 300;                // attestazione valida 5 minuti
const CODE_LEN = 6;
const MAX_ATTEMPTS = 5;
const RL_WINDOW_MS = 10 * 60 * 1000;
const RL_MAX_PER_EMAIL = 3;              // codici per email nella finestra
const RL_MAX_PER_IP = 10;                // richieste per IP nella finestra
const DAILY_SEND_MAX_DEFAULT = 100;      // budget giornaliero ATLAS (quota Brevo 300/d condivisa con NOVA)

// CF-Connecting-IP e' scritto dall'edge Cloudflare (non spoofabile dal client in
// produzione). Il chiamante e' il BACKEND LOCALE dell'utente: l'IP visto dal
// worker e' l'IP di uscita della sua macchina, cioe' l'identita' giusta.

function cors(env, origin) {
  const allowed = (env.ALLOWED_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean);
  const ok = allowed.includes(origin) ? origin : allowed[0] || "";
  return {
    "Access-Control-Allow-Origin": ok,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

const json = (env, origin, status, obj) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", ...cors(env, origin) },
  });

const b64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function sha256hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

function newCode() {
  const n = crypto.getRandomValues(new Uint32Array(1))[0] % 10 ** CODE_LEN;
  return String(n).padStart(CODE_LEN, "0");
}

// Firma Ed25519 dell'attestazione: la chiave privata resta nel worker (secret),
// la pubblica va nel repo ATLAS (valore pubblico, non un segreto).
async function signAttestation(env, email, purpose) {
  const key = await crypto.subtle.importKey(
    "jwk", JSON.parse(env.CODE_SIGNING_JWK), { name: "Ed25519" }, false, ["sign"]);
  const now = Math.floor(Date.now() / 1000);
  const payload = JSON.stringify({
    iss: "atlas-codes", email, purpose, iat: now, exp: now + ATTEST_TTL_S,
    jti: crypto.randomUUID(),
  });
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, key, new TextEncoder().encode(payload));
  return b64u(new TextEncoder().encode(payload)) + "." + b64u(sig);
}

function htmlEmail(purpose, code, env) {
  const heading = purpose === "register" ? "Benvenuto in ATLAS" : "Bentornato in ATLAS";
  const action = purpose === "register"
    ? "usa questo codice per confermare la tua email"
    : "usa questo codice per accedere";
  const logo = env.LOGO_URL
    ? `<img src="${env.LOGO_URL}" width="56" height="56" alt="ATLAS" style="border-radius:14px;display:inline-block">`
    : `<div style="width:56px;height:56px;border-radius:14px;background:#000;border:1px solid #232327;display:inline-block;line-height:56px;font-size:28px;font-weight:700;color:#fff">A</div>`;
  return `<!doctype html><html><body style="margin:0;background:#0a0a0b;color:#ededef;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif">
  <div style="max-width:420px;margin:0 auto;padding:40px 24px;text-align:center">
    ${logo}
    <h1 style="font-size:20px;letter-spacing:.18em;margin:18px 0 4px">ATLAS</h1>
    <p style="color:#8c8c95;font-size:14px;margin:0 0 24px">${heading}, ${action}:</p>
    <div style="font-size:34px;font-weight:700;letter-spacing:.35em;background:#121214;border:1px solid #232327;border-radius:14px;padding:18px 0">${code}</div>
    <p style="color:#8c8c95;font-size:12.5px;margin:20px 0 0">Il codice scade tra 10 minuti. Se non l'hai richiesto tu, ignora questa email.</p>
  </div></body></html>`;
}

async function sendCodeEmail(env, email, purpose, code) {
  // Test seam: in sviluppo locale nessun invio reale. In produzione il secret
  // e' una chiave xkeysib-*, mai questo valore.
  if (env.BREVO_API_KEY === "test-local") return;
  const res = await fetch("https://api.brevo.com/v3/smtp/email", {
    method: "POST",
    headers: { "api-key": env.BREVO_API_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({
      sender: { email: env.SENDER_EMAIL, name: env.SENDER_NAME || "ATLAS" },
      to: [{ email }],
      subject: `${code} - il tuo codice ATLAS`,
      htmlContent: htmlEmail(purpose, code, env),
    }),
  });
  if (!res.ok) throw new Error(`brevo ${res.status}`);
}

// Budget giornaliero applicato PRIMA dell'invio: fail-safe (meglio rifiutare che sforare).
// Protegge la quota Brevo condivisa; NON e' prevenzione abusi completa (un attaccante
// puo' comunque consumare il budget del giorno. (Turnstile rimosso su richiesta del proprietario in v2.0.1.)
async function dailyBudgetExceeded(env) {
  const max = parseInt(env.DAILY_SEND_MAX || String(DAILY_SEND_MAX_DEFAULT), 10);
  const day = new Date().toISOString().slice(0, 10);
  const row = await env.DB.prepare(
    "INSERT INTO daily_sends (day, n) VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET n = n + 1 RETURNING n"
  ).bind(day).first();
  return (row?.n ?? 999999) > max;
}

// Insert-first-then-count: OGNI richiesta (anche rifiutata) entra nella
// finestra con una scrittura, poi il conteggio la include. Due richieste
// concorrenti si vedono a vicenda perche' gli INSERT si serializzano e ogni
// COUNT legge le righe gia' committate: cap stretto, niente race SELECT-then-INSERT.
async function hitRateLimits(env, email, ip) {
  await env.DB.prepare("INSERT INTO code_requests (email, ip, created_at) VALUES (?,?,?)")
    .bind(email, ip, Date.now()).run();
  const since = Date.now() - RL_WINDOW_MS;
  const e = await env.DB.prepare(
    "SELECT COUNT(*) n FROM code_requests WHERE email = ? AND created_at > ?").bind(email, since).first();
  if ((e?.n ?? 0) > RL_MAX_PER_EMAIL) return true;
  const i = await env.DB.prepare(
    "SELECT COUNT(*) n FROM code_requests WHERE ip = ? AND created_at > ?").bind(ip, since).first();
  return (i?.n ?? 0) > RL_MAX_PER_IP;
}

async function requestCode(request, env) {
  const origin = request.headers.get("Origin") || "";
  let body;
  try { body = await request.json(); } catch { return json(env, origin, 400, { error: "bad_json" }); }
  if (!["register", "login"].includes(body.purpose)) return json(env, origin, 400, { error: "bad_purpose" });
  const purpose = body.purpose;
  const email = String(body.email || "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return json(env, origin, 400, { error: "bad_email" });
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  if (await hitRateLimits(env, email, ip)) return json(env, origin, 429, { error: "rate_limited" });
  if (await dailyBudgetExceeded(env)) return json(env, origin, 429, { error: "daily_cap" });

  const code = newCode();
  const hash = await sha256hex(`${email}:${purpose}:${code}:${env.CODE_PEPPER}`);
  await env.DB.prepare(
    "INSERT INTO codes (email, purpose, code_hash, expires_at, attempts, consumed, created_at) VALUES (?,?,?,?,0,0,?)"
  ).bind(email, purpose, hash, Date.now() + CODE_TTL_MS, Date.now()).run();

  try {
    await sendCodeEmail(env, email, purpose, code);
  } catch (err) {
    return json(env, origin, 502, { error: "send_failed" });
  }
  return json(env, origin, 200, { ok: true, ttl_seconds: CODE_TTL_MS / 1000 });
}

async function verifyCode(request, env) {
  const origin = request.headers.get("Origin") || "";
  let body;
  try { body = await request.json(); } catch { return json(env, origin, 400, { error: "bad_json" }); }
  if (!["register", "login"].includes(body.purpose)) return json(env, origin, 400, { error: "bad_purpose" });
  const purpose = body.purpose;
  const email = String(body.email || "").trim().toLowerCase();
  const code = String(body.code || "").trim();
  const row = await env.DB.prepare(
    "SELECT id FROM codes WHERE email = ? AND purpose = ? ORDER BY id DESC LIMIT 1"
  ).bind(email, purpose).first();
  if (!row) return json(env, origin, 400, { ok: false, error: "no_code" });
  const hash = await sha256hex(`${email}:${purpose}:${code}:${env.CODE_PEPPER}`);
  // OGNI tentativo (giusto o sbagliato) incrementa attempts in UN SOLO statement
  // condizionato, e il codice giusto viene consumato nella stessa scrittura.
  // SQLite serializza gli scrittori: cap tentativi stretto e consumo atomico
  // anche sotto concorrenza (niente SELECT-then-UPDATE racy).
  const upd = await env.DB.prepare(
    `UPDATE codes SET attempts = attempts + 1,
       consumed = CASE WHEN code_hash = ? THEN 1 ELSE consumed END
     WHERE id = ? AND consumed = 0 AND attempts < ? AND expires_at > ?
     RETURNING attempts, consumed`
  ).bind(hash, row.id, MAX_ATTEMPTS, Date.now()).first();
  if (!upd) {
    // Nessuna riga aggiornata: classifica l'errore (gia' consumato / cap / scaduto).
    const cur = await env.DB.prepare(
      "SELECT consumed, attempts, expires_at FROM codes WHERE id = ?").bind(row.id).first();
    if (!cur) return json(env, origin, 400, { ok: false, error: "no_code" });
    if (cur.consumed) return json(env, origin, 400, { ok: false, error: "already_used" });
    if (cur.expires_at < Date.now()) return json(env, origin, 400, { ok: false, error: "expired" });
    if (cur.attempts >= MAX_ATTEMPTS) return json(env, origin, 429, { ok: false, error: "too_many_attempts" });
    return json(env, origin, 400, { ok: false, error: "no_code" });
  }
  if (!upd.consumed) return json(env, origin, 400, { ok: false, error: "wrong_code" });
  const attestation = await signAttestation(env, email, purpose);
  return json(env, origin, 200, { ok: true, attestation });
}

function logoPng() {
  const bin = atob(LOGO_PNG_B64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/logo.png") {
      return new Response(logoPng(), {
        headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=604800, immutable" },
      });
    }
    const origin = request.headers.get("Origin") || "";
    if (request.method === "OPTIONS") return new Response(null, { headers: cors(env, origin) });
    if (request.method !== "POST") return json(env, origin, 405, { error: "method" });
    if (url.pathname === "/v1/codes/request") return requestCode(request, env);
    if (url.pathname === "/v1/codes/verify") return verifyCode(request, env);
    return json(env, origin, 404, { error: "not_found" });
  },
};
