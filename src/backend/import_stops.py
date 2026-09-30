import argparse
import csv
from io import BytesIO, StringIO
from zipfile import ZipFile

import httpx
from sqlalchemy.dialects.postgresql import insert

from .database import engine
from .models import Stop

FEED_URLS = {
    "T": "https://gtfs.ztp.krakow.pl/GTFS_KRK_T.zip",
    "A": "https://gtfs.ztp.krakow.pl/GTFS_KRK_A.zip",
}

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_STOPS_BYTES = 10 * 1024 * 1024
BATCH_SIZE = 500


def download_feed(source: str) -> bytes:
    url = FEED_URLS[source]
    buffer = BytesIO()

    print(f"Downloading: {url}")

    with httpx.stream(
        "GET",
        url,
        timeout=60.0,
        follow_redirects=True,
    ) as response:
        response.raise_for_status()

        for chunk in response.iter_bytes():
            if buffer.tell() + len(chunk) > MAX_ARCHIVE_BYTES:
                raise ValueError("The GTFS archive exceeds the 100 MB limit")

            buffer.write(chunk)

    return buffer.getvalue()



def parse_stops(
        archive_bytes: bytes,
        source: str,
) -> list[dict[str, str | float]]:
    with ZipFile(BytesIO(archive_bytes)) as archive:
        info = archive.getinfo("stops.txt")

        if info.file_size > MAX_ARCHIVE_BYTES:
            raise ValueError("The stops.txt file exceeds the 10 MB limit.")

        content = archive.read(info).decode("utf-8-sig")

    reader = csv.DictReader(StringIO(content, newline=""))

    required_columns = {
        "stop_id",
        "stop_name",
        "stop_lat",
        "stop_lon"
    }

    if not required_columns.issubset(reader.fieldnames or []):
        raise ValueError("Missing required columns in stops.txt")

    stops: list[dict[str, str | float]] = []
    seen_ids: set[str] = set()

    for row in reader:
        location_type = (row.get("location_type") or "0").strip()

        if location_type not in ("", "0"):
            continue

        stop_id = (row.get("stop_id") or "").strip()
        name = (row.get("stop_name") or "").strip()

        if not stop_id or not name:
            raise ValueError(f"Missing stop ID or name, route {reader.line_num}.")

        if stop_id in seen_ids:
            raise ValueError(f"Repeated stop_id: {stop_id}")


        try:
            latitude = float(row.get("stop_lat") or "")
            longitude = float(row.get("stop_lon") or "")
        except ValueError as error:
            raise ValueError(f"Invalid stop {stop_id} coordinates") from error

        if not (
            -90 <= latitude <= 90
            and -180 <= longitude <= 180
        ):
            raise ValueError(f"Coordinates out of range for the stop {stop_id}")

        seen_ids.add(stop_id)

        stops.append(
            {
                "source": source,
                "stop_id": stop_id,
                "name": name,
                "latitude": latitude,
                "longitude": longitude,
            }
        )

    if not stops:
        raise ValueError("The GTFS file does not contain stops for import")
    
    return stops


def save_stops(stops: list[dict[str, str | float]]) -> None:
    with engine.begin() as connection:
        for offset in range(0, len(stops), BATCH_SIZE):
            batch = stops[offset : offset + BATCH_SIZE]
            statement = insert(Stop).values(batch)

            statement = statement.on_conflict_do_update(
                index_elements=["source", "stop_id"],
                set_={
                    "name": statement.excluded.name,
                    "latitude": statement.excluded.latitude,
                    "longitude": statement.excluded.longitude
                },
            )

            connection.execute(statement)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importing MPK Krakow stops from GTFS"
    )
    parser.add_argument(
        "source",
        choices=["T", "A"],
        help="T - tramwaje, A - autobusy MPK",
    )
    args = parser.parse_args()

    try:
        archive_bytes = download_feed(args.source)
        stops = parse_stops(archive_bytes, args.source)
        save_stops(stops)

        print(
            f"Import completed. "
            f"Source: {args.source}. "
            f"Stops processed: {len(stops)}."
        )
    finally:
        engine.dispose()



if __name__ == "__main__":
    main()