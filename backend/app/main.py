from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text as sql_text

from .config import get_settings
from .db import get_engine
from .errors import register_error_handlers
from .routers import (
    actions,
    agents,
    ask,
    auth,
    conversations,
    dashboard,
    events,
    graph,
    me,
    memories,
    predictions,
    projects,
    sources,
    timemachine,
)

app = FastAPI(title="ATLAS", version="0.1.0", docs_url="/docs")
register_error_handlers(app)

# CORS solo per le origini configurate (default: involucro desktop Tauri).
# Il frontend web resta same-origin e non riceve header CORS.
_cors_origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
        max_age=600,
    )

app.include_router(auth.router)
app.include_router(sources.router)
app.include_router(projects.router)
app.include_router(events.router)
app.include_router(memories.router)
app.include_router(ask.router)
app.include_router(me.router)
app.include_router(graph.router)
app.include_router(conversations.router)
app.include_router(agents.router)
app.include_router(predictions.router)
app.include_router(actions.router)
app.include_router(timemachine.router)
app.include_router(dashboard.router)


@app.get("/health")
def health() -> dict:
    db_status = "up"
    try:
        with get_engine().connect() as conn:
            conn.execute(sql_text("SELECT 1"))
    except Exception:
        db_status = "down"
    return {"status": "ok", "db": db_status}
