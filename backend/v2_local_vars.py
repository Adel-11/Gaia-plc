"""Reviewed VAR declarations owned by a single program block."""
import re

# CODESYS Standard.library, https://content.helpme-codesys.com/en/libs/Standard/Current/
TIMING = ("IN PT", "Q ET")
STANDARD_FB = {
    "TON": TIMING, "TOF": TIMING, "TP": TIMING,
    "R_TRIG": ("CLK", "Q"), "F_TRIG": ("CLK", "Q"),
    "RS": ("SET RESET1", "Q1"), "SR": ("SET1 RESET", "Q1"),
    "CTU": ("CU RESET PV", "Q CV"), "CTD": ("CD LOAD PV", "Q CV"),
    "CTUD": ("CU CD RESET LOAD PV", "QU QD CV"), "RTC": ("EN PDT", "Q CDT"),
}
TYPE_GROUPS = {
    "Booléens et bits": "BOOL BIT BYTE WORD DWORD LWORD",
    "Entiers": "SINT INT DINT LINT USINT UINT UDINT ULINT __XINT __UXINT __XWORD",
    "Réels": "REAL LREAL",
    "Dates et durées": "TIME T LTIME DATE DATE_AND_TIME DT TIME_OF_DAY TOD LDATE LDATE_AND_TIME LDT LTIME_OF_DAY LTOD",
    "Chaînes et versions": "STRING WSTRING VERSION",
}
TYPE_FORMS = ["STRING(80)", "WSTRING(80)", "ARRAY[0..9] OF INT", "POINTER TO INT",
              "REFERENCE TO INT", "INT(-10..10)", "__VECTOR[4] OF REAL"]
SCALARS = {name for group in TYPE_GROUPS.values() for name in group.split()}
XML_ALIASES = {"DATE_AND_TIME": "DT", "TIME_OF_DAY": "TOD", "T": "TIME"}
EXTENSIONS = {"BIT", "LTIME", "LDATE", "LDATE_AND_TIME", "LDT", "LTIME_OF_DAY", "LTOD",
              "__XINT", "__UXINT", "__XWORD", "VERSION"}
IDENTIFIER = r"[A-Za-z_]\w*"
QUALIFIED = rf"{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})*"
ARRAY = re.compile(r"ARRAY\s*\[([^\[\]]+)\]\s*OF\s+(.+)", re.I)
INDIRECT = re.compile(r"(POINTER|REFERENCE)\s+TO\s+(.+)", re.I)
STRING = re.compile(r"(W?STRING)(?:\s*\(\s*(\d+|[A-Za-z_]\w*)\s*\)|\s*\[\s*(\d+|[A-Za-z_]\w*)\s*\])?", re.I)
RANGE = re.compile(r"(U?(?:S|D|L)?INT)\s*\(\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*\)", re.I)
VECTOR = re.compile(r"__VECTOR\s*\[\s*[1-8]\s*\]\s*OF\s+L?REAL", re.I)
BOUND = r"(?:-?\d+|[+-]?" + QUALIFIED + ")"
ST_STRING = re.compile(r"""'(?:\$[^\r\n]|[^'$\r\n])*'|"(?:\$[^\r\n]|[^"$\r\n])*" """, re.X)


def valid_type(text, depth=0):
    if depth > 12 or len(text) > 250:
        return False
    if text.upper().startswith("ANY") and re.fullmatch(r"ANY(?:_[A-Z]+)?", text, re.I):
        return False  # Generic interface parameters cannot be declared in local VAR.
    if re.fullmatch(QUALIFIED, text) or STRING.fullmatch(text) or VECTOR.fullmatch(text):
        return True
    if bounds := RANGE.fullmatch(text):
        low, high = map(int, bounds.groups()[1:])
        return low <= high and (not bounds[1].upper().startswith("U") or low >= 0)
    if indirection := INDIRECT.fullmatch(text):
        return valid_type(indirection[2].strip(), depth + 1)
    if array := ARRAY.fullmatch(text):
        for dimension in array[1].split(","):
            bounds = dimension.split("..")
            if len(bounds) != 2 or not all(re.fullmatch(BOUND, bound.strip()) for bound in bounds):
                return False
            low, high = (bound.strip() for bound in bounds)
            if re.fullmatch(r"-?\d+", low) and re.fullmatch(r"-?\d+", high) and int(low) > int(high):
                return False
        return valid_type(array[2].strip(), depth + 1)
    return False


