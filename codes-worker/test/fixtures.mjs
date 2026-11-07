// Genera SQL per codici noti (pepper di test) da iniettare nel D1 locale.
import { createHash } from "crypto";

const PEPPER = "test-pepper-locale";
const now = Date.now();
const hash = (email, purpose, code) =>
  createHash("sha256").update(`${email}:${purpose}:${code}:${PEPPER}`).digest("hex");

const rows = [
  ["happy@example.com", "register", "424242", now + 600000],
  ["replay@example.com", "login", "515151", now + 600000],
  ["race@example.com", "register", "626262", now + 600000],
  ["expired@example.com", "register", "737373", 1000],
  ["attempts@example.com", "register", "848484", now + 600000],
  ["caprace@example.com", "register", "999999", now + 600000],
];

const vals = rows
  .map(([e, p, c, exp]) => `('${e}','${p}','${hash(e, p, c)}',${exp},0,0,${now})`)
  .join(",");
console.log(`INSERT INTO codes (email, purpose, code_hash, expires_at, attempts, consumed, created_at) VALUES ${vals};`);
