from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import BigInteger, Date, and_, cast, func, literal, or_, select, union_all
from sqlalchemy.exc import SQLAlchemyError

from ..database import engine
from ..models import CalendarDate, Route, ServiceCalendar, Stop, StopTime, Trip
from ..schemas import StopOut


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/stops", tags=["departures"])
WARSAW = ZoneInfo("Europe/Warsaw")
WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)


class DepartureOut(BaseModel):
    trip_id: str
    route_id: str
    line: str
    headsign: str
    service_date: date
    stop_sequence: int
    scheduled_departure: AwareDatetime
    seconds_until_departure: int
    pickup_type: Literal[0, 2, 3]
    is_approximate: bool


class DeparturePage(BaseModel):
    stop: StopOut
    items: list[DepartureOut]
    count: int
    limit: int
    has_more: bool
    from_time: AwareDatetime
    until_time: AwareDatetime
    window_minutes: int
    timezone: Literal["Europe/Warsaw"] = "Europe/Warsaw"
    data_source: Literal["schedule"] = "schedule"


def service_day_start(service_date: date) -> datetime:
    noon = datetime.combine(service_date, time(12), tzinfo=WARSAW)
    return noon.astimezone(UTC) - timedelta(hours=12)


def active_service_condition(service_date: date):
    weekday = getattr(ServiceCalendar, WEEKDAYS[service_date.weekday()])
    weekly = (
        select(1)
        .select_from(ServiceCalendar)
        .where(
            ServiceCalendar.source == Trip.source,
            ServiceCalendar.service_id == Trip.service_id,
            ServiceCalendar.start_date <= service_date,
            ServiceCalendar.end_date >= service_date,
            weekday.is_(True),
        )
        .correlate(Trip)
        .exists()
    )

    def exception(kind: int):
        return (
            select(1)
            .select_from(CalendarDate)
            .where(
                CalendarDate.source == Trip.source,
                CalendarDate.service_id == Trip.service_id,
                CalendarDate.service_date == service_date,
                CalendarDate.exception_type == kind,
            )
            .correlate(Trip)
            .exists()
        )

    return and_(or_(weekly, exception(1)), ~exception(2))


def build_departures_query(
    source: str,
    stop_id: str,
    from_utc: datetime,
    until_utc: datetime,
    minimum_seconds: int,
    maximum_seconds: int,
    limit: int,
):

    first_date = (
        (from_utc - timedelta(seconds=maximum_seconds)).astimezone(WARSAW).date()
        - timedelta(days=1)
    )
    last_date = (
        (until_utc - timedelta(seconds=minimum_seconds)).astimezone(WARSAW).date()
        + timedelta(days=1)
    )

    queries = []
    service_date = first_date
    while service_date <= last_date:
        start = service_day_start(service_date)
        lower = max(0, ceil((from_utc - start).total_seconds()))
        upper = ceil((until_utc - start).total_seconds())

        if upper > lower and lower <= maximum_seconds and upper > minimum_seconds:
            query = (
                select(
                    Trip.trip_id,
                    Trip.route_id,
                    func.coalesce(
                        func.nullif(Route.short_name, ""),
                        func.nullif(Route.long_name, ""),
                        Route.route_id,
                    ).label("line"),
                    func.coalesce(
                        func.nullif(StopTime.stop_headsign, ""), Trip.headsign,
                    ).label("headsign"),
                    literal(service_date, type_=Date()).label("service_date"),
                    StopTime.stop_sequence,
                    (
                        cast(literal(int(start.timestamp())), BigInteger())
                        + StopTime.departure_seconds
                    ).label("departure_timestamp"),
                    StopTime.pickup_type,
                    StopTime.timepoint,
                )
                .select_from(StopTime)
                .join(
                    Trip,
                    and_(Trip.source == StopTime.source, Trip.trip_id == StopTime.trip_id),
                )
                .join(
                    Route,
                    and_(Route.source == Trip.source, Route.route_id == Trip.route_id),
                )
                .where(
                    StopTime.source == source,
                    StopTime.stop_id == stop_id,
                    StopTime.pickup_type != 1,
                    StopTime.departure_seconds >= lower,
                    StopTime.departure_seconds < upper,
                    active_service_condition(service_date),
                )
                .order_by(
                    StopTime.departure_seconds, Trip.trip_id, StopTime.stop_sequence,
                )
                .limit(limit + 1)
            )
            queries.append(select(query.subquery()))
        service_date += timedelta(days=1)

    if not queries:
        return None
    combined = union_all(*queries).subquery()
    return (
        select(combined)
        .order_by(
            combined.c.departure_timestamp, combined.c.trip_id,
            combined.c.stop_sequence, combined.c.service_date,
        )
        .limit(limit + 1)
    )


