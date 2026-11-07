from fastapi import FastAPI
from sqlalchemy import text as sql_text

from .db import get_engine
from .errors import register_error_handlers
from .routers import ask, auth, events, me, memories, projects, sources

app = FastAPI(title="ATLAS", version="0.1.0", docs_url="/docs")
register_error_handlers(app)

app.include_router(auth.router)
app.include_router(sources.router)
app.include_router(projects.router)
app.include_router(events.router)
app.include_router(memories.router)
app.include_router(ask.router)
app.include_router(me.router)


@app.get("/health")
def health() -> dict:
    db_status = "up"
    try:
        with get_engine().connect() as conn:
            conn.execute(sql_text("SELECT 1"))
    except Exception:
        db_status = "down"
    return {"status": "ok", "db": db_status}
