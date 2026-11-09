"""Generator-only equipment templates and their one-time expansion into PLC types."""
from __future__ import annotations

import re
from typing import Literal
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from backend import v2_store as store

ROLES = ("PA", "COM", "EXC", "IO", "INT")
ANALOG_TYPES = {"BYTE", "WORD", "DWORD", "INT", "UINT", "DINT", "UDINT", "REAL", "LREAL"}
MODBUS_TYPES = {"WORD", "INT", "UINT", "REAL"}
QUALIFIED = re.compile(
    r"//[^\r\n]*|\(\*.*?\*\)|'(?:\$[^\r\n]|''|[^'])*'|\"(?:\$[^\r\n]|\"\"|[^\"])*\""
    r"|(?<![\w.])(PA|COM|EXC|IO|INTERN)\s*\.\s*([A-Za-z_]\w*)",
    re.S | re.I,
)


class Signal(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    direction: Literal["input", "output"]
    kind: Literal["analog", "digital"]
    iec_type: str = Field(max_length=20)
    transport: Literal["io", "tcp", "rtu"] = "io"
    unit: str = Field(default="", max_length=80)
    comment: str = Field(default="", max_length=500)
    io_name: str = Field(default="", max_length=80)
    internal_name: str = Field(default="", max_length=80)
    exchange_name: str = Field(default="", max_length=80)


class LogicMember(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    role: Literal["PA", "COM", "EXC", "IO", "INT"]
    name: str = Field(min_length=1, max_length=80)
    iec_type: str = Field(min_length=1, max_length=128)
    default_value: str = Field(default="", max_length=500)
    comment: str = Field(default="", max_length=500)
    unit: str = Field(default="", max_length=80)
    section: str = Field(default="", max_length=100)


class Variables(BaseModel):
    signals: list[Signal] = Field(default_factory=list, max_length=100)
    members: list[LogicMember] = Field(default_factory=list, max_length=150)


class GenericSave(Variables):
    revision: int
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    processing: str = Field(default="", max_length=30000)
    parent_id: str | None = Field(default=None, max_length=64)


class Inheritance(Variables):
    template_id: str = Field(min_length=1, max_length=64)
    inherit_processing: bool = True
    processing: str | None = Field(default=None, max_length=30000)


def _signal(name, direction="input", kind="digital"):
    return {
        "id": name.lower(), "name": name, "direction": direction, "kind": kind,
        "iec_type": "DWORD" if kind == "analog" else "BOOL",
    }


STARTERS = (
    {
        "id": "sensor", "builtin": True, "name": "Capteur analogique",
        "description": "Inspiré du DOSENSOR. Lit une mesure brute et la met à disposition des échanges.",
        "signals": [_signal("MESURE", kind="analog")], "members": [],
        "processing": "// Placez ici le filtrage ou la mise à l’échelle du capteur.",
    },
    {
        "id": "pump", "builtin": True, "name": "Pompe avec autorisation",
        "description": "Inspiré de la DIP1500. Prépare une commande de marche avec autorisation et contrôle de défaut.",
        "signals": [_signal("AUTORISATION"), _signal("DEFAUT"), _signal("MARCHE", "output")],
        "members": [{"id": "start-request", "role": "EXC", "name": "INPUT_DEMANDE_MARCHE", "iec_type": "BOOL"}],
        "processing": "INTERN.MARCHE := EXC.INPUT_DEMANDE_MARCHE AND INTERN.AUTORISATION AND NOT INTERN.DEFAUT;",
    },
    {
        "id": "hmi-local", "builtin": True, "name": "IHM seule · mode local",
        "description": "Reçoit une demande de marche de l’IHM et l’autorise uniquement en mode local. Publie le mode et l’état de l’autorisation.",
        "signals": [],
        "members": [
            {"id": "local-request", "role": "COM", "name": "HMI_INPUT_MODE_LOCAL", "iec_type": "BOOL"},
            {"id": "start-request", "role": "COM", "name": "HMI_INPUT_MARCHE", "iec_type": "BOOL"},
            {"id": "local-state", "role": "COM", "name": "HMI_OUTPUT_MODE_LOCAL", "iec_type": "BOOL"},
            {"id": "start-state", "role": "COM", "name": "HMI_OUTPUT_MARCHE_AUTORISEE", "iec_type": "BOOL"},
            {"id": "local", "role": "INT", "name": "MODE_LOCAL", "iec_type": "BOOL"},
            {"id": "start", "role": "INT", "name": "MARCHE_AUTORISEE", "iec_type": "BOOL"},
        ],
        "processing": (
            "INTERN.MODE_LOCAL := COM.HMI_INPUT_MODE_LOCAL;\n"
            "INTERN.MARCHE_AUTORISEE := INTERN.MODE_LOCAL AND COM.HMI_INPUT_MARCHE;\n"
            "COM.HMI_OUTPUT_MODE_LOCAL := INTERN.MODE_LOCAL;\n"
            "COM.HMI_OUTPUT_MARCHE_AUTORISEE := INTERN.MARCHE_AUTORISEE;"
        ),
    },
)


def templates(state):
    return [*STARTERS, *state.get("generic_objects", [])]


def _declared(variables, state):
    """Return members keyed by stable identity, plus generated I/O mappings."""
    from backend.v2_type_creation import _iec_type_error, _identifier
    from backend.v2_hmi import member_error

    declarations = {}
    inputs, outputs = [], []
    known_types = {dut["name"] for typ in state.get("types", []) for dut in typ.get("duts", [])}
    names = set()

    def add(identity, role, name, iec_type, section, **details):
        key = (role, name.upper())
        if not _identifier(name):
            raise ValueError(f"{role}.{name} doit être un identifiant IEC valide.")
        if identity in declarations or key in names:
            raise ValueError(f"La variable {role}.{name} est en double.")
        error = member_error(state, role, name) or _iec_type_error(iec_type, known_types)
        if error:
            raise ValueError(f"{role}.{name}: {error}")
        names.add(key)
        declarations[identity] = (role, {
            "name": name, "iec_type": iec_type, "section": section, **details,
        })

    for signal in variables.signals:
        name = signal.name.strip()
        iec_type = signal.iec_type.upper()
        inbound = signal.direction == "input"
        analog_types = ANALOG_TYPES if signal.transport == "io" else MODBUS_TYPES
        if (signal.kind == "digital" and iec_type != "BOOL") or (signal.kind == "analog" and iec_type not in analog_types):
            raise ValueError(f"Le type {iec_type} ne convient pas au signal {name}.")
        role = "IO" if signal.transport == "io" else "COM"
        prefix = "" if role == "IO" else f"{signal.transport.upper()}_"
        port = signal.io_name.strip() or f"{prefix}{'INPUT' if inbound else 'OUTPUT'}_{name}"
        union = role == "COM" and iec_type == "REAL"
        port_type = "DUT_Union_Real_Word" if union else iec_type
        port_path = f"{role}.{port}" + (".Real_value" if union else "")
        intern = signal.internal_name.strip() or name
        section = "INPUT (field to plc)" if inbound else "OUTPUT (plc to field)"
        details = {"unit": signal.unit, "comment": signal.comment}
        add(("signal", signal.id, "io"), role, port, port_type, section, **details)
        add(("signal", signal.id, "internal"), "INT", intern, iec_type, "Signaux internes", **details)
        if inbound:
            exchange = signal.exchange_name.strip() or f"OUTPUT_{name}"
            add(("signal", signal.id, "exchange"), "EXC", exchange, iec_type, "Mesures et états", **details)
            inputs.append(f"INTERN.{intern} := {port_path};")
            outputs.append(f"EXC.{exchange} := INTERN.{intern};")
        else:
            outputs.append(f"{port_path} := INTERN.{intern};")

    for member in variables.members:
        add(("member", member.id), member.role, member.name.strip(), member.iec_type.strip(),
            member.section.strip() or "Variables du modèle", default_value=member.default_value,
            comment=member.comment, unit=member.unit)
    return declarations, inputs, outputs


def _rewrite(processing, source, destination):
    aliases = {
        ("INTERN" if role == "INT" else role, member["name"].upper()): identity
        for identity, (role, member) in source.items()
    }

    def replace(match):
        if match[1] is None:
            return match[0]
        identity = aliases.get((match[1].upper(), match[2].upper()))
        if identity is None:
            raise ValueError(f"La logique utilise une variable non déclarée : {match[0]}.")
        if identity not in destination:
            raise ValueError(
                f"La logique utilise {match[0]}. Garde cette variable ou retire la logique héritée."
            )
        role, member = destination[identity]
        return f"{'INTERN' if role == 'INT' else role}.{member['name']}"

    return QUALIFIED.sub(replace, processing)


def materialize(state, inheritance):
    """Expand the selected template without adding it to the program model."""
    template = next((item for item in templates(state) if item["id"] == inheritance.template_id), None)
    if template is None:
        raise ValueError("Le modèle générique n’existe plus.")
    before, _, _ = _declared(Variables.model_validate(template), state)
    after, inputs, outputs = _declared(inheritance, state)
    if inheritance.processing == template["processing"] and inheritance.inherit_processing:
        processing = _rewrite(inheritance.processing, before, after)
    elif inheritance.processing is not None:
        processing = _rewrite(inheritance.processing, after, after)
    else:
        processing = _rewrite(template["processing"], before, after) if inheritance.inherit_processing else ""
    body = "\n".join(["// INPUTS", *inputs, "// PROCESSING", processing, "// OUTPUTS", *outputs, ""])
    duts = [
        {"role": role, "members": [member for assigned, member in after.values() if assigned == role]
         or [{"name": "SPARE", "iec_type": "BOOL"}]}
        for role in ROLES
    ]
    return duts, body


def register_generic_routes(app, get_session, current_user, csrf):
    @app.post("/api/v2/generic-objects")
    def save(body: GenericSave, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = store.load_project(session)
        if state is None:
            raise HTTPException(404, "Projet introuvable.")
        if body.revision != state["revision"]:
            raise HTTPException(409, "Le projet a changé. Actualise avant d’enregistrer le modèle.")
        custom = state.setdefault("generic_objects", [])
        existing = next((item for item in custom if item["id"] == body.id), None)
        if body.id and existing is None:
            raise HTTPException(404, "Modèle personnalisé introuvable.")
        name = body.name.strip()
        if not name or any(item["id"] != body.id and item["name"].casefold() == name.casefold()
                           for item in templates(state)):
            raise HTTPException(422, "Choisis un nom de modèle unique.")
        parent_id = existing.get("parent_id") if existing else body.parent_id
        parent = next((item for item in templates(state) if item["id"] == parent_id), None)
        if parent_id and not parent and not existing:
            raise HTTPException(422, "Le modèle parent n’est plus disponible.")
        try:
            declared, _, _ = _declared(body, state)
            source = existing or parent
            previous = _declared(Variables.model_validate(source), state)[0] if source else declared
            if source and body.processing == source.get("processing", ""):
                processing = _rewrite(body.processing, previous, declared)
            else:
                processing = _rewrite(body.processing, declared, declared)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        item = body.model_dump(exclude={"revision", "id"})
        item.update(id=body.id or uuid4().hex, name=name, builtin=False, processing=processing, parent_id=parent_id)
        if parent_id:
            item["parent_name"] = existing.get("parent_name") if existing else parent["name"]
        if existing is None:
            custom.append(item)
        else:
            custom[custom.index(existing)] = item
        try:
            result = store.save_project(session, state, user.username, expected_revision=body.revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return {"template": item, "revision": result["revision"]}

    @app.delete("/api/v2/generic-objects/{template_id}")
    def delete(template_id: str, revision: int, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = store.load_project(session)
        if not state or revision != state.get("revision"):
            raise HTTPException(409, "Le projet a changé. Actualise avant de supprimer le modèle.")
        models = state.get("generic_objects", [])
        target = next((item for item in models if item["id"] == template_id and not item.get("builtin")), None)
        if target is None:
            raise HTTPException(404, "Modèle personnalisé introuvable.")
        models.remove(target)
        try:
            result = store.save_project(session, state, user.username, expected_revision=revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return {"revision": result["revision"]}
