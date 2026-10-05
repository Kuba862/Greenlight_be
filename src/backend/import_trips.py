import argparse 
import csv 
from io import BytesIO, TextIOWrapper 
from zipfile import ZipFile 

from sqlalchemy import select 
from sqlalchemy.dialects.postgresql import insert

from .database import engine 
from .import_stops import download_feed
from .models import Route, Trip 

BATCH_SIZE = 500
MAX_TRIPS_FILE_SIZE = 50 * 1024 * 1024

def read_trips(data: bytes | BytesIO, source: str) -> list[dict]:
    zip_data = BytesIO(data) if isinstance(data, bytes) else data 
    trips = []
    seen_ids = set()

    with ZipFile(zip_data) as archive:
        try:
            file_info = archive.getinfo("trips.txt")
        except KeyError:
            raise ValueError("Archive does not contain trips.txt") from None

        if file_info.file_size > MAX_TRIPS_FILE_SIZE:
            raise ValueError("trips.txt exceeds the size limit")

        with archive.open(file_info) as raw_file:
            with TextIOWrapper(
                raw_file,
                encoding="utf-8-sig",
                newline="",
            ) as text_file:
                reader = csv.DictReader(text_file)

                required_columns = { "trip_id", "route_id", "service_id" }
                available_columns = set(reader.fieldnames or [])
                missing_columns = required_columns - available_columns 

                if missing_columns:
                    raise ValueError(
                        "Missing columns in trips.txt: "
                        + ", ".join(sorted(missing_columns))
                    )

                for row_number, row in enumerate(reader, start=2):
                    trip_id = (row.get("trip_id") or "").strip()
                    route_id = (row.get("route_id") or "").strip()
                    service_id = (row.get("service_id") or "").strip()
                    headsign = (row.get("trip_headsign") or "").strip()
                    raw_direction = (row.get("direction_id") or "").strip()

                    if not trip_id or not route_id or not service_id:
                        raise ValueError(
                            f"Row {row_number}: "
                            "trip_id, route_id and service_id are required"
                        )

                    if trip_id in seen_ids:
                        raise ValueError(
                            f"Row {row_number}: duplicate trip_id {trip_id}"
                        )

                    if raw_direction not in {"", "0", "1"}:
                        raise ValueError(
                            f"Row {row_number}: "
                            f"invalie direction_id {raw_direction!r}"
                        )

                    direction_id = (
                        int(raw_direction) if raw_direction else None
                    )

                    seen_ids.add(trip_id)

                    trips.append(
                        {
                            "source": source,
                            "trip_id": trip_id,
                            "route_id": route_id,
                            "service_id": service_id,
                            "headsign": headsign,
                            "direction_id": direction_id,
                        }
                    )

        if not trips:
            raise ValueError("trips.txt contains no trips")

        return trips


def save_trips(trips: list[dict], source: str) -> None:
    with engine.begin() as connection:
        known_route_ids = set(
            connection.execute(
                select(Route.route_id).where(Route.source == source)
            ).scalars()
        )

        required_route_ids = { trip["route_id"] for trip in trips }
        missing_route_ids = required_route_ids - known_route_ids

        if missing_route_ids:
            examples = ", ".join(sorted(missing_route_ids)[:10])

            raise ValueError(
                f"Source {source}: "
                f"{len(missing_route_ids)} routes are missing from the database. "
                f"Examples: {examples}. "
                "Run import_routes for this source first."
            )

        for start in range(0, len(trips), BATCH_SIZE):
            batch = trips[start : start + BATCH_SIZE]

            statement = insert(Trip).values(batch)

            statement = statement.on_conflict_do_update(
                index_elements=["source", "trip_id"],
                set_={
                    "route_id": statement.excluded.route_id,
                    "service_id": statement.excluded.service_id,
                    "headsign": statement.excluded.headsign,
                    "direction_id": statement.excluded.direction_id,
                },
            )

            connection.execute(statement)

def main() -> None:
    parser = argparse.ArgumentParser(description="Import MPK trips from GTFS")
    parser.add_argument("source", choices=["T", "A"])
    args = parser.parse_args()

    try:
        data = download_feed(args.source)
        trips = read_trips(data, args.source)
        save_trips(trips, args.source)

        print(
            f"Import completed. Source: {args.source}, "
            f"Trips processed: {len(trips)}."
        )
    finally:
        engine.dispose()

if __name__ == "__main__":
    main()