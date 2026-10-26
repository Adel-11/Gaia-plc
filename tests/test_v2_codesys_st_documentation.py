from lxml import etree

from backend.v2_codesys import _st, PLC


def test_dut_st_declaration_includes_documentation_as_valid_line_comments():
    datatype = etree.Element(f"{{{PLC}}}dataType", name="DUT_CONTROL")
    base = etree.SubElement(datatype, f"{{{PLC}}}baseType")
    struct = etree.SubElement(base, f"{{{PLC}}}struct")
    variable = etree.SubElement(struct, f"{{{PLC}}}variable", name="INPUT_ENABLE")
    type_node = etree.SubElement(variable, f"{{{PLC}}}type")
    etree.SubElement(type_node, f"{{{PLC}}}BOOL")
    docs = etree.SubElement(variable, f"{{{PLC}}}documentation")
    xhtml = etree.SubElement(docs, "{http://www.w3.org/1999/xhtml}xhtml")
    xhtml.text = "//----------------------------INPUT (field to plc)\nEnables the equipment"

    rendered = _st(datatype)
    assert "\t//----------------------------INPUT (field to plc)\n\t// Enables the equipment\n\tINPUT_ENABLE : BOOL;" in rendered
    assert rendered.startswith("TYPE DUT_CONTROL :\nSTRUCT\n")
    assert rendered.endswith("\nEND_STRUCT\nEND_TYPE")


def test_global_variable_documentation_is_also_rendered_as_line_comments():
    global_vars = etree.Element(f"{{{PLC}}}globalVars", name="GVL_SAMPLE")
    variable = etree.SubElement(global_vars, f"{{{PLC}}}variable", name="READY")
    type_node = etree.SubElement(variable, f"{{{PLC}}}type")
    etree.SubElement(type_node, f"{{{PLC}}}BOOL")
    docs = etree.SubElement(variable, f"{{{PLC}}}documentation")
    etree.SubElement(docs, "{http://www.w3.org/1999/xhtml}xhtml").text = "Field ready flag"

    rendered = _st(global_vars)
    assert "\t// Field ready flag\n\tREADY : BOOL;" in rendered
