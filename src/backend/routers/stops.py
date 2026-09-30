import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from ..database import engine
from ..models import Stop
from ..schemas import StopOut, StopPage


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/stops",
    tags=["stops"],
)


@router.get("", response_model=StopPage)
def list_stops(
    source: Annotated[
        Literal["T", "A"],
        Query(description="T — tramwaje, A — autobusy MPK"),
    ] = "T",
    q: Annotated[
        str | None,
        Query(max_length=100, description="Fragment nazwy przystanku"),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    offset: Annotated[
        int,
        Query(ge=0),
    ] = 0,
) -> StopPage:
    filters = [Stop.source == source]

    search = (q or "").strip()

    if search:
        filters.append(
            Stop.name.icontains(search, autoescape=True)
        )

    count_statement = (
        select(func.count())
        .select_from(Stop)
        .where(*filters)
    )

    items_statement = (
        select(
            Stop.source,
            Stop.stop_id,
            Stop.name,
            Stop.latitude,
            Stop.longitude,
        )
        .where(*filters)
        .order_by(Stop.name, Stop.stop_id)
        .limit(limit)
        .offset(offset)
    )

    try:
        with engine.connect() as connection:
            total = connection.execute(
                count_statement
            ).scalar_one()

            rows = connection.execute(
                items_statement
            ).mappings().all()
    except SQLAlchemyError:
        logger.exception("Failed to fetch stops")
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from None

    return StopPage(
        items=[
            StopOut.model_validate(dict(row))
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )