"""PLCopen free-object generator for the v2 workbook model."""
from __future__ import annotations

import copy
import hashlib
import re
import uuid
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "reference-v2" / "03_plc" / "Gaia_V7_reference_full.xml"
PLC = "http://www.plcopen.org/xml/tc6_0200"
HTML = "http://www.w3.org/1999/xhtml"
NS = "{" + PLC + "}"
HNS = "{" + HTML + "}"
ODATA = "http://www.3s-software.com/plcopenxml/objectid"
STRUCT = "http://www.3s-software.com/plcopenxml/projectstructure"
GVL_DATA = "http://www.3s-software.com/plcopenxml/globalvars"
ATTR = "http://www.3s-software.com/plcopenxml/attributes"


def _local(el): return etree.QName(el).localname
def _children(el, tag): return [x for x in el if _local(x) == tag]
def _id(el):
    for d in el.xpath(".//plc:addData/plc:data[@name=$n]/plc:ObjectId", namespaces={"plc": PLC}, n=ODATA):
        return d.text
    return None


def _source():
    return etree.parse(str(REF)).getroot()


def _source_objects(root):
    result = {}
    types = root.find(NS + "types")
    for group in (types.find(NS + "dataTypes"), types.find(NS + "pous")):
        if group is not None:
            for el in group:
                result[el.get("name")] = el
    for data in root.findall(NS + "addData/" + NS + "data"):
        g = data.find(NS + "globalVars")
        if g is not None: result[g.get("name")] = g
    return result


def _stable_id(name, state):
    ids = (state.get("snapshot") or {}).get("object_ids", {})
    return ids.get(name) or str(uuid.uuid5(uuid.NAMESPACE_URL, "gaia-plc:" + name))


def _set_object_id(el, name, state):
    ad = el.find(NS + "addData")
    if ad is None: ad = etree.SubElement(el, NS + "addData")
    d = next((x for x in ad.findall(NS + "data") if x.get("name") == ODATA), None)
    if d is None:
        d = etree.SubElement(ad, NS + "data", name=ODATA, handleUnknown="discard")
        etree.SubElement(d, NS + "ObjectId")
    d.find(NS + "ObjectId").text = _stable_id(name, state)


def _type_tag(iec):
    text = str(iec or "BOOL").strip()
    arr = re.fullmatch(r"ARRAY\s*\[\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*\]\s*OF\s*(\w+)", text, re.I)
    if arr:
        a = etree.Element(NS + "array"); etree.SubElement(a, NS + "dimension", lower=arr[1], upper=arr[2])
        b = etree.SubElement(a, NS + "baseType"); etree.SubElement(b, NS + arr[3].upper()); return a
    if text.upper().startswith("DUT_"):
        return etree.Element(NS + "derived", name=text)
    return etree.Element(NS + re.sub(r"\s*", "", text).upper())


def _member_variable(member, section_start=False):
    v = etree.Element(NS + "variable", name=str(member.get("name", "")))
    etree.SubElement(v, NS + "type").append(_type_tag(member.get("iec_type")))
    value = member.get("default_value")
    if value not in (None, ""):
        initial = etree.SubElement(v, NS + "initialValue")
        etree.SubElement(initial, NS + "simpleValue", value=str(value))
    comment = member.get("comment")
    if section_start and member.get("section"):
        heading = f"//----------------------------{member['section']}"
        comment = f"{heading}\n{comment}" if comment else heading
    if comment:
        doc = etree.SubElement(v, NS + "documentation"); etree.SubElement(doc, HNS + "xhtml", nsmap={None: HTML}).text = str(comment)
    return v


def _build_dut(name, typ, role, state, template):
    el = copy.deepcopy(template) if template is not None else etree.Element(NS + "dataType", name=name)
    el.set("name", name)
    struct = el.find(NS + "baseType/" + NS + "struct")
    if struct is None:
        base = el.find(NS + "baseType")
        if base is None: base = etree.SubElement(el, NS + "baseType")
        struct = etree.SubElement(base, NS + "struct")
    # The structured workbook declaration is authoritative for members and their order.
    for child in list(struct): struct.remove(child)
    model = next((d for d in typ.get("duts", []) if d.get("role") == role), None)
    members = (model or {}).get("members", [])
    if not members:
        members = [{"name": "SPARE", "iec_type": "BOOL"}]
    previous_section=object()
    for m in sorted(members, key=lambda x: x.get("position", 0)):
        section=m.get("section")
        struct.append(_member_variable(m, section != previous_section))
        previous_section=section
    return el


