from typing import Any, Dict, List

_cache: Dict[str, Any] = {}


def set_station_crowd(station_id: str, data: dict) -> None:
    _cache[station_id] = data


def get_station_crowd(station_id: str) -> dict | None:
    return _cache.get(station_id)


def get_all_crowd() -> List[dict]:
    return list(_cache.values())


def is_warm() -> bool:
    return bool(_cache)
