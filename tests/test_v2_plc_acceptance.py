"""Deep regression checks against the supplied CODESYS PLCopen baselines."""
from __future__ import annotations

from pathlib import Path
import copy
import re
from decimal import Decimal, InvalidOperation

from lxml import etree

from backend.v2_engine import DEFAULT_PACK, _snapshot_metadata, load_pack
from backend.v2_plc import PLC, build_program, serialize_program
from backend.v2_plc_validation import validate_xml


PLC_NS = {"p": PLC, "h": "http://www.w3.org/1999/xhtml"}
PLC_DIR = Path(__file__).resolve().parents[1] / "reference-v2" / "03_plc"
V7_PATH = PLC_DIR / "Gaia_V7_reference_full.xml"
V6_PATH = PLC_DIR / "Gaia_V6_export_CODESYS.xml"
EXAMPLE_DIR = PLC_DIR / "examples"


def _baseline_state() -> dict:
    """Load the current pack data, but make partial planning start from V6."""
    state = copy.deepcopy(load_pack())
    state["snapshot"] = _snapshot_metadata(V6_PATH.read_text(encoding="utf-8", errors="replace"), V6_PATH.name)
    return state


def _object_nodes(root: etree._Element) -> dict[str, etree._Element]:
    found: dict[str, etree._Element] = {}
    for group in root.xpath("./p:types/p:dataTypes/* | ./p:types/p:pous/* | ./p:addData/p:data/p:globalVars", namespaces=PLC_NS):
        name = group.get("name")
        if name:
            found[name] = group
    return found


def _type_signature(node: etree._Element | None):
    if node is None:
        return None
    child = next(iter(node), None)
    if child is None:
        return None
    local = etree.QName(child).localname
    if local == "derived":
        return ("derived", child.get("name"))
    if local == "array":
        dims = tuple((d.get("lower"), d.get("upper")) for d in child.xpath("./p:dimension", namespaces=PLC_NS))
        base = child.find("p:baseType", PLC_NS)
        return ("array", dims, _type_signature(base))
    return (local.lower(),)


def _value_signature(variable: etree._Element):
    initial = variable.find("p:initialValue", PLC_NS)
    if initial is None:
        return None
    simple = initial.find("p:simpleValue", PLC_NS)
    if simple is not None:
        value = simple.get("value")
        try:
            value = Decimal(value).normalize().to_eng_string()
        except (InvalidOperation, TypeError):
            pass
        return ("simple", value)
    struct = initial.find("p:structValue", PLC_NS)
    if struct is not None:
        return ("struct", tuple((v.get("member"), _value_signature(v)) for v in struct.findall("p:value", PLC_NS)))
    return etree.tostring(initial, method="c14n", exclusive=True)


def _dut_signature(node: etree._Element):
    variables = node.xpath("./p:baseType/p:struct/p:variable", namespaces=PLC_NS)
    return tuple((v.get("name"), _type_signature(v.find("p:type", PLC_NS)), _value_signature(v)) for v in variables)


def _gvl_signature(node: etree._Element):
    variables = node.findall("p:variable", PLC_NS)
    rows = tuple((v.get("name"), _type_signature(v.find("p:type", PLC_NS)), _value_signature(v)) for v in variables)
    pa_attrs = tuple(
        (v.get("name"), tuple(sorted(
            (a.get("Name"), a.get("Value"))
            for a in v.xpath("./p:addData/p:data/p:Attributes/p:Attribute", namespaces=PLC_NS)
        )))
        for v in variables if node.get("name") == "GVL_PA"
    )
    return rows, node.get("retain"), node.get("persistent"), pa_attrs


def _interface_signature(node: etree._Element):
    interface = node.find("p:interface", PLC_NS)
    if interface is None:
        return ()
    groups = []
    for group in interface:
        local = etree.QName(group).localname
        variables = []
        for var in group.findall("p:variable", PLC_NS):
            variables.append((var.get("name"), _type_signature(var.find("p:type", PLC_NS)), _value_signature(var)))
        groups.append((local, tuple(variables)))
    return tuple(groups)


