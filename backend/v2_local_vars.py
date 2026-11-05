"""Reviewed VAR declarations owned by a single program block."""
import re

STANDARD_FB = {"TON", "TOF", "TP", "R_TRIG", "F_TRIG", "CTU", "CTD", "CTUD"}


def normalize(rows, state):
    from backend.v2_type_creation import _iec_type_error, _identifier, IEC_SCALARS
    if len(rows) > 200: raise ValueError("Un bloc accepte jusqu’à 200 variables ajoutées.")
    known = {str(d["name"]) for t in state.get("types", []) for d in t.get("duts", [])}
    known.update(o["name"] for o in (state.get("snapshot") or {}).get("objects", [])
                 if o.get("name") and str(o["name"]).startswith(("DUT_", "FB_")))
    known.update(t.get("fb", {}).get("name") for t in state.get("types", []))
    seen, result = set(), []
    for row in rows:
        name, iec = (str(row.get(k) or "").strip() for k in ("name", "iec_type"))
        default = str(row.get("default_value") if row.get("default_value") is not None else "").strip()
        if (not _identifier(name) or name.upper() in (IEC_SCALARS | {"VAR", "END_VAR", "TRUE", "FALSE", "IF", "ELSE", "THEN", "FOR", "WHILE", "END_IF", "FUNCTION", "RETURN", "ARRAY", "STRUCT"})):
            raise ValueError(f"Nom de variable interne invalide : {name!r}")
        if name.casefold() in seen: raise ValueError(f"Variable interne en double : {name}")
        seen.add(name.casefold())
        issue = _iec_type_error(iec, known)
        if issue: raise ValueError(f"{name} : {issue}")
        if default and iec.upper() == "BOOL" and default.upper() not in {"TRUE", "FALSE", "0", "1"}:
            raise ValueError(f"{name} : la valeur BOOL doit être TRUE ou FALSE.")
        if re.search(r"[;\r\n]", default):
            raise ValueError(f"{name} : indiquez une valeur initiale sur une seule ligne.")
        result.append({"name": name, "iec_type": iec, "default_value": default})
    return result


def append(pou, rows, imported=()):
    if pou is None or not (rows or imported): return
    from lxml import etree
    from backend.v2_plc import NS, _member_variable
    interface = pou.find(NS + "interface")
    if interface is None: interface = etree.SubElement(pou, NS + "interface")
    for group in interface.findall(NS + "localVars"):
        for var in group.findall(NS + "variable"):
            if str(var.get("name")).casefold() in imported:
                group.remove(var)
        if not len(group) and not group.attrib: interface.remove(group)
    if not rows: return
    group = next((g for g in interface.findall(NS + "localVars") if not g.attrib), None)
    if group is None: group = etree.SubElement(interface, NS + "localVars")
    reserved = {str(v.get("name")).casefold() for v in interface.findall("*/" + NS + "variable")}
    reserved.add(str(pou.get("name")).casefold())
    for row in rows:
        name = row["name"]
        if name.casefold() in reserved:
            raise ValueError(f"{pou.get('name')} : la variable {name} est déjà déclarée.")
        group.append(_member_variable(row))
        reserved.add(name.casefold())


def models(state):
    result = dict(state.get("generated_local_variables") or {})
    for model in [*(t.get("fb", {}) for t in state.get("types", [])), *state.get("functions", []), *state.get("exchange_functions", [])]:
        if model.get("local_variables"):
            result[model.get("name") or model.get("FC")] = model["local_variables"]
    return result


def imported_ownership(current, incoming):
    """Track reimported generator VARs so their source copy does not mask edits."""
    from backend.v2_plc import NS, _member_variable
    from lxml import etree
    tracked = dict((current.get("snapshot") or {}).get("generator_locals") or {})
    owned = models(current)
    def signature(node):
        return (etree.QName(node).localname, dict(node.attrib), tuple(signature(ch) for ch in node if isinstance(ch.tag, str)))
    for name, obj in incoming.items():
        managed = {row["name"].casefold(): signature(_member_variable(row)) for row in owned.get(name, [])}
        matches = [key for group in obj.findall(NS + "interface/" + NS + "localVars")
                   for var in group.findall(NS + "variable")
                   if (key := str(var.get("name")).casefold()) in managed and signature(var) == managed[key]]
        tracked.pop(name, None)
        if matches: tracked[name] = matches
    return tracked


def validate_removal(before, after, name, *code):
    from backend.v2_member_sync import rewrite_st
    present = {row["name"].casefold() for row in after}
    deleted = {row["name"].casefold(): None for row in before if row["name"].casefold() not in present}
    if not deleted: return
    used = set()
    for fragment in code:
        rewrite_st(fragment, {"": deleted}, used)
    if used: raise ValueError(f"{name} : le ST utilise encore {', '.join(sorted(used))}. Corrigez le code avant de retirer la déclaration.")
