from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO, TextIOWrapper
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from sqlalchemy import create_engine, delete, func, insert, select, text
from sqlalchemy.pool import NullPool

from .config import get_settings
from .database import database_url
from .import_calendars import read_calendars, read_csv_rows
from .import_stops import download_feed
from .import_trips import read_trips
from .models import CalendarDate, Route, ServiceCalendar, Stop, StopTime, Trip


MAX_STOP_TIMES_SIZE = 512 * 1024 * 1024
MAX_INTEGER = 2_147_483_647
BATCH_SIZE = 500
STOP_TIME_COLUMNS = (
    "source", "trip_id", "stop_sequence", "stop_id",
    "arrival_seconds", "departure_seconds", "stop_headsign",
    "pickup_type", "drop_off_type", "timepoint",
)
TIME_PATTERN = re.compile(r"([0-9]+):([0-5][0-9]):([0-5][0-9])")


@dataclass
class Feed:
    source: str
    stops: list[dict]
    routes: list[dict]
    trips: list[dict]
    calendars: list[dict]
    calendar_dates: list[dict]
    stop_times_path: Path
    stop_times_count: int


def parse_nonnegative_integer(value: str, context: str) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError(f"{context}: expected a non-negative integer, got {value!r}")
    result = int(value)
    if result > MAX_INTEGER:
        raise ValueError(f"{context}: value exceeds the database integer range")
    return result


def parse_time(value: str, context: str) -> int | None:
    if not value:
        return None
    match = TIME_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"{context}: invalid GTFS time {value!r}")
    hours, minutes, seconds = map(int, match.groups())
    result = hours * 3600 + minutes * 60 + seconds
    if result > MAX_INTEGER:
        raise ValueError(f"{context}: time exceeds the database integer range")
    return result


def parse_enum(value: str, allowed: set[str], default: str, context: str) -> int:
    value = value or default
    if value not in allowed:
        raise ValueError(f"{context}: unsupported value {value!r}")
    return int(value)


def read_stops_and_routes(data: bytes, source: str) -> tuple[list[dict], list[dict]]:
    with ZipFile(BytesIO(data)) as archive:
        stop_rows = read_csv_rows(
            archive, "stops.txt", {"stop_id", "stop_name", "stop_lat", "stop_lon"}
        )
        route_rows = read_csv_rows(archive, "routes.txt", {"route_id", "route_type"})

    stops = []
    stop_ids = set()
    for number, row in enumerate(stop_rows, start=2):
        if row.get("location_type", "") not in {"", "0"}:
            continue
        stop_id, name = row["stop_id"], row["stop_name"]
        if not stop_id or not name:
            raise ValueError(f"stops.txt, row {number}: stop_id and stop_name required")
        if stop_id in stop_ids:
            raise ValueError(f"stops.txt, row {number}: duplicate stop_id {stop_id}")
        try:
            latitude = float(row["stop_lat"])
            longitude = float(row["stop_lon"])
        except ValueError:
            raise ValueError(f"stops.txt, row {number}: invalid coordinates") from None
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError(f"stops.txt, row {number}: coordinates out of range")
        stops.append({
            "source": source, "stop_id": stop_id, "name": name,
            "latitude": latitude, "longitude": longitude,
        })
        stop_ids.add(stop_id)

    routes = []
    route_ids = set()
    for number, row in enumerate(route_rows, start=2):
        route_id = row["route_id"]
        short_name = row.get("route_short_name", "")
        long_name = row.get("route_long_name", "")
        if not route_id or not (short_name or long_name):
            raise ValueError(f"routes.txt, row {number}: route_id and a name required")
        if route_id in route_ids:
            raise ValueError(f"routes.txt, row {number}: duplicate route_id {route_id}")
        routes.append({
            "source": source, "route_id": route_id,
            "short_name": short_name, "long_name": long_name,
            "route_type": parse_nonnegative_integer(
                row["route_type"], f"routes.txt, row {number}, route_type"
            ),
        })
        route_ids.add(route_id)

    if not stops or not routes:
        raise ValueError("Archive must contain stops and routes")
    return stops, routes


