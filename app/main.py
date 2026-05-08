from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
from app.routers import auth, meetings, series, profiles, prompts, plans, payments, internal, uploads, ws, participants, bitrix, meeting_tasks, exports


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (use Alembic migrations in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.stalekeeper import attach_to_app
    attach_to_app(app)
    yield
    if hasattr(app.state, "stalekeeper_scheduler"):
        app.state.stalekeeper_scheduler.shutdown()
    await engine.dispose()


app = FastAPI(title="Brifia API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public & user-facing API
app.include_router(auth.router)
app.include_router(meetings.router)
app.include_router(series.router)
app.include_router(profiles.router)
app.include_router(prompts.router)
app.include_router(plans.router)
app.include_router(payments.router)
app.include_router(participants.router)
app.include_router(bitrix.router)
app.include_router(meeting_tasks.meeting_tasks_router)
app.include_router(meeting_tasks.tasks_router)
app.include_router(exports.router)

# Internal API for faster-whisper
app.include_router(internal.router)
app.include_router(uploads.router)

# WebSocket
app.include_router(ws.router)


@app.get("/")
async def root():
    return {"message": "Brifia API"}


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint for app/metrics.py counters and gauges."""
    from fastapi.responses import Response
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
