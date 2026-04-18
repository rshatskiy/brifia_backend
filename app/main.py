from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
from app.routers import auth, meetings, series, profiles, prompts, plans, payments, internal, uploads, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (use Alembic migrations in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
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

# Internal API for faster-whisper
app.include_router(internal.router)
app.include_router(uploads.router)

# WebSocket
app.include_router(ws.router)


@app.get("/")
async def root():
    return {"message": "Brifia API"}
