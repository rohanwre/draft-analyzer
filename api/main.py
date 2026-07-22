import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import drafts, players

app = FastAPI(title="Fantasy Draft Advisor API")

# Comma-separated list of allowed frontend origins, e.g.
# "http://localhost:5173,https://your-app.vercel.app". Falls back to local dev only.
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(drafts.router)
app.include_router(players.router)

@app.get("/health")
def health():
    return {"status": "ok"}
