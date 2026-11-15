-- Schema D1 del codes worker ATLAS.
--
-- Tabelle:
--   codes         - codici OTP a 6 cifre (hash SHA-256 con pepper, TTL, tentativi)
--   code_requests - finestra rate limit per email e per IP
--   daily_sends   - budget invii giornaliero (protezione quota Brevo)
-- Il dettaglio del trust boundary e' in README.md.
CREATE TABLE IF NOT EXISTS codes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,
  purpose TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  consumed INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_codes_email ON codes(email, purpose, id);
CREATE TABLE IF NOT EXISTS code_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,
  ip TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_req_email ON code_requests(email, created_at);
CREATE INDEX IF NOT EXISTS idx_req_ip ON code_requests(ip, created_at);
CREATE TABLE IF NOT EXISTS daily_sends (
  day TEXT PRIMARY KEY,
  n INTEGER NOT NULL DEFAULT 0
);
