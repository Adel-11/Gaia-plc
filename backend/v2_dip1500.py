"""Normalize the saved DIP1500 FB without replacing any project data."""
from __future__ import annotations

import copy
import re

from backend.v2_fb_flow import ASSIGN, ensure_flow, flow_view, render_flow, update_flow, variable_options
from backend.v2_plc_validation import _st_code


def regularize(state):
    typ = next((row for row in state.get("types", []) if row.get("name") == "DIP1500"), None)
    if typ is None:
        return False
    original = typ.get("fb", {})
    if (state.get("st_overrides") or {}).get(original.get("name"), {}).get("body") is not None:
        return False
    fb = copy.deepcopy(original)
    flow = ensure_flow(fb)
    if flow.get("section_overrides"):
        return False
    rows = flow_view(fb)["mappings"]
    options = {row["path"]: str(row["iec_type"]).upper() for row in variable_options(typ, state)}
    lines = flow.get("processing_code", "").splitlines(keepends=True)
    depth = 0
    removed = set()
    additions = []
    for index, line in enumerate(lines):
        clean = _st_code(line).strip()
        match = ASSIGN.fullmatch(line) if depth == 0 else None
        if match:
            _, destination, source = match.groups()
            section = None
            if re.fullmatch(r"COM\.COM_INPUT_IR\[\d+\]", source) and destination.startswith("INTERN."):
                section = "inputs"
            elif re.fullmatch(r"COM\.COM_OUTPUT_COILS\[\d+\]", destination) and source.startswith("INTERN."):
                section = "outputs"
            if section and options.get(source) and options[source] == options.get(destination):
                affected = lines[:index] if section == "inputs" else lines[index + 1:]
                context = _st_code("".join(affected))
                # A copy may move only past code independent of both its endpoints.
                def used(path):
                    base, bracket, index = path.partition("[")
                    suffix = rf"(?:\s*\[\s*{re.escape(index[:-1])}\s*\]|(?!\s*\[))" if bracket else ""
                    return re.search(r"(?<![\w.])" + re.escape(base) + r"(?!\w)" + suffix, context, re.I)
                references = any(used(path) for path in (source, destination)) or re.search(r"\bRETURN\b", context, re.I)
                duplicate = any(row["section"] == section and row["destination"] == destination for row in rows)
                if not references and not duplicate:
                    removed.add(index)
                    additions.append({"section": section, "source": source, "destination": destination, "original": line})
        if re.match(r"^(IF|FOR|WHILE|CASE|REPEAT)\b", clean, re.I):
            depth += 1
        elif re.match(r"^(END_IF|END_FOR|END_WHILE|END_CASE|UNTIL)\b", clean, re.I):
            depth = max(0, depth - 1)
    if not additions:
        return False
    proposed = {**typ, "fb": fb}
    processing = "".join(line for index, line in enumerate(lines) if index not in removed)
    if update_flow(proposed, state, mappings=rows + additions, processing_code=processing):
        return False
    flow = proposed["fb"]["flow"]
    for row in additions:
        segments = flow[f"{row['section'][:-1]}_segments"]
        segment = next(item for item in segments if item.get("destination") == row["destination"])
        segment["original"] = row["original"]
    proposed["fb"]["body"] = render_flow(flow)
    typ["fb"] = proposed["fb"]
    return True


def migrate_named_project(session, name):
    from sqlalchemy import func, select
    from backend import v2_store as store, v2_workspace as workspace
    from backend.v2_hmi import is_hmi

    project = session.scalar(select(store.V2Project).where(func.lower(store.V2Project.name) == name.casefold()))
    if project is None:
        return False
    previous_id = session.info.get("workspace_id")
    session.info["workspace_id"] = project.id
    try:
        current = store.load_project(session)
        if not current or not is_hmi(current):
            return False
        proposed = copy.deepcopy(current)
        if not regularize(proposed):
            return False
        baseline = set(workspace._compatibility_errors(current))
        issues = [error for error in workspace._compatibility_errors(proposed) if error not in baseline]
        if issues:
            raise ValueError(issues[0])
        store.save_project(session, proposed, username="system:DIP1500", expected_revision=current["revision"])
        return True
    finally:
        if previous_id is None:
            session.info.pop("workspace_id", None)
        else:
            session.info["workspace_id"] = previous_id