def _new_fb(name, typ):
    el=etree.Element(NS+"pou",name=name,pouType="functionBlock")
    interface=etree.SubElement(el,NS+"interface")
    ins=etree.SubElement(interface,NS+"inOutVars")
    upi="UPI" in str(typ.get("kind","")).upper()
    for role in (["COM","EXC"] if upi else ["PA","COM","EXC","IO"]):
        dut=next((d for d in typ.get("duts",[]) if d.get("role")==role),None)
        if dut is not None:
            v=etree.SubElement(ins,NS+"variable",name=role); etree.SubElement(v,NS+"type").append(etree.Element(NS+"derived",name=dut.get("name")))
    intern=next((d for d in typ.get("duts",[]) if d.get("role")=="INT"),None)
    if intern is not None:
        locals_=etree.SubElement(interface,NS+"localVars"); v=etree.SubElement(locals_,NS+"variable",name="INTERN"); etree.SubElement(v,NS+"type").append(etree.Element(NS+"derived",name=intern.get("name")))
    raw_interface = typ.get("fb", {}).get("interface_xml")
    if raw_interface:
        preserved = etree.fromstring(str(raw_interface).encode(), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
        if preserved.tag != NS + "interface": raise ValueError("Invalid preserved FB interface")
        el.replace(interface, preserved)
    body=etree.SubElement(el,NS+"body"); st=etree.SubElement(body,NS+"ST"); xhtml=etree.SubElement(st,HNS+"xhtml",nsmap={None:HTML})
    xhtml.text="// INPUTS\n\n// PROCESSING\n\n// OUTPUTS\n"
    return el


def _new_exchange_function(fn, types):
    name=str(fn.get("name") or fn.get("FC") or "")
    el=etree.Element(NS+"pou",name=name,pouType="function")
    interface=etree.SubElement(el,NS+"interface"); ret=etree.SubElement(interface,NS+"returnType"); etree.SubElement(ret,NS+"BOOL")
    inout=etree.SubElement(interface,NS+"inOutVars")
    for key,fallback in (("type_a_id","A"),("type_b_id","B")):
        t=types.get(str(fn.get(key) or fn.get("Object type " + fallback) or ""))
        arg=fn.get("parameter_a" if fallback=="A" else "parameter_b") or fn.get("Parameter " + fallback) or f"EXC_{t['name'] if t else fallback}"
        v=etree.SubElement(inout,NS+"variable",name=str(arg)); dut=next((d for d in t.get("duts",[]) if d.get("role")=="EXC"),None) if t else None
        etree.SubElement(v,NS+"type").append(etree.Element(NS+"derived",name=dut.get("name") if dut else f"DUT_EXC_{t['name'] if t else fallback}"))
    body=etree.SubElement(el,NS+"body"); st=etree.SubElement(body,NS+"ST"); etree.SubElement(st,HNS+"xhtml",nsmap={None:HTML})
    return el


def _folder_map(root):
    out = {}
    ps = next((x for x in root.iter() if _local(x) == "ProjectStructure"), None)
    def walk(el, path=()):
        for ch in el:
            if _local(ch) == "Folder": walk(ch, path + (ch.get("Name", ""),))
            elif _local(ch) == "Object": out[ch.get("Name")] = "/".join(path)
    if ps is not None: walk(ps)
    return out


def _pou_text(obj, text):
    body = obj["element"].find(NS + "body/" + NS + "ST/" + HNS + "xhtml")
    if body is not None: body.text = text


def _append_custom(obj, code, function_name=None):
    if not code: return
    body=obj["element"].find(NS+"body/"+NS+"ST/"+HNS+"xhtml")
    if body is None: return
    text=body.text or ""
    text=re.sub(r"\n// BEGIN CUSTOM CODE\n.*?\n// END CUSTOM CODE\n?","\n",text,flags=re.S)
    block="// BEGIN CUSTOM CODE\n"+str(code)+"\n// END CUSTOM CODE\n"
    if function_name:
        lines=text.splitlines(keepends=True)
        marker=next((i for i,line in enumerate(lines) if re.match(r"\s*"+re.escape(function_name)+r"\s*:=",line)),None)
        if marker is not None:
            lines.insert(marker,block)
            body.text="".join(lines); return
    body.text=text+"\n"+block


def _sync_gfb_instances(pou, pairs, upi=False):
    interface=pou.find(NS+"interface")
    if interface is None: interface=etree.SubElement(pou,NS+"interface")
    local=interface.find(NS+"localVars")
    if local is None: local=etree.SubElement(interface,NS+"localVars")
    expected={f"FB_{str(i.get('name') or i.get('id'))}{'_1' if upi else ''}": t.get("fb",{}).get("name") for i,t in pairs}
    for v in list(local.findall(NS+"variable")):
        if v.get("name","").startswith("FB_") and v.get("name") not in expected: local.remove(v)
    existing={v.get("name") for v in local.findall(NS+"variable")}
    for name, fbtype in expected.items():
        if name in existing: continue
        var=etree.SubElement(local,NS+"variable",name=name); etree.SubElement(var,NS+"type").append(etree.Element(NS+"derived",name=fbtype))


def _reference_com_rank(pou_name):
    source=_source_objects(_source()).get(pou_name)
    if source is None: return {}
    body=source.find(NS+"body/"+NS+"ST/"+HNS+"xhtml")
    ranks={}
    for index,line in enumerate((body.text or "").splitlines() if body is not None else []):
        if ":=" not in line: continue
        lhs,rhs=(part.strip() for part in line.split(":=",1))
        lhs=lhs.split("//",1)[0].strip().rstrip(";")
        rhs=rhs.split("//",1)[0].strip().rstrip(";")
        com=lhs if lhs.startswith("GVL_COM.") else rhs if rhs.startswith("GVL_COM.") else ""
        if com: ranks.setdefault(com.removeprefix("GVL_COM."),index)
    return ranks


def _com_key(value):
    value=str(value or "").removeprefix("GVL_COM.")
    return re.sub(r"(COM_(?:INPUT|OUTPUT)_[A-Z0-9_]+)\[[^]]+\](?:\.\d+)?$",r"\1",value)


def _add_gvl_variable(gvl, name, iec_type):
    if gvl is None or not name or any(v.get("name")==name for v in gvl.findall(NS+"variable")): return
    variable=etree.Element(NS+"variable",name=name); etree.SubElement(variable,NS+"type").append(_type_tag(iec_type))
    adddata=gvl.find(NS+"addData"); gvl.insert(gvl.index(adddata) if adddata is not None else len(gvl),variable)


def _ensure_mapped_declarations(objs, state, valid_instances):
    """Declare workbook-named mapped variables with types inferred from their model row."""
    modbus=objs.get("GVL_MODBUS",{}).get("element"); iomap=objs.get("GVL_IOMAP",{}).get("element")
    types={str(t.get("name")):t for t in state.get("types",[])}
    instances={str(i.get("name") or i.get("id")):i for i in state.get("instances",[]) if str(i.get("name") or i.get("id")) in valid_instances}
    tcp_base_type={"coils":"ARRAY[0..187] OF BYTE","discrete_inputs":"ARRAY[0..187] OF BYTE","input_registers":"ARRAY[0..1500] OF WORD","holding_registers":"ARRAY[0..1500] OF WORD"}
    for row in state.get("tcp_points",[]):
        inst=str(row.get("Instance") or row.get("instance_id") or "")
        if inst not in valid_instances: continue
        expression=_tcp_address_expression(row,state); base=re.match(r"([A-Za-z_][A-Za-z0-9_]*)",expression)
        if base and modbus is not None: _add_gvl_variable(modbus,base.group(1),tcp_base_type.get(str(row.get("table","")).lower(),row.get("Type","BOOL")))
    for row in state.get("rtu_channels",[]):
        inst=str(row.get("instance") or row.get("instance_id") or "")
        if inst not in valid_instances: continue
        raw=str(row.get("GVL_MODBUS variable") or "").removeprefix("GVL_MODBUS.")
        match=re.match(r"([A-Za-z_][A-Za-z0-9_]*)",raw)
        if not match or modbus is None: continue
        base=match.group(1); com=str(row.get("GVL_COM variable") or "").removeprefix("GVL_COM.")
        parts=com.split("."); typ=types.get(str(instances.get(inst,{}).get("type_id")))
        member_name=re.sub(r"\[.*$","",parts[1]) if len(parts)>1 else ""
        member=next((m for d in (typ or {}).get("duts",[]) if d.get("role")=="COM" for m in d.get("members",[]) if m.get("name")==member_name),None)
        if row.get("manual_scalar"):
            _add_gvl_variable(modbus,base,row.get("Type") or "BOOL")
        elif member is not None: _add_gvl_variable(modbus,base,member.get("iec_type"))
    for row in state.get("io_channels",[]):
        inst=str(row.get("Instance") or row.get("instance_id") or "")
        if inst and inst not in valid_instances: continue
        raw=str(row.get("GVL_IOMAP variable") or "").removeprefix("GVL_IOMAP.")
        match=re.match(r"([A-Za-z_][A-Za-z0-9_]*)",raw)
        if match and iomap is not None: _add_gvl_variable(iomap,match.group(1),row.get("IOMAP type") or row.get("Type") or "BOOL")
    for row in state.get("virtual_io",[]):
        raw=str(row.get("GVL_IOMAP variable") or "").removeprefix("GVL_IOMAP.")
        match=re.match(r"([A-Za-z_][A-Za-z0-9_]*)",raw)
        if match and iomap is not None: _add_gvl_variable(iomap,match.group(1),row.get("IOMAP type") or row.get("iec_type") or "BOOL")


def _mapped_name(value):
    return str(value or "").removeprefix("GVL_MODBUS.").removeprefix("GVL_IOMAP.")


def _tcp_address_expression(point, state):
    """Use the mapped array's existing name while deriving its index from the current address."""
    raw = str(point.get("GVL_MODBUS variable") or "")
    match = re.match(r"(?:GVL_MODBUS\.)?([A-Za-z_][A-Za-z0-9_]*)", raw)
    table = str(point.get("table") or point.get("sheet") or "").lower()
    known = {"coils": "Coils_TCP_HMI", "discrete_inputs": "Discret_input_TCP_HMI",
             "input_registers": "Input_register_TCP_HMI", "holding_registers": "Holding_register_TCP_HMI"}
    base = match.group(1) if match else next((
        re.match(r"(?:GVL_MODBUS\.)?([A-Za-z_][A-Za-z0-9_]*)", str(p.get("GVL_MODBUS variable") or "")).group(1)
        for p in state.get("tcp_points", []) if str(p.get("table") or p.get("sheet", "")).lower() == table
        and re.match(r"(?:GVL_MODBUS\.)?([A-Za-z_][A-Za-z0-9_]*)", str(p.get("GVL_MODBUS variable") or ""))), known.get(table))
    try: address = int(point.get("Modbus address", point.get("address")))
    except (TypeError, ValueError): return raw.removeprefix("GVL_MODBUS.")
    if table in {"coils", "discrete_inputs"}:
        zero = address - (10001 if table == "discrete_inputs" else 1)
        if zero < 0: return raw.removeprefix("GVL_MODBUS.")
        return f"{base}[{zero // 8}].{zero % 8}"
    return f"{base}[{address - 1}]"


def _generated_objects(objs, state):
    """Refresh generated calls and copy POUs from current workbook rows."""
    from backend.v2_program_membership import freeze_membership
    legacy_membership = not isinstance(state.get("program_membership"), dict)
    membership = freeze_membership(state)
    types = {str(t.get("name")): t for t in state.get("types", [])}
    instances = state.get("instances", [])
    equips, upis = [], []
    for inst in instances:
        nm = str(inst.get("name") or inst.get("id")); typ = types.get(str(inst.get("type_id")))
        if not typ: continue
        if "UPI" in str(typ.get("kind", "")).upper(): upis.append((inst, typ))
        else: equips.append((inst, typ))
    equips.sort(key=lambda pair: (str(pair[1].get("folder", "")), str(pair[0].get("name", ""))))
    upis.sort(key=lambda pair: (str(pair[1].get("folder", "")), str(pair[0].get("name", ""))))
    valid_instances={str(i.get("name") or i.get("id")) for i,_ in equips+upis}
    instance_name_by_id={str(i.get("id")):str(i.get("name") or i.get("id")) for i,_ in equips+upis}
    def included(block, identifier):
        value=str(identifier or "")
        name=instance_name_by_id.get(value,value)
        return name in set(membership.get(block, []))
    _ensure_mapped_declarations(objs,state,valid_instances)
    # Keep global variable names and declared types in sync with model instances.
    for gvl, role in (("GVL_COM", "COM"), ("GVL_EXC", "EXC"), ("GVL_IO", "IO"), ("GVL_PA", "PA")):
        el = objs.get(gvl, {}).get("element")
        if el is None: continue
        original_names = {v.get("name") for v in el.findall(NS + "variable")}
        existing = {v.get("name") for v in el.findall(NS + "variable")}
        pairs = upis + equips if role in ("COM", "EXC") else equips
        wanted = {f"{role}_{str(i.get('name') or i.get('id'))}" for i, _ in pairs}
        for var in list(el.findall(NS + "variable")):
            if var.get("name", "").startswith(role + "_") and var.get("name") not in wanted:
                el.remove(var); existing.discard(var.get("name"))
        for inst, typ in pairs:
            nm = str(inst.get("name") or inst.get("id")); varname = f"{role}_{nm}"
            if varname in existing: continue
            dut = next((d for d in typ.get("duts", []) if d.get("role") == role), None)
            if dut is None: continue
            var = etree.Element(NS + "variable", name=varname)
            adddata=el.find(NS+"addData")
            el.insert(el.index(adddata) if adddata is not None else len(el),var)
            etree.SubElement(var, NS + "type").append(etree.Element(NS + "derived", name=dut.get("name")))
            if role == "PA":
                defaults = [m for m in sorted(dut.get("members", []), key=lambda x: x.get("position", 0)) if m.get("default_value") not in (None, "")]
                if defaults:
                    init=etree.SubElement(var,NS+"initialValue"); struct=etree.SubElement(init,NS+"structValue")
                    for member in defaults:
                        value=etree.SubElement(struct,NS+"value",member=str(member.get("name")))
                        etree.SubElement(value,NS+"simpleValue",value=str(member["default_value"]))
                ad = etree.SubElement(var, NS + "addData"); data = etree.SubElement(ad, NS + "data", name=ATTR, handleUnknown="implementation")
                attrs = etree.SubElement(data, NS + "Attributes"); etree.SubElement(attrs, NS + "Attribute", Name="order_in_persistent_editor", Value=str(len(existing)))
            existing.add(varname)
        if role == "PA" and existing != original_names:
            # CODESYS internal initialization markers are valid only while the persistent list is unchanged.
            ad = el.find(NS + "addData")
            if ad is not None:
                for data in ad.findall(NS+"data"):
                    if data.get("name") != ATTR: continue
                    attrs=next(iter(data.findall(NS+"Attributes")),None)
                    if attrs is not None:
                        for attribute in list(attrs.findall(NS+"Attribute")):
                            if attribute.get("Name") in {"checksumnoinit_override", "init_related_code"}: attrs.remove(attribute)
                        if not len(attrs): ad.remove(data)
            for order, variable in enumerate(el.findall(NS+"variable")):
                vadd=variable.find(NS+"addData")
                if vadd is None: vadd=etree.SubElement(variable,NS+"addData")
                pdata=next((d for d in vadd.findall(NS+"data") if d.get("name")==ATTR),None)
                if pdata is None: pdata=etree.SubElement(vadd,NS+"data",name=ATTR,handleUnknown="implementation")
                attrs=pdata.find(NS+"Attributes")
                if attrs is None: attrs=etree.SubElement(pdata,NS+"Attributes")
                order_attr=next((a for a in attrs.findall(NS+"Attribute") if a.get("Name")=="order_in_persistent_editor"),None)
                if order_attr is None: etree.SubElement(attrs,NS+"Attribute",Name="order_in_persistent_editor",Value=str(order))
                else: order_attr.set("Value",str(order))
    # Regenerate instance FB calls.
    if "GFB_INST" in objs:
        text = "//==============================================================================\n// GFB_INST\n// Calls selected equipment instances\n//==============================================================================\n\n"
        _sync_gfb_instances(objs["GFB_INST"]["element"], equips, upi=False)
        for inst, typ in equips:
            nm = str(inst.get("name") or inst.get("id")); fb_name = typ.get("fb", {}).get("name", "")
            if nm not in membership["GFB_INST"]: continue
            # Equipment FB convention is FB_<instance>.
            text += f"//--- {nm}\nFB_{nm}(\n\tPA  := GVL_PA.PA_{nm},\n\tCOM := GVL_COM.COM_{nm},\n\tEXC := GVL_EXC.EXC_{nm},\n\tIO  := GVL_IO.IO_{nm});\n\n"
        _pou_text(objs["GFB_INST"], text)
    if "GFB_UPI" in objs:
        text = "//==============================================================================\n// GFB_UPI\n// Calls selected functional units (UPI)\n//==============================================================================\n\n"
        _sync_gfb_instances(objs["GFB_UPI"]["element"], upis, upi=True)
        for inst, typ in upis:
            nm = str(inst.get("name") or inst.get("id")); fb_name = typ.get("fb", {}).get("name", "FB_" + nm)
            if nm not in membership["GFB_UPI"]: continue
            text += f"//--- {nm}\n{fb_name}_1(\n\tCOM := GVL_COM.COM_{nm},\n\tEXC := GVL_EXC.EXC_{nm});\n\n"
        _pou_text(objs["GFB_UPI"], text)
    # TCP copy statements use the exact mapped variable identifiers from Excel.
    tcp_in, tcp_out, rtu_in, rtu_out = [], [], [], []
    tcp_points=[p for p in state.get("tcp_points",[]) if str(p.get("Instance") or p.get("instance_id") or "") in valid_instances]
    in_rank=_reference_com_rank("GFC_COM_IN"); out_rank=_reference_com_rank("GFC_COM_OUT")
    def com_rank(p, ranks): return ranks.get(_com_key(p.get("GVL_COM variable")),10**9)
    tcp_in_rows=sorted((p for p in tcp_points if p.get("Direction")=="Client to PLC" and included("GFC_COM_IN",p.get("Instance") or p.get("instance_id"))),key=lambda p:com_rank(p,in_rank))
    tcp_out_rows=sorted((p for p in tcp_points if p.get("Direction")!="Client to PLC" and included("GFC_COM_OUT",p.get("Instance") or p.get("instance_id"))),key=lambda p:com_rank(p,out_rank))
    for p in tcp_in_rows+tcp_out_rows:
        mapped = _tcp_address_expression(p, state); com = str(p.get("GVL_COM variable") or "").removeprefix("GVL_COM.")
        if str(p.get("Word", "")).upper() in {"LSW", "MSW"} and ".Word_value[" in com:
            com = re.sub(r"\.Word_value\[\d+\]", f".Word_value[{0 if str(p.get('Word')).upper() == 'LSW' else 1}]", com)
        if not mapped or not com: continue
        stmt = f"GVL_COM.{com} := GVL_MODBUS.{mapped};" if p.get("Direction") == "Client to PLC" else f"GVL_MODBUS.{mapped} := GVL_COM.{com};"
        (tcp_in if p.get("Direction") == "Client to PLC" else tcp_out).append(stmt)
    # RTU channels are whole arrays, grouped to avoid emitting one copy per register.
    rtu_rows=[p for p in state.get("rtu_channels",[]) if str(p.get("instance") or p.get("instance_id") or "") in valid_instances and included("GFC_COM_IN" if p.get("Direction")=="Equipment to PLC" else "GFC_COM_OUT",p.get("instance") or p.get("instance_id"))]
    rtu_rows=sorted(rtu_rows,key=lambda p:com_rank(p,in_rank if p.get("Direction")=="Equipment to PLC" else out_rank))
    seen_rtu = set()
    for p in rtu_rows:
        a, b = str(p.get("GVL_MODBUS variable") or ""), str(p.get("GVL_COM variable") or "")
        if not a or not b: continue
        a = a.removeprefix("GVL_MODBUS."); b = b.removeprefix("GVL_COM.")
        a = re.sub(r"\[[^]]+\](?:\.\d+)?$", "", a)
        if not p.get("manual_scalar"):
            b = re.sub(r"\[[^]]+\](?:\.\d+)?$", "", b)
        key=(a,b,p.get("Direction"))
        if key in seen_rtu: continue
        seen_rtu.add(key); stmt = f"GVL_COM.{b} := GVL_MODBUS.{a};" if p.get("Direction") == "Equipment to PLC" else f"GVL_MODBUS.{a} := GVL_COM.{b};"
        (rtu_in if p.get("Direction") == "Equipment to PLC" else rtu_out).append(stmt)
    com_in, com_out = rtu_in + tcp_in, rtu_out + tcp_out
    if "GFC_COM_IN" in objs: _pou_text(objs["GFC_COM_IN"], "\n".join(com_in)+"\nGFC_COM_IN := TRUE;")
    if "GFC_COM_OUT" in objs: _pou_text(objs["GFC_COM_OUT"], "\n".join(com_out)+"\nGFC_COM_OUT := TRUE;")
    io_in, io_out = [], []
    io_rows = list(state.get("io_channels",[])) + list(state.get("virtual_io",[]))
    for p in sorted((row for row in io_rows if (not (row.get("Instance") or row.get("instance_id")) and legacy_membership) or (str(row.get("Instance") or row.get("instance_id")) in valid_instances and included("GFC_IO_IN" if str(row.get("I/O type", "")).upper() in {"AI", "DI"} else "GFC_IO_OUT",row.get("Instance") or row.get("instance_id")))), key=lambda row: (0 if str(row.get("I/O type", "")).upper() == "DI" else 1, str(row.get("id", "")))):
        iomap, iovar = str(p.get("GVL_IOMAP variable") or ""), str(p.get("GVL_IO member") or "")
        if not iomap or not iovar: continue
        iomap=iomap.removeprefix("GVL_IOMAP."); iovar=iovar.removeprefix("GVL_IO.")
        stmt = f"GVL_IO.{iovar} := GVL_IOMAP.{iomap};" if str(p.get("I/O type", "")).upper() in {"AI", "DI"} else f"GVL_IOMAP.{iomap} := GVL_IO.{iovar};"
        (io_in if str(p.get("I/O type", "")).upper() in {"AI", "DI"} else io_out).append(stmt)
    if "GFC_IO_IN" in objs: _pou_text(objs["GFC_IO_IN"], "\n".join(io_in)+"\nGFC_IO_IN := TRUE;")
    if "GFC_IO_OUT" in objs: _pou_text(objs["GFC_IO_OUT"], "\n".join(io_out)+"\nGFC_IO_OUT := TRUE;")
    # Graph-authored exchange detail compiles into FC bodies; calls are per instance pair.
    for fn in state.get("exchange_functions", []):
        name = str(fn.get("name") or fn.get("FC") or ""); obj=objs.get(name)
        if not obj: continue
        from backend.v2_exchanges import compile_function
        _pou_text(obj, compile_function(state, fn))
    call_chunks=[]
    for c in state.get("exchange_calls",[]):
        fn=str(c.get("function_id") or c.get("FC") or ""); a=str(c.get("parameter_a_value") or ""); b=str(c.get("parameter_b_value") or "")
        if str(c.get("instance_a_id") or c.get("Instance A") or "") not in valid_instances or str(c.get("instance_b_id") or c.get("Instance B") or "") not in valid_instances: continue
        if not included("GFC_EXC",c.get("instance_a_id") or c.get("Instance A")) or not included("GFC_EXC",c.get("instance_b_id") or c.get("Instance B")): continue
        if fn and a and b:
            function=objs.get(fn,{}).get("element")
            params=function.xpath("./plc:interface/plc:inOutVars/plc:variable/@name",namespaces={"plc":PLC}) if function is not None else []
            if len(params) >= 2:
                call_chunks.append(f"{fn}(\n\t{params[0]} := {a},\n\t{params[1]} := {b});")
    if "GFC_EXC" in objs: _pou_text(objs["GFC_EXC"],"\n\n".join(call_chunks)+"\nGFC_EXC := TRUE;")
    if "PLC_PRG" in objs: _pou_text(objs["PLC_PRG"],"GFC_COM_IN();\nGFC_IO_IN();\nGFB_UPI_1();\nGFC_EXC();\nGFB_INST_1();\nGFC_COM_OUT();\nGFC_IO_OUT();")


def build_program(state) -> dict:
    """Return every free program object as {name: {name,kind,folder,element}}."""
    root = _source(); src = _source_objects(root); folders = _folder_map(root)
    model_pous={str(t.get("fb",{}).get("name")) for t in state.get("types",[]) if t.get("fb",{}).get("name")}
    model_duts={str(d.get("name")) for t in state.get("types",[]) for d in t.get("duts",[]) if d.get("name")}
    model_fcs={str(f.get("name") or f.get("FC")) for f in state.get("exchange_functions",[]) if f.get("name") or f.get("FC")}
    snapshot_objects={o.get("name"):o for o in (state.get("snapshot") or {}).get("objects",[])}
    snapshot_folders={name:o.get("folder") for name,o in snapshot_objects.items() if o.get("folder")}
    objs = {}
    for name, template in src.items():
        if name == "DUT_Union_Real_Word": continue
        if name.startswith("DUT_") and name not in model_duts: continue
        if name.startswith("FB_") and name not in model_pous: continue
        if name.startswith("FC_EXC_") and name not in model_fcs: continue
        el = copy.deepcopy(template)
        kind = "DUT" if _local(el) == "dataType" else ("GVL" if _local(el) == "globalVars" else "POU")
        folder = snapshot_folders.get(name) or folders.get(name, "")
        objs[name] = {"name": name, "kind": kind, "folder": folder, "element": el}
    # Rebuild DUT declarations from the current structured model, retaining source FB/FNC implementations.
    templates = {n: v["element"] for n, v in objs.items()}
    for typ in state.get("types", []):
        type_name = typ.get("name")
        for dut in typ.get("duts", []):
            name = dut.get("name") or f"DUT_{dut.get('role')}_{type_name}"
            el = _build_dut(name, typ, dut.get("role"), state, templates.get(name))
            folder = folders.get(name, typ.get("folder", "Function Blocs/FB"))
            objs[name] = {"name": name, "kind": "DUT", "folder": folder, "element": el}
        fbname=typ.get("fb",{}).get("name")
        if fbname and fbname not in objs:
            objs[fbname]={"name":fbname,"kind":"POU","folder":typ.get("folder","Function Blocs/FB"),"element":_new_fb(fbname,typ)}
    for fn in state.get("exchange_functions",[]):
        name=str(fn.get("name") or fn.get("FC") or "")
        if name and name not in objs:
            objs[name]={"name":name,"kind":"POU","folder":"Functions/FC_EXC","element":_new_exchange_function(fn,{str(t.get("name")):t for t in state.get("types",[])})}
    # The structured model owns authored ST; unchanged reference objects still retain their exact V7 source text.
    for typ in state.get("types", []):
        fb = typ.get("fb", {}); name = fb.get("name")
        if name in objs and fb.get("body") is not None:
            body=objs[name]["element"].find(NS+"body/"+NS+"ST/"+HNS+"xhtml")
            if body is not None: body.text=fb["body"]
        if name in objs: _append_custom(objs[name],fb.get("custom_code"),name if name and objs[name]["element"].get("pouType")=="function" else None)
    for fn in state.get("functions", []):
        name=fn.get("name"); code=fn.get("custom_code")
        if name in objs and fn.get("body") is not None:
            body=objs[name]["element"].find(NS+"body/"+NS+"ST/"+HNS+"xhtml")
            if body is not None: body.text=fn["body"]
        if name in objs: _append_custom(objs[name],code,name)
    _generated_objects(objs, state)
    # Generated sections remain owned by the model; authored additions survive regeneration.
    for name, code in (state.get("generated_custom_code") or {}).items():
        if name in {"GFC_COM_IN", "GFC_COM_OUT", "GFC_IO_IN", "GFC_IO_OUT", "GFC_EXC", "GFB_INST", "GFB_UPI", "PLC_PRG"} and name in objs:
            _append_custom(objs[name], code)

    for fn in state.get("exchange_functions",[]):
        name=str(fn.get("name") or fn.get("FC") or "")
        if name in objs: _append_custom(objs[name],fn.get("custom_code"),name)
    # Explicit PA instance values are overlays on the persistent declarations.
    pa = objs.get("GVL_PA", {}).get("element")
    if pa is not None:
        for row in state.get("pa_values", []):
            inst = row.get("instance") or row.get("instance_id")
            member = row.get("member_name") or row.get("Member")
            value = row.get("value", row.get("Instance value"))
            if value in (None, "") or not inst or not member: continue
            variable = next((v for v in pa.findall(NS + "variable") if v.get("name") == f"PA_{inst}"), None)
            if variable is None: continue
            initial = variable.find(NS + "initialValue")
            if initial is None:
                initial = etree.Element(NS + "initialValue")
                type_node = variable.find(NS + "type")
                variable.insert(variable.index(type_node) + 1, initial)
            struct = initial.find(NS + "structValue")
            if struct is None:
                old = initial.find(NS + "simpleValue")
                if old is not None: initial.remove(old)
                struct = etree.SubElement(initial, NS + "structValue")
            val = next((x for x in struct.findall(NS + "value") if x.get("member") == str(member)), None)
            if val is None: val = etree.SubElement(struct, NS + "value", member=str(member))
            for ch in list(val): val.remove(ch)
            etree.SubElement(val, NS + "simpleValue", value=str(value))
    # Keep all objects modeled in the latest full reference, including 6 GVLs and generated GFC/GFBs.
    for name, obj in objs.items(): _set_object_id(obj["element"], name, state)
    return objs


def _new_project(template_root, state):
    root = etree.Element(NS + "project", nsmap={None: PLC})
    file_header=copy.deepcopy(template_root.find(NS + "fileHeader"))
    content_header=copy.deepcopy(template_root.find(NS + "contentHeader"))
    snapshot=state.get("snapshot") or {}
    raw = snapshot.get('xml')
    if raw:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        known = etree.fromstring(raw.encode('utf-8') if isinstance(raw, str) else raw, parser)
        known_file, known_content = known.find(NS + 'fileHeader'), known.find(NS + 'contentHeader')
        if known_file is not None:
            file_header = copy.deepcopy(known_file)
        if known_content is not None and known_content.find(NS + 'coordinateInfo') is not None:
            content_header = copy.deepcopy(known_content)
    if file_header is not None: file_header.attrib.update(snapshot.get("file_header",{}))
    if content_header is not None: content_header.attrib.update(snapshot.get("content_header",{}))
    root.append(file_header); root.append(content_header)
    types = etree.SubElement(root, NS + "types")
    etree.SubElement(types, NS + "dataTypes"); etree.SubElement(types, NS + "pous")
    instances = etree.SubElement(root, NS + "instances"); etree.SubElement(instances, NS + "configurations")
    etree.SubElement(root, NS + "addData")
    return root


def serialize_program(objects: dict, state: dict, selected_names: list[str]) -> bytes:
    """Serialize selected free objects only, with a pruned ProjectStructure."""
    selected_names=list(dict.fromkeys(selected_names))
    missing = [n for n in selected_names if n not in objects]
    if missing: raise KeyError("Unknown PLC objects: " + ", ".join(missing))
    source = _source(); root = _new_project(source,state)
    types = root.find(NS + "types"); dts=types.find(NS+"dataTypes"); pous=types.find(NS+"pous")
    add=root.find(NS+"addData")
    selected=set(selected_names); paths={}
    for name in selected_names:
        obj=objects[name]; el=copy.deepcopy(obj["element"]); _set_object_id(el,name,state)
        if obj["kind"] == "DUT": dts.append(el)
        elif obj["kind"] == "POU": pous.append(el)
        elif obj["kind"] == "GVL":
            data=etree.SubElement(add,NS+"data",name=GVL_DATA,handleUnknown="implementation")
            data.append(el)
        snapshot_obj=next((o for o in (state.get("snapshot") or {}).get("objects",[]) if o.get("name")==name),None)
        paths[name]=snapshot_obj.get("folder", "") if snapshot_obj is not None else obj.get("folder","")
    psdata=etree.SubElement(add,NS+"data",name=STRUCT,handleUnknown="discard")
    ps=etree.SubElement(psdata,NS+"ProjectStructure")
    for name in selected_names:
        cur=ps
        for folder in [p for p in paths[name].split("/") if p]:
            node=next((x for x in cur if _local(x)=="Folder" and x.get("Name")==folder),None)
            if node is None: node=etree.SubElement(cur,NS+"Folder",Name=folder)
            cur=node
        etree.SubElement(cur,NS+"Object",Name=name,ObjectId=_stable_id(name,state))
    output=etree.tostring(root,xml_declaration=True,encoding="utf-8",pretty_print=True)
    if b"<html:" in output or b"<ns0:xhtml" in output:
        raise ValueError("PLCopen XHTML must use its default namespace")
    return output