def type_element(text):
    """Return the PLCopen type and whether CODESYS needs its plain ST form."""
    from lxml import etree
    from backend.v2_plc import NS
    text = text.strip()
    if array := ARRAY.fullmatch(text):
        node = etree.Element(NS + "array")
        for dimension in array[1].split(","):
            low, high = (bound.strip() for bound in dimension.split(".."))
            if not all(re.fullmatch(r"-?\d+", bound) for bound in (low, high)):
                return etree.Element(NS + "derived", name=text), True
            etree.SubElement(node, NS + "dimension", lower=low, upper=high)
        child, plain = type_element(array[2])
        etree.SubElement(node, NS + "baseType").append(child)
        return node, plain
    if (indirect := INDIRECT.fullmatch(text)) and indirect[1].upper() == "POINTER":
        child, plain = type_element(indirect[2])
        node = etree.Element(NS + "pointer")
        etree.SubElement(node, NS + "baseType").append(child)
        return node, plain
    if string := STRING.fullmatch(text):
        node = etree.Element(NS + string[1].lower())
        length = string[2] or string[3]
        if length:
            node.set("length", length)
        return node, False
    if bounds := RANGE.fullmatch(text):
        kind = "subrangeUnsigned" if bounds[1].upper().startswith("U") else "subrangeSigned"
        node = etree.Element(NS + kind)
        etree.SubElement(node, NS + "range", lower=bounds[2], upper=bounds[3])
        etree.SubElement(node, NS + "baseType").append(etree.Element(NS + bounds[1].upper()))
        return node, False
    upper = text.upper()
    if upper in SCALARS - EXTENSIONS:
        return etree.Element(NS + XML_ALIASES.get(upper, upper)), False
    if upper in EXTENSIONS or INDIRECT.fullmatch(text) or VECTOR.fullmatch(text):
        return etree.Element(NS + "derived", name=text), True
    return etree.Element(NS + "derived", name=re.sub(r"\s*\.\s*", ".", text)), False


def normalize(rows, _state):
    from backend.v2_type_creation import _identifier
    if len(rows) > 200: raise ValueError("Un bloc accepte jusqu’à 200 variables ajoutées.")
    seen, result = set(), []
    for row in rows:
        name, iec = (str(row.get(k) or "").strip() for k in ("name", "iec_type"))
        default = str(row.get("default_value") if row.get("default_value") is not None else "").strip()
        if not _identifier(name) or name.upper() in SCALARS | {"THEN", "END_VAR", "POINTER", "REFERENCE"}:
            raise ValueError(f"Nom de variable interne invalide : {name!r}")
        if name.casefold() in seen: raise ValueError(f"Variable interne en double : {name}")
        seen.add(name.casefold())
        if not valid_type(iec):
            raise ValueError(f"{name} : type IEC invalide dans VAR : {iec!r}.")
        if default and iec.upper() == "BOOL" and default.upper() not in {"TRUE", "FALSE", "0", "1"}:
            raise ValueError(f"{name} : la valeur BOOL doit être TRUE ou FALSE.")
        if re.search(r"""[;'"\r\n]|//|\(\*|\*\)""", ST_STRING.sub("", default)):
            raise ValueError(f"{name} : indiquez une expression ST sur une seule ligne pour la valeur initiale.")
        result.append({"name": name, "iec_type": iec, "default_value": default})
    return result


def _variable(row):
    from backend.v2_plc import NS, _member_variable
    node, needs_text = type_element(row["iec_type"])
    variable = _member_variable({**row, "iec_type": "BOOL"})
    variable.find(NS + "type")[0] = node
    return variable, needs_text or str(row.get("default_value") or "").startswith(("[", "("))


def append(pou, rows, imported=()):
    if pou is None or not (rows or imported): return
    from lxml import etree
    from backend.v2_plc import NS
    from backend.v2_st_overrides import plain_declaration, apply_overrides
    previous_text = plain_declaration(pou)
    interface = pou.find(NS + "interface")
    if interface is None: interface = etree.SubElement(pou, NS + "interface")
    for group in interface.findall(NS + "localVars"):
        for var in group.findall(NS + "variable"):
            if str(var.get("name")).casefold() in imported:
                group.remove(var)
        if not len(group) and not group.attrib: interface.remove(group)
    group = next((g for g in interface.findall(NS + "localVars") if not g.attrib), None)
    if rows and group is None: group = etree.SubElement(interface, NS + "localVars")
    reserved = {str(v.get("name")).casefold() for v in interface.findall("*/" + NS + "variable")}
    reserved.add(str(pou.get("name")).casefold())
    needs_text = False
    for row in rows or ():
        name = row["name"]
        if name.casefold() in reserved:
            raise ValueError(f"{pou.get('name')} : la variable {name} est déjà déclarée.")
        variable, requires_text = _variable(row)
        group.append(variable)
        needs_text |= requires_text
        reserved.add(name.casefold())
    if previous_text is not None:
        if imported:
            owned = "|".join(re.escape(name) for name in imported)
            previous_text = re.sub(rf"(?im)^\s*(?:{owned})\s*:[^\r\n]*;[^\r\n]*(?:\r?\n|$)", "", previous_text)
        if rows:
            lines = [f"    {row['name']} : {row['iec_type']}" +
                     (f" := {row['default_value']}" if row.get("default_value") else "") + ";" for row in rows]
            previous_text = previous_text.rstrip() + "\n\nVAR\n" + "\n".join(lines) + "\nEND_VAR"
        apply_overrides({"element": pou}, {"declaration": previous_text})
    elif needs_text:
        from backend.v2_code import _decl
        apply_overrides({"element": pou}, {"declaration": _decl({"element": pou})})


