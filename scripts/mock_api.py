import json
from http.server import BaseHTTPRequestHandler, HTTPServer

# Dati fittizi dimostrativi: nessun riferimento a persone/progetti reali.
CIT = {"ref":"M1","memory_id":"demo-m1","content":"Il telescopio del club demo si trova nella stanza B12","kind":"fact","origin":"stated","confidence":0.85,"recorded_at":"2026-10-02T10:00:00Z","source":{"id":"s1","kind":"chat","label":"chat demo","created_at":"2026-10-02T09:00:00Z"}}
PROV = {"memories": "demo-m1", "events": None}

def dashboard():
    item = lambda **kw: kw
    return {"generated_at":"2026-11-07T18:00:00Z","provenance_rule":"Ogni elemento espone gli id dei dati da cui deriva.","panels":{
      "projects":[item(project_id="demo-p1",name="Demo Orbitale",status="active",target_date="2027-01-15",events_count=7,last_event_at="2026-11-05T09:30:00Z",derived_from=PROV),
                  item(project_id="demo-p2",name="Progetto Fittizio",status="completed",target_date=None,events_count=21,last_event_at="2026-10-20T10:05:00Z",derived_from=PROV)],
      "upcoming":{"predictions":[item(id="demo-pr1",question="La simulazione demo finira entro venerdi?",verify_at="2026-11-13T00:00:00Z",derived_from=PROV)],
                  "project_deadlines":[item(project_id="demo-p1",name="Demo Orbitale",target_date="2027-01-15",derived_from=PROV)]},
      "recent_events":[item(id="demo-e1",type="demo_sync",event_at="2026-11-06T18:20:00Z",project_id="demo-p1",derived_from=PROV),
                       item(id="demo-e2",type="demo_note",event_at="2026-11-06T16:30:00Z",project_id="demo-p1",derived_from=PROV)],
      "predictions":{"open":1,"verified":3,"due_now":1,"metrics":{"count":3,"mae":1.42},
                     "due_items":[item(id="demo-pr2",question="Il test demo superera la soglia di esempio?",derived_from=PROV)]},
      "agents":{"active_last_24h":1,"last_runs":[item(id="demo-r1",agent="demo_agent",status="completed",created_at="2026-11-06T08:00:00Z",derived_from=PROV)]},
      "automations":{"connectors":[item(id="demo-c1",kind="demo_connector",status="authorized",derived_from=PROV)],"engine_actions_by_status":{"executed":2}},
      "important_memories":[item(id="demo-m1",content="Il telescopio del club demo si trova nella stanza B12",kind="fact",confidence=0.85,recorded_at="2026-10-02T10:00:00Z",derived_from=PROV)],
      "authorization_requests":{"pending_connectors":[],"pending_confirm_actions":[]}}}

class H(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.path in ("/auth/login","/auth/register","/auth/login-code"):
            return self._json({"token":"demo-token"})
        if self.path == "/auth/codes/request":
            return self._json({"ok":True,"ttl_seconds":600})
        if self.path == "/auth/codes/check":
            return self._json({"ok":True,"signup_token":"mock-signup-token-demo"})
        if self.path == "/ask":
            return self._json({"classification":"question","answer":"Il telescopio del club demo si trova nella stanza B12.","citations":[CIT],"saved_memory":None,"conversation_id":"demo-conv"})
        return self._json({"error":{"message":"not found"}},404)
    def do_GET(self):
        if self.path == "/dashboard":
            return self._json(dashboard())
        return self._json({"error":{"message":"not found"}},404)
    def log_message(self,*a): pass

HTTPServer(("127.0.0.1",8000),H).serve_forever()
