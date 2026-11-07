// Batteria test reali contro il worker in wrangler dev (D1 sqlite locale).
const BASE = "http://127.0.0.1:8787";
const ORIGIN = { Origin: "http://localhost:5173", "Content-Type": "application/json" };
const results = [];
const check = (name, cond, extra = "") =>
  results.push(`${cond ? "PASS" : "FAIL"}  ${name}${cond ? "" : "  " + extra}`);

const post = (p, b, h = {}) =>
  fetch(BASE + p, { method: "POST", headers: { ...ORIGIN, ...h }, body: JSON.stringify({ turnstile_token: "test-token", ...b }) });
const j = (r) => r.json();

let n = 0;
const email = () => `t${++n}-${Date.now() % 100000}@example.com`;

// 1 logo
const lr = await fetch(BASE + "/logo.png");
const lbuf = Buffer.from(await lr.arrayBuffer());
check("GET /logo.png 200 image/png 4490b", lr.status === 200 && lr.headers.get("content-type") === "image/png" && lbuf.length === 4490, `${lr.status} ${lbuf.length}`);

// 2 bad json
let r = await fetch(BASE + "/v1/codes/request", { method: "POST", headers: ORIGIN, body: "{" });
check("bad json -> 400 bad_json", r.status === 400 && (await j(r)).error === "bad_json");

// 3 purpose guard
r = await post("/v1/codes/request", { email: email(), purpose: "reset" });
check("purpose reset -> 400 bad_purpose", r.status === 400 && (await j(r)).error === "bad_purpose");
r = await post("/v1/codes/verify", { email: email(), purpose: "reset", code: "123456" });
check("verify purpose reset -> 400", r.status === 400 && (await j(r)).error === "bad_purpose");

// 4 bad email
r = await post("/v1/codes/request", { email: "notanemail", purpose: "register" });
check("bad email -> 400", r.status === 400 && (await j(r)).error === "bad_email");

// 4b turnstile: token mancante/errato rifiutato
r = await post("/v1/codes/request", { email: email(), purpose: "register", turnstile_token: "" });
check("turnstile mancante -> 400 turnstile_failed", r.status === 400 && (await j(r)).error === "turnstile_failed");
r = await post("/v1/codes/request", { email: email(), purpose: "register", turnstile_token: "wrong" });
check("turnstile errato -> 400 turnstile_failed", r.status === 400 && (await j(r)).error === "turnstile_failed");

// 5 request ok (test seam: nessun invio reale)
const e1 = email();
r = await post("/v1/codes/request", { email: e1, purpose: "register" });
let body = await j(r);
check("request ok ttl 600", r.status === 200 && body.ok === true && body.ttl_seconds === 600, JSON.stringify(body));

// il codice reale lo leggiamo dal DB? No: lo ricaviamo via hash noto? Non possiamo.
// Per i test di verify usiamo un trucco: il worker accetta il codice che gli diamo solo via email.
// In test seam non vediamo il codice... SOLUZIONE: leggiamo dal DB locale via API? Non esiste.
// Quindi: per verify tests usiamo codice iniettato via SQL prima del test (vedi script bash).
// Qui testiamo solo gli errori:
r = await post("/v1/codes/verify", { email: e1, purpose: "register", code: "000000" });
check("wrong code -> 400 wrong_code", r.status === 400 && (await j(r)).error === "wrong_code");

// 6 email senza codice
r = await post("/v1/codes/verify", { email: email(), purpose: "register", code: "123456" });
check("no code -> 400 no_code", r.status === 400 && (await j(r)).error === "no_code");

// 7 rate limit per email (3/10min): 3 ok, 4a -> 429
const e2 = email();
let last;
for (let i = 0; i < 4; i++) { r = await post("/v1/codes/request", { email: e2, purpose: "register" }); last = r.status; }
check("per-email rate limit (4a -> 429)", last === 429 && (await j(r)).error === "rate_limited", String(last));

// 9 per-IP limit: 10/10min con IP custom (CF-Connecting-IP settabile SOLO in locale; in prod lo scrive l'edge)
const ip = "198.51.100.77";
let st = 0, blocked = 0;
for (let i = 0; i < 12; i++) {
  r = await post("/v1/codes/request", { email: email(), purpose: "register" }, { "CF-Connecting-IP": ip });
  st = r.status;
  if (st === 429) { blocked = (await j(r)).error; break; }
}
check("per-IP rate limit entro 12 richieste -> 429 rate_limited", st === 429 && blocked === "rate_limited", `${st} ${blocked}`);

// 10 CORS: origine non consentita non viene riflessa
r = await fetch(BASE + "/v1/codes/request", { method: "POST", headers: { "Content-Type": "application/json", Origin: "https://evil.example" }, body: JSON.stringify({ email: email(), purpose: "register", turnstile_token: "test-token" }) });
check("CORS: origine estranea non riflessa", (r.headers.get("access-control-allow-origin") || "") !== "https://evil.example");

// 11 method guard
r = await fetch(BASE + "/v1/codes/request");
check("GET su endpoint POST -> 405", r.status === 405);

console.log(results.join("\n"));

// FAIL-CLOSED: se FAILCLOSED_BASE punta a un'istanza SENZA TURNSTILE_SECRET,
// la richiesta deve essere rifiutata (mai bypass silenzioso senza chiave).
if (process.env.FAILCLOSED_BASE) {
  const r2 = await fetch(process.env.FAILCLOSED_BASE + "/v1/codes/request", {
    method: "POST", headers: H,
    body: JSON.stringify({ email: "fc@example.com", purpose: "register", turnstile_token: "test-token" }),
  });
  const b2 = await r2.json();
  console.log(
    r2.status === 400 && b2.error === "turnstile_failed"
      ? "PASS  fail-closed: senza TURNSTILE_SECRET -> 400 turnstile_failed"
      : `FAIL  fail-closed: atteso 400 turnstile_failed, ottenuto ${r2.status} ${JSON.stringify(b2)}`
  );
}
