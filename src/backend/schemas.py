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

class RouteOut(BaseModel):
    source: Literal["T", "A"]
    route_id: str
    short_name: str
    long_name: str
    route_type: int


class RoutePage(BaseModel):
    items: list[RouteOut]
    total: int
    limit: int
    offset: int