def prepare_stop_times(
    data: bytes,
    source: str,
    trip_ids: set[str],
    stop_ids: set[str],
    destination: Path,
) -> int:
    """Validate the large CSV and spool normalized rows to a temporary file."""
    sequences: dict[str, set[int]] = defaultdict(set)
    endpoints: dict[str, tuple[int, bool, int, bool]] = {}
    required = {"trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"}
    count = 0

    with ZipFile(BytesIO(data)) as archive:
        try:
            info = archive.getinfo("stop_times.txt")
        except KeyError:
            raise ValueError("Archive does not contain stop_times.txt") from None
        if info.file_size > MAX_STOP_TIMES_SIZE:
            raise ValueError("stop_times.txt exceeds the size limit")

        with archive.open(info) as raw_file:
            with TextIOWrapper(raw_file, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                columns = reader.fieldnames or []
                missing = required - set(columns)
                if missing:
                    raise ValueError(f"stop_times.txt: missing columns {sorted(missing)}")
                if len(columns) != len(set(columns)):
                    raise ValueError("stop_times.txt: duplicate column names")

                with destination.open("w", encoding="utf-8", newline="") as output:
                    # Unquoted empty fields represent SQL NULL; quoted "" is text.
                    writer = csv.writer(output, quoting=csv.QUOTE_NOTNULL)
                    for number, raw in enumerate(reader, start=2):
                        context = f"stop_times.txt, row {number}"
                        if None in raw or any(value is None for value in raw.values()):
                            raise ValueError(f"{context}: incorrect number of fields")
                        row = {key: value.strip() for key, value in raw.items()}
                        if any("\x00" in value for value in row.values()):
                            raise ValueError(f"{context}: NUL characters are not supported")
                        trip_id, stop_id = row["trip_id"], row["stop_id"]
                        if trip_id not in trip_ids:
                            raise ValueError(f"{context}: unknown trip_id {trip_id!r}")
                        if stop_id not in stop_ids:
                            raise ValueError(f"{context}: unknown stop_id {stop_id!r}")
                        sequence = parse_nonnegative_integer(
                            row["stop_sequence"], f"{context}, stop_sequence"
                        )
                        if sequence in sequences[trip_id]:
                            raise ValueError(f"{context}: duplicate trip_id/stop_sequence")
                        sequences[trip_id].add(sequence)

                        arrival = parse_time(row["arrival_time"], f"{context}, arrival_time")
                        departure = parse_time(row["departure_time"], f"{context}, departure_time")
                        if arrival is not None and departure is not None and departure < arrival:
                            raise ValueError(f"{context}: departure precedes arrival")
                        timepoint = parse_enum(
                            row.get("timepoint", ""), {"0", "1"}, "1", f"{context}, timepoint"
                        )
                        if timepoint == 1 and (arrival is None or departure is None):
                            raise ValueError(f"{context}: timepoint=1 requires both times")
                        pickup = parse_enum(
                            row.get("pickup_type", ""), {"0", "1", "2", "3"},
                            "0", f"{context}, pickup_type",
                        )
                        drop_off = parse_enum(
                            row.get("drop_off_type", ""), {"0", "1", "2", "3"},
                            "0", f"{context}, drop_off_type",
                        )
                        has_arrival = arrival is not None
                        first, first_ok, last, last_ok = endpoints.get(
                            trip_id, (sequence, has_arrival, sequence, has_arrival)
                        )
                        if sequence < first:
                            first, first_ok = sequence, has_arrival
                        if sequence > last:
                            last, last_ok = sequence, has_arrival
                        endpoints[trip_id] = (first, first_ok, last, last_ok)
                        writer.writerow((
                            source, trip_id, sequence, stop_id, arrival, departure,
                            row.get("stop_headsign", ""), pickup, drop_off, timepoint,
                        ))
                        count += 1
                        if count % 100_000 == 0:
                            print(f"Validated stop times: {count}", flush=True)

    if not count:
        raise ValueError("stop_times.txt contains no rows")
    missing_trips = trip_ids - sequences.keys()
    if missing_trips:
        raise ValueError(f"Trips without stop times: {sorted(missing_trips)[:10]}")
    for trip_id, (_, first_ok, _, last_ok) in endpoints.items():
        if not first_ok or not last_ok:
            raise ValueError(f"Trip {trip_id}: first and last stop require arrival times")
    return count


def prepare_feed(data: bytes | BytesIO, source: str, destination: Path) -> Feed:
    if source not in {"T", "A"}:
        raise ValueError("Source must be T or A")
    if not isinstance(data, bytes):
        data = data.getvalue()
    stops, routes = read_stops_and_routes(data, source)
    trips = read_trips(data, source)
    calendars, calendar_dates = read_calendars(data, source)

    route_ids = {row["route_id"] for row in routes}
    services = {row["service_id"] for row in calendars + calendar_dates}
    for trip in trips:
        if trip["route_id"] not in route_ids:
            raise ValueError(f"Trip {trip['trip_id']}: route missing from this archive")
        if trip["service_id"] not in services:
            raise ValueError(f"Trip {trip['trip_id']}: calendar missing from this archive")

    count = prepare_stop_times(
        data, source, {row["trip_id"] for row in trips},
        {row["stop_id"] for row in stops}, destination,
    )
    return Feed(source, stops, routes, trips, calendars, calendar_dates, destination, count)


def copy_stop_times(connection, path: Path) -> None:
    """Use COPY on the same transaction owned by the SQLAlchemy connection."""
    driver_connection = connection.connection.driver_connection
    columns = ", ".join(STOP_TIME_COLUMNS)
    statement = f"COPY public.mpk_stop_times ({columns}) FROM STDIN (FORMAT CSV)"
    with driver_connection.cursor() as cursor:
        with cursor.copy(statement) as copy:
            with path.open("rb") as source:
                while block := source.read(1024 * 1024):
                    copy.write(block)


def replace_feed(connection, feed: Feed) -> None:
    for model in (StopTime, Trip, CalendarDate, ServiceCalendar, Route, Stop):
        connection.execute(delete(model).where(model.source == feed.source))

    for model, rows in (
        (Stop, feed.stops), (Route, feed.routes),
        (ServiceCalendar, feed.calendars), (CalendarDate, feed.calendar_dates),
        (Trip, feed.trips),
    ):
        for start in range(0, len(rows), BATCH_SIZE):
            connection.execute(insert(model), rows[start : start + BATCH_SIZE])

    print(f"Saving {feed.stop_times_count} stop times with COPY...", flush=True)
    copy_stop_times(connection, feed.stop_times_path)
    actual = connection.execute(
        select(func.count()).select_from(StopTime).where(StopTime.source == feed.source)
    ).scalar_one()
    if actual != feed.stop_times_count:
        raise ValueError(f"Stop times count mismatch: expected {feed.stop_times_count}, got {actual}")


def save_feed(feed: Feed) -> tuple[str, str]:
    settings = get_settings()
    import_engine = create_engine(
        database_url.set(port=settings.db_migration_port),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "sslmode": "require", "connect_timeout": 10, "prepare_threshold": None,
        },
    )
    try:
        with import_engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout = '300s'"))
            connection.execute(text("SET LOCAL lock_timeout = '10s'"))
            locked = connection.execute(
                text("SELECT pg_try_advisory_xact_lock(:namespace, :source_key)"),
                {"namespace": 19780306, "source_key": ord(feed.source)},
            ).scalar_one()
            if not locked:
                raise ValueError(f"Another import for source {feed.source} is running")
            replace_feed(connection, feed)
            database_size = connection.execute(
                text("SELECT pg_size_pretty(pg_database_size(current_database()))")
            ).scalar_one()
            stop_times_size = connection.execute(
                text("SELECT pg_size_pretty(pg_total_relation_size('public.mpk_stop_times'))")
            ).scalar_one()
        return database_size, stop_times_size
    finally:
        import_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and import one complete MPK GTFS source")
    parser.add_argument("source", choices=["T", "A"])
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing to the database")
    args = parser.parse_args()

    data = download_feed(args.source)
    with TemporaryDirectory(prefix="mpk-gtfs-") as temporary:
        feed = prepare_feed(data, args.source, Path(temporary) / "stop_times.csv")
        print(
            f"Validated source {feed.source}: stops={len(feed.stops)}, "
            f"routes={len(feed.routes)}, trips={len(feed.trips)}, "
            f"calendars={len(feed.calendars)}, calendar_dates={len(feed.calendar_dates)}, "
            f"stop_times={feed.stop_times_count}",
            flush=True,
        )
        if args.dry_run:
            print("Dry run completed. Database unchanged.")
            return

        database_size, stop_times_size = save_feed(feed)
        print(f"Import completed. Source: {feed.source}. Stop times: {feed.stop_times_count}.")
        print(f"Database size: {database_size}. Stop times including indexes: {stop_times_size}.")


if __name__ == "__main__":
    main()