def load_departures(
    connection,
    source: str,
    stop_id: str,
    from_utc: datetime,
    until_utc: datetime,
    limit: int,
) -> tuple[dict, list[dict]]:
    stop = connection.execute(
        select(Stop.source, Stop.stop_id, Stop.name, Stop.latitude, Stop.longitude)
        .where(Stop.source == source, Stop.stop_id == stop_id)
    ).mappings().one_or_none()
    if stop is None:
        raise HTTPException(status_code=404, detail="Stop not found")

    minimum, maximum = connection.execute(
        select(func.min(StopTime.departure_seconds), func.max(StopTime.departure_seconds))
        .where(
            StopTime.source == source,
            StopTime.stop_id == stop_id,
            StopTime.pickup_type != 1,
        )
    ).one()

    if maximum is None:
        imported = connection.execute(
            select(StopTime.source).where(StopTime.source == source).limit(1)
        ).first()
        if imported is None:
            raise HTTPException(status_code=503, detail="Timetable unavailable for this source")
        return dict(stop), []

    try:
        query = build_departures_query(
            source, stop_id, from_utc, until_utc, minimum, maximum, limit,
        )
    except (OverflowError, ValueError):
        raise HTTPException(status_code=422, detail="Requested time is out of range") from None

    rows = connection.execute(query).mappings().all() if query is not None else []
    return dict(stop), [dict(row) for row in rows]


@router.get("/{stop_id}/departures", response_model=DeparturePage)
def get_departures(
    stop_id: Annotated[str, Path(min_length=1, max_length=200)],
    source: Annotated[Literal["T", "A"], Query()] = "T",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    window_minutes: Annotated[int, Query(ge=1, le=1440)] = 120,
    at: Annotated[
        AwareDatetime | None,
        Query(description="Optional start time with UTC offset, e.g. 2026-10-06T12:00:00+02:00"),
    ] = None,
) -> DeparturePage:
    try:
        from_utc = (at or datetime.now(UTC)).astimezone(UTC)
        until_utc = from_utc + timedelta(minutes=window_minutes)
        from_local = from_utc.astimezone(WARSAW)
        until_local = until_utc.astimezone(WARSAW)
    except OverflowError:
        raise HTTPException(status_code=422, detail="Requested time is out of range") from None

    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            stop, rows = load_departures(
                connection, source, stop_id, from_utc, until_utc, limit,
            )
    except SQLAlchemyError:
        logger.exception("Cannot load departures: source=%s stop_id=%r", source, stop_id)
        raise HTTPException(status_code=503, detail="Database unavailable") from None

    items = []
    for row in rows[:limit]:
        departure_utc = datetime.fromtimestamp(row["departure_timestamp"], UTC)
        items.append(DepartureOut(
            trip_id=row["trip_id"],
            route_id=row["route_id"],
            line=row["line"],
            headsign=row["headsign"],
            service_date=row["service_date"],
            stop_sequence=row["stop_sequence"],
            scheduled_departure=departure_utc.astimezone(WARSAW),
            seconds_until_departure=ceil((departure_utc - from_utc).total_seconds()),
            pickup_type=row["pickup_type"],
            is_approximate=row["timepoint"] == 0,
        ))

    return DeparturePage(
        stop=StopOut.model_validate(stop),
        items=items,
        count=len(items),
        limit=limit,
        has_more=len(rows) > limit,
        from_time=from_local,
        until_time=until_local,
        window_minutes=window_minutes,
    )
