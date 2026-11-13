"""Keep managed PLC references aligned with a reviewed DUT edit."""
from __future__ import annotations

import copy
import re

# Consume comments and strings as complete tokens before looking at identifiers.
TOKEN = re.compile(r"""//[^\r\n]*|\(\*|"(?:\$[^\r\n]|[^"\r\n])*"|'(?:\$[^\r\n]|[^'\r\n])*'|[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*""")
COMMENT_EDGE = re.compile(r"\(\*|\*\)")
DOT = re.compile(r"\s*\.\s*")


def rewrite_st(code, roots, removed=None):
    """Rewrite qualified references in one pass, without changing their formatting."""
    source = str(code or "")
    out, end = [], 0
    while match := TOKEN.search(source, end):
        out.append(source[end:match.start()])
        token = match.group()
        end = match.end()
        if token == "(*":
            start, depth = match.start(), 1
            while depth and (edge := COMMENT_EDGE.search(source, end)):
                depth += 1 if edge.group() == "(*" else -1
                end = edge.end()
            if depth: end = len(source)
            token = source[start:end]
        elif token[0].isalpha() or token[0] == "_":
            parts = DOT.split(token)
            for i in range(len(parts) - 1, -1, -1):
                root = ".".join(parts[:i]).casefold()
                delta = roots.get(root, {})
                member = parts[i]
                if member.casefold() not in delta: continue
                value = delta[member.casefold()]
                if value is None:
                    if removed is not None: removed.add(".".join(parts[:i+1]))
                elif member != value:
                    spans = list(re.finditer(r"[A-Za-z_]\w*", token))
                    a, b = spans[i].span()
                    token = token[:a] + value + token[b:]
                break
        out.append(token)
    out.append(source[end:])
    return "".join(out)


def member_deltas(before, after):
    roles = {}
    for old in before.get("duts", []):
        role = old["role"]
        rows = next((d.get("members", []) for d in after.get("duts", []) if d["role"] == role), [])
        ids = {str(m["id"]): m["name"] for m in rows if m.get("id")}
        names = {str(m["name"]).casefold(): m["name"] for m in rows}
        edits = {}
        for row in old.get("members", []):
            name = row["name"]
            value = ids.get(str(row.get("id")), names.get(name.casefold()))
            if value != name: edits[name.casefold()] = value
        if edits: roles[role] = edits
    return roles


def _code(owner, field, roots, label, errors):
    if not owner.get(field): return False
    removed = set()
    before = owner[field]
    owner[field] = rewrite_st(before, roots, removed)
    if removed:
        errors.append(f"{label} : le ST utilise encore {', '.join(sorted(removed))}. Modifiez le code avant de supprimer ces variables.")
    return before != owner[field]


def sync_fb(typ, edits, mappings=None, processing_code=None, prefix_code=None, section_overrides=None):
    from backend.v2_fb_flow import ensure_flow, flow_view, render_flow
    fb = typ.setdefault("fb", {})
    flow = ensure_flow(fb)
    if prefix_code is not None:
        flow["prefix"] = prefix_code + ("\n" if prefix_code and not prefix_code.endswith("\n") else "")
    if processing_code is not None:
        flow["processing_code"] = processing_code + ("\n" if processing_code and not processing_code.endswith("\n") else "")
    if section_overrides is not None:
        manual = flow.setdefault("section_overrides", {})
        for section, code in section_overrides.items():
            if code is None:
                manual.pop(section, None)
            else:
                manual[section] = code
        if manual and not flow.get("input_header"):
            flow.update(input_header="// INPUTS\n", processing_header="\n// PROCESSING\n", output_header="\n// OUTPUTS\n")
    roots = {("intern" if k == "INT" else k.lower()): v for k, v in edits.items()}
    errors, changes = [], []
    label = fb.get("name") or typ["name"]
    if roots:
        for field in ("prefix", "processing_code"):
            if _code(flow, field, roots, label, errors): changes.append(f"{label} : références ST mises à jour")
        for section in ("inputs", "outputs"):
            if _code(flow.get("section_overrides", {}), section, roots, label, errors):
                changes.append(f"{label} : références ST mises à jour ({section})")
        if _code(fb, "custom_code", roots, label, errors): changes.append(f"{label} : références du code personnalisé mises à jour")
        fb["body"] = render_flow(flow)
        maps = copy.deepcopy(mappings if mappings is not None else flow_view(fb)["mappings"])
        mappings = []
        for row in maps:
            removed = set()
            source, destination = (rewrite_st(row.get(k), roots, removed) for k in ("source", "destination"))
            link = f"{row.get('source')} → {row.get('destination')}"
            if removed:
                changes.append(f"{label} : lien retiré ({link})")
            else:
                if (source, destination) != (row.get("source"), row.get("destination")):
                    changes.append(f"{label} : lien renommé ({link} → {source} / {destination})")
                mappings.append({**row, "source": source, "destination": destination})
    fb["body"] = render_flow(flow)
    return mappings, errors, changes


def _simple(value, delta, removed):
    if not isinstance(value, str) or not value: return value
    return rewrite_st("ROOT." + value, {"root": delta, "root.com": delta}, removed).removeprefix("ROOT.")


