from fastapi import FastAPI
from .config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="API dojazdów komunikacją MPK w Krakowie.",
)

@app.get("/", tags=["health"])
def root() -> dict[str, str]:
    return { "status": "ok" }


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    return { "status": "ok" }