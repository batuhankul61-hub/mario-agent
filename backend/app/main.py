import logging

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .config import ROOT_DIR
from .routers import dev, token, tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Mario API", version="0.1.0")
app.include_router(token.router)
app.include_router(tools.router)
app.include_router(dev.router)


@app.middleware("http")
async def revalidate(request: Request, call_next):
    # Browsers otherwise keep running old ES modules after an update.
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# The PWA is served from the same origin, so the phone needs only one address
# (`tailscale serve` → this app) and no CORS.
app.mount("/", StaticFiles(directory=ROOT_DIR / "frontend", html=True), name="frontend")