def _st_tokens(node: etree._Element) -> tuple[str, ...]:
    source = node.xpath("string(./p:body/p:ST/h:xhtml)", namespaces=PLC_NS)
    # Comments are skipped, strings remain single tokens, so code comparisons
    # ignore formatting and comments while preserving program meaning.
    token_re = re.compile(r"\s+|//[^\r\n]*|\(\*.*?\*\)|'(?:''|[^'])*'|[A-Za-z_][A-Za-z0-9_]*|(?:\d+(?:\.\d+)?)|:=|<=|>=|<>|\.\.|[()\[\],;:.+*/=-]", re.S)
    tokens = []
    for match in token_re.finditer(source):
        token = match.group(0)
        if token.isspace() or token.startswith("//") or token.startswith("(*"):
            continue
        tokens.append(token)
    return tuple(tokens)


def _body_text(node: etree._Element) -> str:
    return node.xpath("string(./p:body/p:ST/h:xhtml)", namespaces=PLC_NS)


def test_full_v7_export_matches_deep_reference_content_from_v6_snapshot():
    state = _baseline_state()
    objects = build_program(state)
    expected_root = etree.parse(str(V7_PATH)).getroot()
    expected = _object_nodes(expected_root)
    actual = {name: row["element"] for name, row in objects.items()}

    expected_names = set(expected) - {"DUT_Union_Real_Word"}
    assert set(actual) == expected_names, f"PLC object set differs: missing={sorted(expected_names-set(actual))}, extra={sorted(set(actual)-expected_names)}"

    differences = []
    dut_names = {name for name, node in expected.items() if etree.QName(node).localname == "dataType" and name != "DUT_Union_Real_Word"}
    for name in sorted(dut_names):
        if _dut_signature(actual[name]) != _dut_signature(expected[name]): differences.append(f"DUT members/types/defaults differ for {name}")

    expected_pous = {name for name, node in expected.items() if etree.QName(node).localname == "pou"}
    for name in sorted(expected_pous):
        if _interface_signature(actual[name]) != _interface_signature(expected[name]): differences.append(f"POU interface differs for {name}")
        if _st_tokens(actual[name]) != _st_tokens(expected[name]): differences.append(f"POU ST tokens differ for {name}")
        if name.startswith(("FB_", "FNC_")):
            if _body_text(actual[name]) != _body_text(expected[name]): differences.append(f"FB/FNC body text differs for {name}")

    expected_gvls = {name for name, node in expected.items() if etree.QName(node).localname == "globalVars"}
    for name in sorted(expected_gvls):
        if _gvl_signature(actual[name]) != _gvl_signature(expected[name]): differences.append(f"GVL variables/types/PA attributes differ for {name}")

    xml = serialize_program(objects, state, sorted(actual))
    validation = validate_xml(xml, state, objects)
    if validation["errors"]: differences.append("Generated full PLCopen XML validation: " + " | ".join(validation["errors"]))
    assert not differences, "Full V7 baseline mismatches:\n- " + "\n- ".join(differences)


def _example_object_names(path: Path) -> set[str]:
    return set(_object_nodes(etree.parse(str(path)).getroot()))


def test_selected_partial_examples_reproduce_exact_object_names():
    state = _baseline_state()
    objects = build_program(state)
    examples = [
        EXAMPLE_DIR / "Gaia_V6_partial_PA_DIP1500.xml",
        EXAMPLE_DIR / "Gaia_V7_partial_FC_EXC.xml",
    ]
    for path in examples:
        expected_names = _example_object_names(path)
        missing = expected_names - set(objects)
        assert not missing, f"{path.name}: reference objects absent from generated program: {sorted(missing)}"
        xml = serialize_program(objects, state, sorted(expected_names))
        generated_names = set(_object_nodes(etree.fromstring(xml)))
        assert generated_names == expected_names, f"{path.name}: object names differ: missing={sorted(expected_names-generated_names)}, extra={sorted(generated_names-expected_names)}"