def variables_in(pou, groups):
    """Read visible variable names and types from a PLCopen POU interface."""
    from backend.v2_plc import NS
    from backend.v2_codesys import _type_text
    if pou is None:
        return []
    return [{"path": var.get("name"), "iec_type": _type_text(var.find(NS + "type/*"))}
            for group in groups
            for var in pou.findall(NS + "interface/" + NS + group + "/" + NS + "variable")
            if var.get("name")]


def catalog(state):
    from lxml import etree
    from backend.v2_plc import _source
    from backend.v2_st_overrides import plain_declaration
    from backend.v2_plc_validation import _st_code
    groups = ("inputVars", "inOutVars", "outputVars")
    labels = ("VAR_INPUT", "VAR_IN_OUT", "VAR_OUTPUT")
    types, blocks, functions = set(), {}, {}
    exchanges = {row.get("name") or row.get("FC") for row in state.get("exchange_functions", [])}
    raw = (state.get("snapshot") or {}).get("xml")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    roots = [_source(state)]
    if raw:
        roots.append(etree.fromstring(raw.encode("utf-8") if isinstance(raw, str) else raw, parser))
    for root in roots:
        types.update(node.get("name") for node in root.xpath("//*[local-name()='dataType' and @name]"))
        for pou in root.xpath("//*[local-name()='pou' and (@pouType='functionBlock' or @pouType='function') and @name]"):
            name = pou.get("name")
            function = pou.get("pouType") == "function"
            if function and (name in exchanges or name.startswith(("FC_EXC_", "GFC_"))):
                continue
            declaration = (state.get("st_overrides") or {}).get(name, {}).get("declaration")
            declaration = declaration if declaration is not None else plain_declaration(pou)
            pins = []
            for group, label in zip(groups, labels):
                if declaration is None:
                    names = [row["path"] for row in variables_in(pou, [group])]
                else:
                    sections = re.findall(rf"(?is)\b{label}\b(.*?)\bEND_VAR\b", _st_code(declaration))
                    names = [name.strip() for section in sections for row in re.findall(
                        rf"(?im)(?:^|;)\s*({IDENTIFIER}(?:\s*,\s*{IDENTIFIER})*)\s*:(?!=)", section)
                        for name in row.split(",")]
                pins.extend({"name": pin, "output": group == "outputVars"} for pin in names)
            if function:
                from backend.v2_codesys import _type_text
                from backend.v2_plc import NS
                result = pou.find(f"{NS}interface/{NS}returnType/*")
                functions[name] = {"pins": pins, "result": _type_text(result) if result is not None else ""}
            else:
                blocks[name] = pins
    for typ in state.get("types", []):
        types.update(d["name"] for d in typ.get("duts", []) if d.get("name"))
        fb = typ.get("fb") or {}
        if fb.get("name") and fb["name"] not in blocks:
            from backend.v2_plc import _new_fb
            pou = _new_fb(fb["name"], typ)
            blocks[fb["name"]] = [{"name": row["path"], "output": group == "outputVars"}
                                  for group in groups for row in variables_in(pou, [group])]
    return {
        "groups": {**{name: group.split() for name, group in TYPE_GROUPS.items()},
                   "Types composés": TYPE_FORMS, "Blocs Standard": list(STANDARD_FB),
                   "Types du projet": sorted(types), "Blocs du projet": sorted(blocks)},
        "blocks": {**{name: [{"name": pin, "output": output} for output, group in zip((False, True), pins)
                            for pin in group.split()] for name, pins in STANDARD_FB.items()}, **blocks},
        "functions": functions,
    }


def models(state):
    result = dict(state.get("generated_local_variables") or {})
    for model in [*(t.get("fb", {}) for t in state.get("types", [])), *state.get("functions", []), *state.get("exchange_functions", [])]:
        if model.get("local_variables"):
            result[model.get("name") or model.get("FC")] = model["local_variables"]
    return result


def imported_ownership(current, incoming):
    """Track reimported generator VARs so their source copy does not mask edits."""
    from backend.v2_plc import NS
    from lxml import etree
    tracked = dict((current.get("snapshot") or {}).get("generator_locals") or {})
    owned = models(current)
    def signature(node):
        return (etree.QName(node).localname, dict(node.attrib), tuple(signature(ch) for ch in node if isinstance(ch.tag, str)))
    for name, obj in incoming.items():
        managed = {row["name"].casefold(): signature(_variable(row)[0]) for row in owned.get(name, [])}
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
