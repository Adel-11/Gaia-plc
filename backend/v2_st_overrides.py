"""Manual ST overlays for the free PLC objects exposed by the generator."""
from __future__ import annotations

import copy
import re

from fastapi import Depends, HTTPException
from lxml import etree
from pydantic import BaseModel, Field

PLAINTEXT = "http://www.3s-software.com/plcopenxml/interfaceasplaintext"


class SourceEdit(BaseModel):
    revision: int
    declaration: str | None = Field(default=None, max_length=150_000)
    body: str | None = Field(default=None, max_length=150_000)


def plain_declaration(element):
    from backend.v2_plc import NS, HNS
    data = element.find(f"{NS}addData/{NS}data[@name='{PLAINTEXT}']")
    node = data.find(f"{NS}InterfaceAsPlainText/{HNS}xhtml") if data is not None else None
    return (node.text or "") if node is not None else None


def apply_overrides(obj, override):
    from backend.v2_plc import NS, HNS, HTML, _pou_text
    if override.get("body") is not None:
        _pou_text(obj, override["body"])
    if "declaration" not in override:
        return
    element = obj["element"]
    add = element.find(NS + "addData")
    if add is not None:
        for previous in add.findall(f"{NS}data[@name='{PLAINTEXT}']"):
            add.remove(previous)
    if override["declaration"] is None:
        return
    if add is None:
        add = etree.SubElement(element, NS + "addData")
    data = etree.SubElement(add, NS + "data", name=PLAINTEXT, handleUnknown="implementation")
    declaration = etree.SubElement(data, NS + "InterfaceAsPlainText")
    etree.SubElement(declaration, HNS + "xhtml", nsmap={None: HTML}).text = override["declaration"]


def discard_removed_overrides(previous, state):
    overrides = state.get("st_overrides")
    if not previous or not overrides:
        return
    from backend.v2_type_deletion import type_objects

    def names(project):
        result = set().union(*(type_objects(typ) for typ in project.get("types", [])))
        result.update(fn.get("name") or fn.get("FC") for fn in project.get("exchange_functions", []))
        return result

    for removed in names(previous) - names(state):
        overrides.pop(removed, None)


def _generated_declaration(obj):
    from backend.v2_code import _decl
    from backend.v2_codesys import _st
    return _decl(obj) if obj["kind"] == "POU" else _st(obj["element"])


def _body_node(obj):
    from backend.v2_plc import NS, HNS
    return obj["element"].find(f"{NS}body/{NS}ST/{HNS}xhtml")


def _source_view(state, name, objects):
    obj = objects.get(name)
    if obj is None:
        raise HTTPException(404, "Objet PLC introuvable.")
    override = (state.get("st_overrides") or {}).get(name, {})
    body = _body_node(obj)
    return {
        "name": name, "kind": obj["kind"], "revision": state["revision"],
        "declaration": plain_declaration(obj["element"]) or _generated_declaration(obj),
        "body": (body.text or "") if body is not None else None,
        "manual_declaration": override.get("declaration") is not None,
        "manual_body": override.get("body") is not None,
    }


def _validate_declaration(obj, source):
    from backend.v2_plc_validation import _st_code
    text = _st_code(source)
    if obj["kind"] == "GVL":
        if not re.search(r"(?im)^\s*VAR_GLOBAL\b", text):
            raise ValueError("La déclaration d’une GVL doit contenir VAR_GLOBAL.")
        return
    name = re.escape(obj["name"])
    if obj["kind"] == "DUT":
        header = "TYPE"
    else:
        header = {"functionBlock": "FUNCTION_BLOCK", "function": "FUNCTION", "program": "PROGRAM"}[
            obj["element"].get("pouType", "program")
        ]
    if not re.search(rf"(?im)^\s*{header}\s+{name}\b", text):
        raise ValueError(f"Conserve l’en-tête {header} {obj['name']} dans la déclaration.")


def register_source_routes(app, get_session, current_user, csrf, project):
    from backend import v2_plc, v2_plc_validation, v2_store

    @app.get("/api/v2/code/source/{name}")
    def get_source(name: str, session=Depends(get_session), user=Depends(current_user)):
        state = project(session)
        objects = v2_plc.build_program(state, include_union=True)
        return _source_view(state, name, objects)

    @app.post("/api/v2/code/source/{name}")
    def save_source(name: str, req: SourceEdit, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        current = project(session)
        if req.revision != current["revision"]:
            raise HTTPException(409, "Le projet a changé. Rouvre le ST avant de l’enregistrer.")
        fields = req.model_dump(exclude_unset=True, exclude={"revision"})
        if not fields:
            raise HTTPException(422, "Aucune modification ST à enregistrer.")
        try:
            old_objects = v2_plc.build_program(current, include_union=True)
            obj = old_objects.get(name)
            if obj is None:
                raise HTTPException(404, "Objet PLC introuvable.")
            if fields.get("declaration") is not None:
                _validate_declaration(obj, fields["declaration"])
            if fields.get("body") is not None and _body_node(obj) is None:
                raise ValueError("Cet objet ne contient pas de corps Structured Text.")
            state = copy.deepcopy(current)
            override = state.setdefault("st_overrides", {}).setdefault(name, {})
            if "body" in fields and fields["body"] is None:
                override.pop("body", None)
                fields.pop("body")
            override.update(fields)
            if not override:
                state["st_overrides"].pop(name)
            new_objects = v2_plc.build_program(state, include_union=True)
            def errors(objects, project_state):
                xml = v2_plc.serialize_program(objects, project_state, list(objects))
                return v2_plc_validation.validate_xml(xml, project_state, objects)["errors"]
            before = set(errors(old_objects, current))
            added = [error for error in errors(new_objects, state) if error not in before]
            if added:
                raise ValueError("\n".join(added))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            saved = v2_store.save_project(session, state, username=user.username, expected_revision=req.revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return _source_view(saved, name, new_objects)
