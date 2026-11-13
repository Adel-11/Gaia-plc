"""Browse GVL declarations and organize them into ST comment sections."""
import re

from fastapi import Depends, HTTPException
from lxml import etree
from pydantic import BaseModel, Field


def prepend_documentation(row, heading):
    from backend.v2_plc import NS, HNS, HTML
    doc = row.find(NS + "documentation")
    if doc is None:
        doc = etree.SubElement(row, NS + "documentation")
    node = doc.find(HNS + "xhtml")
    if node is None:
        node = etree.SubElement(doc, HNS + "xhtml", nsmap={None: HTML})
    previous = node.text or ""
    if previous != heading and not previous.startswith(heading + "\n"):
        node.text = heading + ("\n" + previous if previous else "")


def organize(element, folders):
    from backend.v2_plc import NS
    variables = element.findall(NS + "variable")
    if not any(folders.get(row.get("name", "").casefold()) for row in variables):
        return
    first = element.index(variables[0])
    for row in variables:
        element.remove(row)
    previous = None
    for index, row in enumerate(sorted(variables, key=lambda item: (
        bool(folders.get(item.get("name", "").casefold())),
        folders.get(item.get("name", "").casefold(), "").casefold(),
    ))):
        folder = folders.get(row.get("name", "").casefold())
        if folder and folder != previous:
            prepend_documentation(row, f"---------------------------- {folder}")
        element.insert(first + index, row)
        previous = folder


class Edit(BaseModel):
    revision: int
    gvl: str
    name: str
    folder: str = Field(default="", max_length=100)
    new_name: str | None = None
    iec_type: str | None = None


def register(app, get_session, current_user, csrf, project):
    from backend.v2_catalog import IEC_TYPES
    from backend.v2_codesys import _st, _type_text
    from backend.v2_io_copies import add_iomap_row, variable_rows
    from backend.v2_io_hardware import STATE_KEY, read_hardware
    from backend.v2_plc import NS, build_program
    from backend.v2_plc_validation import _st_code
    from backend.v2_st_overrides import plain_declaration
    from backend.v2_store import save_project
    from backend.v2_type_creation import _identifier

    def inventory(state, only=None):
        objects = build_program(state)
        code = "\n".join(_st_code(_st(obj["element"])) for obj in objects.values() if obj["kind"] == "POU")
        used = {(gvl.casefold(), member.casefold()) for gvl, member in
                re.findall(r"\b(GVL_\w+)\s*\.\s*(\w+)", code, re.I)}
        # Qualified-only GVL declarations are typical, but retain unqualified references too.
        unqualified = re.sub(r"\.\s*\w+", "", code.casefold())
        tokens = set(re.findall(r"\b\w+\b", unqualified))
        for collection, row, key in variable_rows(state):
            if collection == "io_channels" or row.get("GVL_IO member"):
                used.add(("gvl_iomap", key))
        if rack := state.get(STATE_KEY):
            _, channels = read_hardware(rack["xml"].encode("utf-8"))
            for channel in channels.values():
                path = channel.variable.split(".")
                if len(path) >= 2:
                    used.add((path[-2].casefold(), path[-1].casefold()))
        result = []
        for name, obj in objects.items():
            if obj["kind"] != "GVL" or only and name != only:
                continue
            element = obj["element"]
            manual = plain_declaration(element)
            folders = state.get("gvl_folders", {}).get(name, {})
            variables = [{"name": row.get("name"), "iec_type": _type_text(row.find(NS + "type/*"))}
                         for row in element.findall(NS + "variable")]
            if manual is not None:
                variables = [{"name": member.strip(), "iec_type": dtype.strip()}
                             for names, dtype in re.findall(
                                 r"(?im)(?:^|(?<=;))\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*(?:AT\s+%[\w*.]+\s*)?:\s*([^;\r\n]+?)(?=\s*:=|\s*;)",
                                 _st_code(manual))
                             for member in names.split(",")]
            rows = []
            for row in variables:
                key = row["name"].casefold()
                assigned = (name.casefold(), key) in used or key in tokens
                rows.append({**row, "assigned": assigned, "folder": folders.get(key, ""),
                             "editable": name == "GVL_IOMAP" and manual is None and not assigned and row["iec_type"] in IEC_TYPES})
            result.append({"name": name, "manual": manual is not None, "rows": rows, "st": manual if manual is not None else _st(element)})
        return result

    @app.get("/api/v2/gvl")
    def get_gvl(session=Depends(get_session), user=Depends(current_user)):
        state = project(session)
        return {"revision": state["revision"], "lists": inventory(state), "types": sorted(IEC_TYPES)}

    @app.post("/api/v2/gvl")
    def edit_gvl(body: Edit, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        if body.revision != state["revision"]:
            raise HTTPException(409, "Le projet a changé. Recharge la liste.")
        lists = inventory(state, body.gvl)
        if not lists or lists[0]["manual"]:
            raise HTTPException(422, "Cette déclaration se modifie dans le ST complet.")
        if body.gvl != "GVL_IOMAP":
            raise HTTPException(422, "Cette liste est gérée dans son éditeur source.")
        rows = {row["name"].casefold(): row for row in lists[0]["rows"]}
        key = body.name.casefold()
        row = rows.get(key)
        folder = body.folder.strip()
        if re.search(r"[\r\n\x00-\x1f]|\(\*|\*\)", folder):
            raise HTTPException(422, "Indique un nom de dossier sur une seule ligne.")
        folders = state.setdefault("gvl_folders", {}).setdefault(body.gvl, {})
        if body.new_name is not None or body.iec_type is not None:
            name = (body.new_name or body.name).strip()
            dtype = (body.iec_type or (row or {}).get("iec_type") or "").upper()
            if body.gvl != "GVL_IOMAP" or not _identifier(name) or dtype not in IEC_TYPES:
                raise HTTPException(422, "Choisis un nom IEC valide et un type IOMAP compatible.")
            if (row is None and body.name) or (row is not None and not row["editable"]):
                raise HTTPException(422, "Cette variable est affectée ou sa déclaration est gérée par le programme.")
            if name.casefold() in rows and (row is None or name.casefold() != key):
                raise HTTPException(422, "Ce nom existe déjà dans GVL_IOMAP.")
            old = next((record for _, record, current in variable_rows(state) if current == key), {})
            from backend.v2_io_copies import remove_copy, variable_direction
            direction = variable_direction(old)
            if row:
                remove_copy(state, row["name"], delete=True)
            entry = add_iomap_row(state, name, dtype, direction or "input")
            if not direction:
                entry["direction"] = entry["I/O type"] = None
            folders.pop(key, None)
            key = name.casefold()
            from backend.v2_configuration import synchronize_io_workbook
            synchronize_io_workbook(state)
        elif row is None:
            raise HTTPException(404, "Variable introuvable.")
        if folder:
            folders[key] = folder
        else:
            folders.pop(key, None)
        try:
            saved = save_project(session, state, username=user.username, expected_revision=body.revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return {"revision": saved["revision"], "lists": inventory(saved), "types": sorted(IEC_TYPES)}
