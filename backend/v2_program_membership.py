"""Program call membership for explicitly wiring generated instance and copy programs."""
from __future__ import annotations

BLOCKS = (
    "GFB_INST",
    "GFB_UPI",
    "GFC_COM_IN",
    "GFC_COM_OUT",
    "GFC_IO_IN",
    "GFC_IO_OUT",
    "GFC_EXC",
)


def _instance_name(instance: dict) -> str:
    return str(instance.get("name") or instance.get("id") or "")


def eligible_instances(state: dict) -> dict[str, list[str]]:
    """Return eligible names by generated program, preserving project instance order."""
    equipment: list[str] = []
    upis: list[str] = []
    type_by_id = {str(t.get("id")): t for t in state.get("types", [])}
    type_by_name = {str(t.get("name")): t for t in state.get("types", [])}
    for instance in state.get("instances", []):
        name = _instance_name(instance)
        typ = type_by_id.get(str(instance.get("type_id")), type_by_name.get(str(instance.get("type_id")), {}))
        if not name or not typ:
            continue
        if "UPI" in str(typ.get("kind", "")).upper():
            upis.append(name)
        else:
            equipment.append(name)
    all_instances = equipment + upis
    return {
        "GFB_INST": equipment,
        "GFB_UPI": upis,
        "GFC_COM_IN": all_instances.copy(),
        "GFC_COM_OUT": all_instances.copy(),
        "GFC_IO_IN": equipment.copy(),
        "GFC_IO_OUT": equipment.copy(),
        "GFC_EXC": all_instances.copy(),
    }


def freeze_membership(state: dict) -> dict[str, list[str]]:
    """Initialize missing block memberships from the instances present at freeze time.

    Existing saved memberships remain explicit. A legacy state without membership gets
    the historical all-eligible defaults, so current projects keep their generated calls.
    """
    defaults = eligible_instances(state)
    stored = state.get("program_membership")
    if not isinstance(stored, dict):
        stored = {}
        state["program_membership"] = stored
    for block in BLOCKS:
        if block not in stored or not isinstance(stored.get(block), list):
            stored[block] = defaults[block].copy()
        else:
            # Remove duplicates and stale identifiers while retaining the user order.
            allowed = set(defaults[block])
            stored[block] = list(dict.fromkeys(str(name) for name in stored[block] if str(name) in allowed))
    return stored
