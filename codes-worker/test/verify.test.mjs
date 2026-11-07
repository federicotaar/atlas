// Verify battery: happy path, firma Ed25519, replay, concorrenza, scadenza, tentativi.
import { readFileSync } from "fs";
import { createPublicKey, verify as nverify } from "crypto";

const BASE = "http://127.0.0.1:8787";
const H = { "Content-Type": "application/json" };
const results = [];
const check = (name, cond, extra = "") =>
  results.push(`${cond ? "PASS" : "FAIL"}  ${name}${cond ? "" : "  " + extra}`);
const verifyReq = (email, purpose, code) =>
  fetch(BASE + "/v1/codes/verify", { method: "POST", headers: H, body: JSON.stringify({ email, purpose, code }) });

// chiave pubblica dal JWK di test in .dev.vars
const devvars = readFileSync(".dev.vars", "utf8");
const privJwk = JSON.parse(devvars.match(/^CODE_SIGNING_JWK=(.*)$/m)[1]);
const pub = createPublicKey({ key: { kty: privJwk.kty, crv: privJwk.crv, x: privJwk.x }, format: "jwk" });

const b64ud = (s) => Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");

// 1 happy path + firma valida
let r = await verifyReq("happy@example.com", "register", "424242");
let body = await r.json();
check("verify happy -> 200 ok + attestation", r.status === 200 && body.ok === true && !!body.attestation, JSON.stringify(body));
if (body.attestation) {
  const [p, s] = body.attestation.split(".");
  const claims = JSON.parse(b64ud(p).toString());
  const sigOk = nverify(null, b64ud(p), pub, b64ud(s));
  check("attestation: firma Ed25519 valida", sigOk);
  check("attestation: iss/email/purpose/exp/jti", claims.iss === "atlas-codes" && claims.email === "happy@example.com" && claims.purpose === "register" && claims.exp > Date.now() / 1000 && !!claims.jti, JSON.stringify(claims));
}

// 2 codice consumato: riuso -> already_used
r = await verifyReq("happy@example.com", "register", "424242");
check("riuso codice consumato -> already_used", r.status === 400 && (await r.json()).error === "already_used", String(r.status));

// 3 replay su altra chiamata (login, codice diverso): primo ok, secondo no_code
r = await verifyReq("replay@example.com", "login", "515151");
check("replay setup: primo verify ok", r.status === 200);
r = await verifyReq("replay@example.com", "login", "515151");
check("replay: secondo verify -> already_used", r.status === 400 && (await r.json()).error === "already_used");

// 4 concorrenza: 8 verify paralleli, ESATTAMENTE uno vince
const race = await Promise.all(
  Array.from({ length: 8 }, () => verifyReq("race@example.com", "register", "626262").then(async (rr) => ({ s: rr.status, b: await rr.json() })))
);
const oks = race.filter((x) => x.s === 200 && x.b.ok).length;
const losers = race.filter((x) => x.s === 400 && ["already_used", "no_code"].includes(x.b.error)).length;
check("concorrenza: esattamente 1 vincitore su 8", oks === 1 && oks + losers === 8, JSON.stringify(race.map((x) => [x.s, x.b.error || "ok"])));

// 5 scadenza
r = await verifyReq("expired@example.com", "register", "737373");
check("codice scaduto -> expired", r.status === 400 && (await r.json()).error === "expired");

// 6 tentativi: 5 sbagliati -> 6o too_many_attempts (anche col codice giusto)
let lastErr = "", lastSt = 0;
for (let i = 0; i < 6; i++) {
  r = await verifyReq("attempts@example.com", "register", "000000");
  lastSt = r.status; lastErr = (await r.json()).error;
}
check("5 tentativi sbagliati -> too_many_attempts", lastSt === 429 && lastErr === "too_many_attempts", `${lastSt} ${lastErr}`);
r = await verifyReq("attempts@example.com", "register", "848484");
check("dopo cap tentativi anche codice giusto rifiutato", r.status === 429);

// 6b cap tentativi STRETTO sotto concorrenza: 8 verify sbagliati in parallelo
// -> esattamente 5 conteggiati (wrong_code), gli altri too_many_attempts. Il DB
// di test ha un codice fresco seminato da bash prima di questa fase.
const wrace = await Promise.all(
  Array.from({ length: 8 }, () => verifyReq("caprace@example.com", "register", "000000").then(async (rr) => ({ s: rr.status, b: await rr.json() })))
);
const wrongs = wrace.filter((x) => x.s === 400 && x.b.error === "wrong_code").length;
const capped = wrace.filter((x) => x.s === 429 && x.b.error === "too_many_attempts").length;
check("cap stretto concorrente: esattamente 5 tentativi conteggiati su 8", wrongs === 5 && capped === 3, JSON.stringify(wrace.map((x) => [x.s, x.b.error])));

// 7 daily cap: seed SQL a 100 fatto da bash prima di questa fase? No: fatto in batteria A.
// Qui verifichiamo che verify NON conti nel budget giornaliero (verify sempre consentito):
r = await verifyReq("expired@example.com", "register", "000000");
check("verify mai bloccato da daily cap (expired, non daily_cap)", (await r.json()).error === "expired");

console.log(results.join("\n"));
