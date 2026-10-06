import argparse
import csv
from datetime import date, datetime
from io import BytesIO, TextIOWrapper
from zipfile import ZipFile

from sqlalchemy import delete, insert

from .database import engine
from .import_stops import download_feed
from .models import CalendarDate, ServiceCalendar


BATCH_SIZE = 500
MAX_CALENDAR_FILE_SIZE = 10 * 1024 * 1024
WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)


def read_csv_rows(
    archive: ZipFile,
    filename: str,
    required_columns: set[str],
) -> list[dict[str, str]]:
    """Either calendar file may be absent, but a present file must be valid."""
    try:
        file_info = archive.getinfo(filename)
    except KeyError:
        return []

    if file_info.file_size > MAX_CALENDAR_FILE_SIZE:
        raise ValueError(f"{filename} exceeds the size limit")

    rows = []
    with archive.open(file_info) as raw_file:
        with TextIOWrapper(raw_file, encoding="utf-8-sig", newline="") as text_file:
            reader = csv.DictReader(text_file)
            columns = reader.fieldnames or []
            missing = required_columns - set(columns)

            if missing:
                raise ValueError(
                    f"{filename}: missing columns: {', '.join(sorted(missing))}"
                )
            if len(columns) != len(set(columns)):
                raise ValueError(f"{filename}: duplicate column names")

            for row_number, row in enumerate(reader, start=2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(
                        f"{filename}, row {row_number}: incorrect number of fields"
                    )
                rows.append({key: value.strip() for key, value in row.items()})

    return rows


def parse_date(value: str, context: str) -> date:
    try:
        if len(value) != 8 or not value.isascii() or not value.isdigit():
            raise ValueError
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ValueError(
            f"{context}: invalid date {value!r}; expected YYYYMMDD"
        ) from None


def read_calendars(
    data: bytes | BytesIO,
    source: str,
) -> tuple[list[dict], list[dict]]:
    if source not in {"T", "A"}:
        raise ValueError("Source must be T or A")

    zip_data = BytesIO(data) if isinstance(data, bytes) else data
    with ZipFile(zip_data) as archive:
        calendar_rows = read_csv_rows(
            archive,
            "calendar.txt",
            {"service_id", "start_date", "end_date", *WEEKDAYS},
        )
        exception_rows = read_csv_rows(
            archive,
            "calendar_dates.txt",
            {"service_id", "date", "exception_type"},
        )

    calendars = []
    seen_services = set()

    for row_number, row in enumerate(calendar_rows, start=2):
        context = f"calendar.txt, row {row_number}"
        service_id = row["service_id"]

        if not service_id:
            raise ValueError(f"{context}: service_id is required")
        if service_id in seen_services:
            raise ValueError(f"{context}: duplicate service_id {service_id}")

        start_date = parse_date(row["start_date"], f"{context}, start_date")
        end_date = parse_date(row["end_date"], f"{context}, end_date")
        if end_date < start_date:
            raise ValueError(f"{context}: end_date precedes start_date")

        for weekday in WEEKDAYS:
            if row[weekday] not in {"0", "1"}:
                raise ValueError(f"{context}: {weekday} must be 0 or 1")

        calendars.append(
            {
                "source": source,
                "service_id": service_id,
                "start_date": start_date,
                "end_date": end_date,
                **{weekday: row[weekday] == "1" for weekday in WEEKDAYS},
            }
        )
        seen_services.add(service_id)

    calendar_dates = []
    seen_dates = set()

    for row_number, row in enumerate(exception_rows, start=2):
        context = f"calendar_dates.txt, row {row_number}"
        service_id = row["service_id"]

        if not service_id:
            raise ValueError(f"{context}: service_id is required")
        if row["exception_type"] not in {"1", "2"}:
            raise ValueError(f"{context}: exception_type must be 1 or 2")

        service_date = parse_date(row["date"], context)
        key = (service_id, service_date)
        if key in seen_dates:
            raise ValueError(f"{context}: duplicate service_id and date: {key}")

        calendar_dates.append(
            {
                "source": source,
                "service_id": service_id,
                "service_date": service_date,
                "exception_type": int(row["exception_type"]),
            }
        )
        seen_dates.add(key)

    if not calendars and not calendar_dates:
        raise ValueError("Archive contains no calendar data")

    return calendars, calendar_dates


def save_calendars(
    calendars: list[dict],
    calendar_dates: list[dict],
    source: str,
) -> None:
    """Replace both calendar tables for one source in a single transaction."""
    if source not in {"T", "A"}:
        raise ValueError("Source must be T or A")
    if not calendars and not calendar_dates:
        raise ValueError("Refusing to replace calendars with an empty dataset")
    if any(row["source"] != source for row in calendars + calendar_dates):
        raise ValueError("All calendar rows must belong to the selected source")

    with engine.begin() as connection:
        connection.execute(delete(CalendarDate).where(CalendarDate.source == source))
        connection.execute(
            delete(ServiceCalendar).where(ServiceCalendar.source == source)
        )

        for model, rows in (
            (ServiceCalendar, calendars),
            (CalendarDate, calendar_dates),
        ):
            for start in range(0, len(rows), BATCH_SIZE):
                connection.execute(insert(model), rows[start : start + BATCH_SIZE])


def main() -> None:
    parser = argparse.ArgumentParser(description="Import MPK calendars from GTFS")
    parser.add_argument("source", choices=["T", "A"])
    args = parser.parse_args()

    try:
        data = download_feed(args.source)
        calendars, calendar_dates = read_calendars(data, args.source)
        save_calendars(calendars, calendar_dates, args.source)
        print(
            f"Import completed. Source: {args.source}. "
            f"Calendars: {len(calendars)}. "
            f"Calendar dates: {len(calendar_dates)}."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
