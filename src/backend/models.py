from datetime import date 
from sqlalchemy import Boolean, Date
from sqlalchemy import (
    CheckConstraint, 
    Float, 
    String, 
    Text, 
    Integer,
    ForeignKeyConstraint,
    Index,
    )
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


class Stop(Base):
    __tablename__ = "mpk_stops"

    __table_args__ = (
        CheckConstraint(
            "source IN ('A', 'T')",
            name="ck_mpk_stops_source",
        ),
        CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name="ck_mpk_stops_latitude",
        ),
        CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name="ck_mpk_stops_longitude",
        ),
    )

    source: Mapped[str] = mapped_column(String(1), primary_key=True)
    stop_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)


class Route(Base):
    __tablename__ = "mpk_routes"

    __table_args__ = (
        CheckConstraint(
            "source IN ('A', 'T')", 
            name="ck_mpk_routes_source",
        ),
        CheckConstraint(
            "route_type >= 0",
            name="ck_mpk_routes_type",
        ),
        CheckConstraint(
            "length(trim(short_name)) > 0 "
            "OR length(trim(long_name)) > 0",
            name="ck_mpk_routes_name",
        ),
    )

    source: Mapped[str] = mapped_column(String(1), primary_key=True)
    route_id: Mapped[str] = mapped_column(Text, primary_key=True)
    short_name: Mapped[str] = mapped_column(Text, server_default="")
    long_name: Mapped[str] = mapped_column(Text, server_default="")
    route_type: Mapped[int] = mapped_column(Integer)

class Trip(Base):
    __tablename__ = "mpk_trips"

    __table_args__ = (
        CheckConstraint(
            "source IN ('A', 'T')",
            name="ck_mpk_trips_source"
        ),
        CheckConstraint(
            "direction_id IS NULL OR direction_id IN (0, 1)",
            name="ck_mpk_trips_direction",
        ),
        ForeignKeyConstraint(
            ["source", "route_id"],
            ["mpk_routes.source", "mpk_routes.route_id"],
            name="fk_mpk_trips_route"
        ),
        Index(
            "ix_mpk_trips_source_route_service",
            "source",
            "route_id",
            "service_id"
        ),
    )

    source: Mapped[str] = mapped_column(String(1), primary_key=True)
    trip_id: Mapped[str] = mapped_column(Text, primary_key=True)
    route_id: Mapped[str] = mapped_column(Text)
    service_id: Mapped[str] = mapped_column(Text)
    headsign: Mapped[str] = mapped_column(Text, server_default="")
    direction_id: Mapped[int | None] = mapped_column(Integer)


class ServiceCalendar(Base):
    __tablename__ = "mpk_calendars"

    __table_args__ = (
        CheckConstraint(
            "source IN ('A', 'T')",
            name="ck_mpk_calendars_source",
        ),
        CheckConstraint(
            "end_date >= start_date",
            name="ck_mpk_calendars_date_range",
        ),
    )

    source: Mapped[str] = mapped_column(String(1), primary_key=True)
    service_id: Mapped[str] = mapped_column(Text, primary_key=True)

    monday: Mapped[bool] = mapped_column(Boolean)
    tuesday: Mapped[bool] = mapped_column(Boolean)
    wednesday: Mapped[bool] = mapped_column(Boolean)
    thursday: Mapped[bool] = mapped_column(Boolean)
    friday: Mapped[bool] = mapped_column(Boolean)
    saturday: Mapped[bool] = mapped_column(Boolean)
    sunday: Mapped[bool] = mapped_column(Boolean)

    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)


class CalendarDate(Base):
    __tablename__ = "mpk_calendar_dates"

    __table_args__ = (
        CheckConstraint(
            "source IN ('A', 'T')",
            name="ck_mpk_calendar_dates_source",
        ),
        CheckConstraint(
            "exception_type IN (1,2)",
            name="ck_mpk_calendar_dates_type",
        ),
    )

    source: Mapped[str] = mapped_column(String(1), primary_key=True)
    service_id: Mapped[str] = mapped_column(Text, primary_key=True)
    service_date: Mapped[date] = mapped_column(Date, primary_key=True)

    exception_type: Mapped[int] = mapped_column(Integer)


class StopTime(Base):
    __tablename__ = "mpk_stop_times"

    __table_args__ = (
        CheckConstraint(
            "source IN ('A', 'T')",
            name="ck_mpk_stop_times_source",
        ),
        CheckConstraint(
            "stop_sequence >= 0",
            name="ck_mpk_stop_times_sequence",
        ),
        CheckConstraint(
            "arrival_seconds >= 0",
            name="ck_mpk_stop_times_arrival",
        ),
        CheckConstraint(
            "departure_seconds >= 0",
            name="ck_mpk_stop_times_departure",
        ),
        CheckConstraint(
            "departure_seconds >= arrival_seconds",
            name="ck_mpk_stop_time_order",
        ),
        CheckConstraint(
            "pickup_type IN (0, 1, 2, 3)",
            name="ck_mpk_stop_times_drop_off",
        ),
        CheckConstraint(
            "timepoint IN (0, 1)",
            name="ck_mpk_stop_times_timepoint",
        ),
        ForeignKeyConstraint(
            ["source", "trip_id"],
            ["mpk_trips.source", "mpk_trips.trip_id"],
            name="fk_mpk_stop_times_stop",
        ),
        Index(
            "ix_mpk_stop_times_stop_departure",
            "source",
            "stop_id",
            "departure_seconds",
        ),
    )

    source: Mapped[str] = mapped_column(String(1), primary_key=True)
    trip_id: Mapped[str] = mapped_column(Text, primary_key=True)
    stop_sequence: Mapped[int] = mapped_column(Integer, primary_key=True)

    stop_id: Mapped[str] = mapped_column(Text)

    arrival_seconds: Mapped[int | None] = mapped_column(Integer)
    departure_seconds: Mapped[int | None] = mapped_column(Integer)

    stop_headsign: Mapped[str] = mapped_column(Text, server_default="")
    pickup_type: Mapped[int] = mapped_column(Integer, server_default="0")
    drop_off_type: Mapped[int] = mapped_column(Integer, server_default="0")
    timepoint: Mapped[int] = mapped_column(Integer, server_default="1")