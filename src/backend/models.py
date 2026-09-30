from sqlalchemy import CheckConstraint, Float, String, Text
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