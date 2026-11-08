"""Vecchia API ATLAS 2.0.x simulata per il test CI della porta di riserva.

Risponde a /health con l'identita' storica SENZA campo version (come la
2.0.x reale). Il launcher desktop NON deve mai riusarla ne' terminarla:
deve avviare il suo backend su una porta di riserva.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            # Esattamente la risposta della 2.0.x: niente campo version.
            return self._json({"status": "ok", "db": "up", "app": "atlas", "api": "v1"})
        return self._json({"detail": "Not Found"}, 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8000), H).serve_forever()
