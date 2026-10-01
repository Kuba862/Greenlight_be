import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Numeric, case, cast, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from ..database import engine
from ..models import Route
from ..schemas import RouteOut, RoutePage


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/routes",
    tags=["routes"],
)


@router.get("", response_model=RoutePage)
def list_routes(
    source: Annotated[
        Literal["T", "A"],
        Query(description="T — tramwaje, A — autobusy MPK"),
    ] = "T",
    q: Annotated[
        str | None,
        Query(
            max_length=100,
            description="Fragment oznaczenia lub nazwy linii",
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    offset: Annotated[
        int,
        Query(ge=0),
    ] = 0,
) -> RoutePage:
    filters = [Route.source == source]

    search = (q or "").strip()

    if search:
        filters.append(
            or_(
                Route.short_name.icontains(search, autoescape=True),
                Route.long_name.icontains(search, autoescape=True),
            )
        )

    count_statement = (
        select(func.count())
        .select_from(Route)
        .where(*filters)
    )

    numeric_name = case(
        (
            Route.short_name.regexp_match(r"^[0-9]+$"),
            cast(Route.short_name, Numeric),
        ),
        else_=None,
    )

    items_statement = (
        select(
            Route.source,
            Route.route_id,
            Route.short_name,
            Route.long_name,
            Route.route_type,
        )
        .where(*filters)
        .order_by(
            numeric_name.asc().nulls_last(),
            Route.short_name,
            Route.long_name,
            Route.route_id,
        )
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
        logger.exception("Failed to fetch routes")
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from None

    return RoutePage(
        items=[
            RouteOut.model_validate(dict(row))
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )