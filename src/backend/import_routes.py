import argparse
import csv
from io import BytesIO, StringIO
from zipfile import ZipFile

from sqlalchemy.dialects.postgresql import insert

from .database import engine
from .import_stops import download_feed
from .models import Route

MAX_ROUTES_BYTES = 5 * 1024 * 1024
BATCH_SIZE = 500

def parse_routes(
        archive_bytes: bytes,
        source: str,
) -> list[dict[str, str | int]]:
    with ZipFile(BytesIO(archive_bytes)) as archive:
        info = archive.getinfo("routes.txt")

        if info.file_size > MAX_ROUTES_BYTES:
            raise ValueError("routes.txt exceeds the 5 MB limit")

        content = archive.read(info).decode("utf-8-sig")

    reader = csv.DictReader(StringIO(content, newline=""))
    columns = set(reader.fieldnames or [])


    if not { "route_id", "route_type" }.issubset(columns):
        raise ValueError("Missing required columns in routes.txt")

    if not { "route_short_name", "route_long_name" }.intersection(columns):
        raise ValueError("Missing route name columns in routes.txt")


    routes: list[dict[str, str | int]] = []
    seen_ids: set[str] = set()

    for row in reader:
        route_id = (row.get("route_id") or "").strip()
        short_name = (row.get("route_short_name") or "").strip()
        long_name = (row.get("route_long_name") or "").strip()

        if not route_id:
            raise ValueError(f"Missing route_id at line {reader.line_num}")

        if route_id in seen_ids:
            raise ValueError(f"Duplicate route_id: {route_id}")

        if not short_name and not long_name:
            raise ValueError(f"Missing name for route {route_id}")


        try:
            route_type = int(row.get("route_type") or "")
        except ValueError as error:
            raise ValueError(f"Invalid route_type for route {route_id}") from error

        if route_type < 0:
            raise ValueError(f"Negative route_type for route {route_id}")

        seen_ids.add(route_id)

        routes.append(
            {
                "source": source,
                "route_id": route_id,
                "short_name": short_name,
                "long_name": long_name,
                "route_type": route_type,
            }
        )

    if not routes:
        raise ValueError("No routes found in the GTFS archive")

    return routes


def save_routes(routes: list[dict[str, str | int]]) -> None:
    with engine.begin() as connection:
        for offset in range(0, len(routes), BATCH_SIZE):
            batch = routes[offset : offset + BATCH_SIZE]
            statement = insert(Route).values(batch)

            statement = statement.on_conflict_do_update(
                index_elements=["source", "route_id"],
                set_={
                    "short_name": statement.excluded.short_name,
                    "long_name": statement.excluded.long_name,
                    "route_type": statement.excluded.route_type,
                },
            )

            connection.execute(statement)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import MPK Krakow routes from GTFS"
    )
    parser.add_argument(
        "source",
        choices=["T", "A"],
        help="T - trams, A - MPK buses",
    )
    args = parser.parse_args()


    try:
        archive_bytes = download_feed(args.source)
        routes = parse_routes(archive_bytes, args.source)
        save_routes(routes)

        print(
            f"Import completed. Source: {args.source} "
            f"Routes processed: {len(routes)}"
        )

    finally:
        engine.dispose()


if __name__ == "__main__":
    main()