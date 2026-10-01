import logging
from fastapi import FastAPI, HTTPException

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings
from .database import engine
from .routers.stops import router as stops_router
from .routers.routes import router as routes_router

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="API dojazdów komunikacją MPK w Krakowie.",
)

app.include_router(stops_router)
app.include_router(routes_router)

@app.get("/", tags=["health"])
def root() -> dict[str, str]:
    return { "status": "ok" }


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    return { "status": "ok" }


@app.get("/health/ready", tags=["health"])
def health_ready() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Database readiness check failed")
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from None


    return {
        "status": "ok",
        "database": "ok",
    }