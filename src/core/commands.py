from __future__ import annotations

from typing import TypedDict, NotRequired, Optional, Dict, Any, Tuple


# Command payload types
class BaseCommand(TypedDict):
    type: str
    user_id: int


class BuildBuildingCommand(BaseCommand):
    building_type: str


class DemolishBuildingCommand(BaseCommand):
    building_type: str


class CancelBuildQueueCommand(BaseCommand):
    index: NotRequired[Optional[int]]


class UpdatePlayerActivityCommand(BaseCommand):
    pass


class StartResearchCommand(BaseCommand):
    research_type: str


class BuildShipsCommand(BaseCommand):
    ship_type: str
    quantity: NotRequired[int]


class ColonizeCommand(BaseCommand):
    galaxy: NotRequired[int]
    system: NotRequired[int]
    position: NotRequired[int]
    planet_name: NotRequired[str]


class FleetDispatchCommand(BaseCommand):
    galaxy: NotRequired[int]
    system: NotRequired[int]
    position: NotRequired[int]
    mission: NotRequired[str]
    speed: NotRequired[Optional[float]]
    ships: NotRequired[Optional[Dict[str, int]]]


class FleetRecallCommand(BaseCommand):
    fleet_id: NotRequired[Optional[int]]


class TradeCreateOfferCommand(BaseCommand):
    offered_resource: NotRequired[Optional[str]]
    offered_amount: NotRequired[int]
    requested_resource: NotRequired[Optional[str]]
    requested_amount: NotRequired[int]


class TradeAcceptOfferCommand(BaseCommand):
    offer_id: int


# Parse helpers to normalize incoming raw dicts into typed, validated tuples

def _get_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _get_coord(value: Any, default: int = 1) -> int:
    """Coordinates should default to 1 when missing or falsy (including 0)."""
    try:
        v = int(value)
    except Exception:
        return default
    return v or default


def _get_optional_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def parse_build_building(cmd: Dict[str, Any]) -> Tuple[int, Any]:
    return _get_int(cmd.get("user_id")), cmd.get("building_type")


def parse_demolish_building(cmd: Dict[str, Any]) -> Tuple[int, Any]:
    return _get_int(cmd.get("user_id")), cmd.get("building_type")


def parse_cancel_build_queue(cmd: Dict[str, Any]) -> Tuple[int, Optional[int]]:
    return _get_int(cmd.get("user_id")), _get_optional_int(cmd.get("index"))


def parse_update_activity(cmd: Dict[str, Any]) -> int:
    return _get_int(cmd.get("user_id"))


def parse_start_research(cmd: Dict[str, Any]) -> Tuple[int, str]:
    return _get_int(cmd.get("user_id")), str(cmd.get("research_type"))


def parse_build_ships(cmd: Dict[str, Any]) -> Tuple[int, str, int]:
    return (
        _get_int(cmd.get("user_id")),
        str(cmd.get("ship_type")),
        _get_int(cmd.get("quantity"), 1),
    )


def parse_colonize(cmd: Dict[str, Any]) -> Tuple[int, int, int, int, str]:
    return (
        _get_int(cmd.get("user_id")),
        _get_coord(cmd.get("galaxy"), 1),
        _get_coord(cmd.get("system"), 1),
        _get_coord(cmd.get("position"), 1),
        str(cmd.get("planet_name") or "Colony"),
    )


def parse_fleet_dispatch(cmd: Dict[str, Any]) -> Tuple[int, int, int, int, str, Optional[float], Optional[Dict[str, int]]]:
    speed_val = cmd.get("speed")
    try:
        speed: Optional[float] = float(speed_val) if speed_val is not None else None
    except Exception:
        speed = None

    ships = cmd.get("ships")
    if ships is not None and not isinstance(ships, dict):
        ships = None

    return (
        _get_int(cmd.get("user_id")),
        _get_coord(cmd.get("galaxy"), 1),
        _get_coord(cmd.get("system"), 1),
        _get_coord(cmd.get("position"), 1),
        str(cmd.get("mission") or "transfer"),
        speed,
        ships,
    )


def parse_fleet_recall(cmd: Dict[str, Any]) -> Tuple[int, Optional[int]]:
    return _get_int(cmd.get("user_id")), _get_optional_int(cmd.get("fleet_id"))


def parse_trade_create_offer(cmd: Dict[str, Any]) -> Tuple[int, Optional[str], int, Optional[str], int]:
    return (
        _get_int(cmd.get("user_id")),
        (cmd.get("offered_resource") if cmd.get("offered_resource") else None),
        _get_int(cmd.get("offered_amount"), 0),
        (cmd.get("requested_resource") if cmd.get("requested_resource") else None),
        _get_int(cmd.get("requested_amount"), 0),
    )


def parse_trade_accept_offer(cmd: Dict[str, Any]) -> Tuple[int, int]:
    return _get_int(cmd.get("user_id")), _get_int(cmd.get("offer_id"), -1)