def sync_consumers(state, typ, edits):
    """Propagate structured links; only handwritten references can block removal."""
    if not edits: return [], []
    from backend.v2_exchanges import _normal_mappings, _sync
    from backend.v2_configuration import synchronize_workbooks
    names = {str(typ.get(k)) for k in ("id", "name")}
    instances = [i for i in state.get("instances", []) if str(i.get("type_id")) in names]
    instances_by_alias = {str(i[k]): str(i.get("name") or i.get("id")) for i in instances for k in ("id", "name") if i.get(k)}
    roots = {f"gvl_{role.lower()}.{role.lower()}_{name.lower()}": delta
             for role, delta in edits.items() if role in {"IO", "COM", "EXC", "PA"}
             for name in set(instances_by_alias.values())}
    short_roots = {root.split(".", 1)[1]: delta for root, delta in roots.items()}
    errors, changes = [], []
    sync_sheets = False
    if "COM" in edits and "modbus_template" in typ:
        updated = []
        for row in typ["modbus_template"]:
            removed = set()
            path = rewrite_st("COM." + row["member"], {"com": edits["COM"]}, removed)[4:]
            if removed:
                changes.append(f"Retirer {row['member']} du modèle Modbus.")
            else:
                updated.append({**row, "member": path})
        typ["modbus_template"] = updated
    for collection, role, qualified, simple in (
        ("tcp_points", "COM", ("GVL_COM variable",), ("Member", "member_path", "member_name", "member")),
        ("rtu_channels", "COM", ("GVL_COM variable",), ("Member", "member_path", "member_name", "member", "DUT_COM member")),
        ("io_channels", "IO", ("GVL_IO member",), ("member_name", "member_path")),
        ("virtual_io", "IO", ("GVL_IO member",), ("member_name", "member_path")),
        ("pa_values", "PA", ("GVL_PA variable",), ("Member", "member_name", "member")),
    ):
        delta = edits.get(role)
        if not delta or not instances: continue
        keep = []
        for row in state.get(collection, []):
            inst = next((str(row[key]) for key in ("instance_id", "Instance", "instance") if row.get(key)), "")
            removed = set()
            old = dict(row)
            for field in qualified:
                if row.get(field): row[field] = rewrite_st(row[field], roots | short_roots, removed)
            if inst in instances_by_alias:
                for field in simple:
                    if row.get(field): row[field] = _simple(row[field], delta, removed)
                if collection == "pa_values" and row.get("Member") != old.get("Member"):
                    row["id"] = f"{instances_by_alias[inst]}:{row['Member']}"
                if row.get("Name"):
                    name = instances_by_alias[inst]
                    row["Name"] = rewrite_st(row["Name"], {name.casefold(): delta})
            if removed:
                changes.append(f"{collection} : lien retiré ({inst} · {', '.join(sorted(removed)).replace('ROOT.', '')})")
                sync_sheets = True
                if collection not in {"io_channels", "virtual_io"}: continue
                for field in (*qualified, *simple, "Instance", "instance_id", "instance"):
                    row[field] = None
            elif old != row:
                changes.append(f"{collection} : référence mise à jour ({inst})")
                sync_sheets = True
            keep.append(row)
        if collection in state: state[collection] = keep
    sync_exchanges = False
    for fn in state.get("exchange_functions", []):
        local = dict(roots)
        for end in ("a", "b"):
            if "EXC" not in edits: continue
            if str(fn.get(f"type_{end}_id") or fn.get(f"Object type {end.upper()}")) in names:
                arg = fn.get(f"parameter_{end}") or fn.get(f"Parameter {end.upper()}") or f"EXC_{typ['name']}"
                local[arg.casefold()] = edits["EXC"]
        label = fn.get("name") or fn.get("FC")
        if _code(fn, "custom_code", local, label, errors): changes.append(f"{label} : code personnalisé mis à jour")
        if local.keys() == roots.keys(): continue
        before = _normal_mappings(state, fn)
        result = []
        for old in before:
            row = copy.deepcopy(old)
            removed = set()
            for key in ("source", "destination"):
                row[key] = rewrite_st(row.get(key), local, removed)
            for term in (row.get("condition") or {}).get("terms", []):
                term["left"] = rewrite_st(term.get("left"), local, removed)
                if (term.get("right") or {}).get("kind") == "variable":
                    term["right"]["value"] = rewrite_st(term["right"].get("value"), local, removed)
            if (row.get("else_value") or {}).get("kind") == "variable":
                row["else_value"]["value"] = rewrite_st(row["else_value"].get("value"), local, removed)
            if removed:
                changes.append(f"{label} : recopie retirée ({old.get('source')} → {old.get('destination')})")
            else:
                result.append(row)
                if row != old: changes.append(f"{label} : recopie mise à jour ({row['source']} → {row['destination']})")
        if result != before:
            fn["mappings"] = result
            sync_exchanges = True
    if roots:
        for t in state.get("types", []):
            fb = t.get("fb", {})
            for field in ("body", "custom_code"):
                if _code(fb, field, roots, fb.get("name"), errors): changes.append(f"{fb.get('name')} : références globales mises à jour")
        for fn in state.get("functions", []):
            for field in ("body", "custom_code"):
                if _code(fn, field, roots, fn.get("name"), errors): changes.append(f"{fn.get('name')} : références globales mises à jour")
        code = state.get("generated_custom_code") or {}
        for name in code:
            if _code(code, name, roots, name, errors): changes.append(f"{name} : références globales mises à jour")
    if sync_exchanges: _sync(state)
    if sync_sheets: synchronize_workbooks(state)
    return errors, list(dict.fromkeys(changes))
