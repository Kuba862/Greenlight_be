from typing import Literal
from pydantic import BaseModel


class StopOut(BaseModel):
    source: Literal["T", "A"]
    stop_id: str
    name: str
    latitude: float
    longitude: float


class StopPage(BaseModel):
    items: list[StopOut]
    total: int
    limit: int
    offset: int