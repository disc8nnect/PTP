"""Straight-line distance between points. Standard library only."""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def nearest(facilities: list[dict], lat: float, lon: float, kind: str | None = None) -> list[dict]:
    """Facilities with a "distance_km" field, closest first. kind filters by facility type."""
    out = []
    for f in facilities:
        if kind and f.get("kind") != kind:
            continue
        item = dict(f)
        item["distance_km"] = round(haversine_km(lat, lon, f["lat"], f["lon"]), 2)
        out.append(item)
    out.sort(key=lambda f: f["distance_km"])
    return out
