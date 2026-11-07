"""CLI ATLAS: parla con il backend dal terminale.

    atlas register EMAIL PASSWORD
    atlas login EMAIL PASSWORD
    atlas ask "A che punto sono col progetto?"
    atlas note "Il server di produzione e su Oracle Cloud"
    atlas search "database"
    atlas projects
"""
import argparse
import json
import os
import sys
from pathlib import Path

import httpx

CONFIG = Path.home() / ".atlas.toml"


def _load() -> dict:
    cfg: dict = {"base_url": os.environ.get("ATLAS_API_URL", "http://localhost:8000"), "token": None}
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    return cfg


def _save(cfg: dict) -> None:
    CONFIG.write_text("\n".join(f'{k} = "{v}"' for k, v in cfg.items() if v is not None) + "\n")


def _client() -> tuple[httpx.Client, dict]:
    cfg = _load()
    headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {}
    return httpx.Client(base_url=cfg["base_url"], headers=headers, timeout=30.0), cfg


def main() -> None:
    parser = argparse.ArgumentParser(prog="atlas", description="CLI per ATLAS")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("register", "login"):
        p = sub.add_parser(name)
        p.add_argument("email")
        p.add_argument("password")
    p = sub.add_parser("ask")
    p.add_argument("text")
    p = sub.add_parser("note")
    p.add_argument("text")
    p = sub.add_parser("search")
    p.add_argument("query")
    sub.add_parser("projects")
    args = parser.parse_args()

    client, cfg = _client()
    try:
        if args.cmd in ("register", "login"):
            r = client.post(f"/auth/{args.cmd}", json={"email": args.email, "password": args.password})
            r.raise_for_status()
            data = r.json()
            cfg["token"] = data["token"]
            _save(cfg)
            print(f"Ok, autenticato come {data['user']['email']}")
        elif args.cmd == "ask":
            r = client.post("/ask", json={"text": args.text})
            r.raise_for_status()
            data = r.json()
            print(data["answer"])
            for c in data.get("citations", []):
                src = c["source"]["label"] if c.get("source") else "?"
                print(f"  [{c['ref']}] ({c['kind']}, conf {c['confidence']:.2f}, fonte: {src}) {c['content']}")
        elif args.cmd == "note":
            r = client.post("/ask", json={"text": f"ricorda: {args.text}"})
            r.raise_for_status()
            print(r.json()["answer"])
        elif args.cmd == "search":
            r = client.post("/memories/search", json={"query": args.query})
            r.raise_for_status()
            for hit in r.json():
                m = hit["memory"]
                print(f"[{hit['score']:.2f}] ({m['kind']}/{m['origin']}) {m['content']}")
        elif args.cmd == "projects":
            r = client.get("/projects")
            r.raise_for_status()
            for p in r.json():
                print(f"- {p['name']} ({p['status']}) {p['description']}")
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json()
        except json.JSONDecodeError:
            detail = exc.response.text
        print(f"Errore {exc.response.status_code}: {detail}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
