"""Small, uncoloured Excel exports, preserving values and vendor workbook structure."""
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
NS = '{' + MAIN + '}'


def plain_xlsx(data):
    """Keep number formats and alignment; only row 1 receives a coloured header."""
    output = BytesIO()
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    with ZipFile(BytesIO(data)) as source, ZipFile(output, 'w', ZIP_DEFLATED) as target:
        missing_styles = 'xl/styles.xml' not in source.namelist()
        if missing_styles:
            # Some vendor exports, including PanelView, have no style part at all.
            styles = etree.Element(NS + 'styleSheet', nsmap={None: MAIN})
            for name, child in [('fonts', 'font'), ('fills', 'fill'), ('borders', 'border'), ('cellStyleXfs', 'xf'), ('cellXfs', 'xf')]:
                etree.SubElement(etree.SubElement(styles, NS + name, count='1'), NS + child)
            etree.SubElement(etree.SubElement(styles, NS + 'cellStyles', count='1'), NS + 'cellStyle', name='Normal', xfId='0', builtinId='0')
        else:
            styles = etree.fromstring(source.read('xl/styles.xml'), parser)
        font = etree.Element(NS + 'font')
        etree.SubElement(font, NS + 'sz', val='11')
        etree.SubElement(font, NS + 'color', rgb='FF000000')
        etree.SubElement(font, NS + 'name', val='Calibri')
        fonts = styles.find(NS + 'fonts')
        fonts[:] = [font, deepcopy(font)]
        etree.SubElement(fonts[1], NS + 'b')
        fonts.set('count', '2')
        fills = styles.find(NS + 'fills')
        fills[:] = []
        for pattern in ('none', 'gray125', 'solid'):
            fill = etree.SubElement(fills, NS + 'fill')
            node = etree.SubElement(fill, NS + 'patternFill', patternType=pattern)
            if pattern == 'solid':
                etree.SubElement(node, NS + 'fgColor', rgb='FFD9E1F2')
        fills.set('count', '3')
        borders = styles.find(NS + 'borders')
        borders[:] = [etree.Element(NS + 'border')]
        borders.set('count', '1')
        original = list(styles.find(NS + 'cellXfs'))
        new, lookup, remap = [], {}, {}
        for index, old in enumerate(original):
            node = deepcopy(old)
            node.attrib.update({'fontId': '0', 'fillId': '0', 'borderId': '0', 'xfId': '0'})
            for key in ('applyFont', 'applyFill', 'applyBorder'):
                node.attrib.pop(key, None)
            signature = etree.tostring(node)
            if signature not in lookup:
                lookup[signature] = len(new)
                new.append(node)
            remap[index] = lookup[signature]
        header_id = len(new)
        new.append(etree.Element(NS + 'xf', numFmtId='0', fontId='1', fillId='2', borderId='0', xfId='0'))
        xfs = styles.find(NS + 'cellXfs')
        xfs[:] = new
        xfs.set('count', str(len(new)))
        base = styles.find(NS + 'cellStyleXfs')
        if base is not None:
            base[:] = [etree.Element(NS + 'xf', numFmtId='0', fontId='0', fillId='0', borderId='0')]
            base.set('count', '1')
        named = styles.find(NS + 'cellStyles')
        if named is not None:
            named[:] = [etree.Element(NS + 'cellStyle', name='Normal', xfId='0', builtinId='0')]
            named.set('count', '1')
        table_styles = styles.find(NS + 'tableStyles')
        if table_styles is not None:
            table_styles.attrib.pop('defaultTableStyle', None)
        for dxf in styles.findall(NS + 'dxfs/' + NS + 'dxf'):
            for child in list(dxf):
                if child.tag in {NS + 'font', NS + 'fill', NS + 'border'}:
                    dxf.remove(child)
        for entry in source.infolist():
            raw = source.read(entry)
            if entry.filename == 'xl/styles.xml':
                raw = etree.tostring(styles, encoding='utf-8', xml_declaration=True)
            elif entry.filename.startswith('xl/worksheets/') and entry.filename.endswith('.xml'):
                root = etree.fromstring(raw, parser)
                for element in root.findall(NS + 'conditionalFormatting'):
                    root.remove(element)
                for color in root.findall(NS + 'sheetPr/' + NS + 'tabColor'):
                    color.getparent().remove(color)
                for col in root.findall(NS + 'cols/' + NS + 'col'):
                    if 'style' in col.attrib:
                        col.set('style', str(remap.get(int(col.get('style')), 0)))
                for index, row in enumerate(root.findall(NS + 'sheetData/' + NS + 'row'), 1):
                    is_header = row.get('r', ''.join(filter(str.isdigit, row[0].get('r', ''))) if len(row) else str(index)) == '1'
                    if 's' in row.attrib:
                        row.set('s', str(remap.get(int(row.get('s')), 0)))
                    for cell in list(row):
                        cell.set('s', str(header_id if is_header else remap.get(int(cell.get('s', '0')), 0)))
                        if not is_header and not len(cell):
                            row.remove(cell)
                    if not len(row) and not row.get('hidden'):
                        row.getparent().remove(row)
                # Rich text must not bring colours back into data cells.
                for color in root.findall('.//' + NS + 'rPr/' + NS + 'color'):
                    color.set('rgb', 'FF000000')
                    for key in list(color.attrib):
                        if key != 'rgb':
                            del color.attrib[key]
                raw = etree.tostring(root, encoding='utf-8', xml_declaration=True)
            elif missing_styles and entry.filename == '[Content_Types].xml':
                root = etree.fromstring(raw, parser)
                etree.SubElement(root, '{http://schemas.openxmlformats.org/package/2006/content-types}Override',
                                 PartName='/xl/styles.xml', ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml')
                raw = etree.tostring(root, encoding='utf-8', xml_declaration=True)
            elif missing_styles and entry.filename == 'xl/_rels/workbook.xml.rels':
                root = etree.fromstring(raw, parser)
                used = {node.get('Id') for node in root}
                relation_id = 'gaiaStyles'
                while relation_id in used:
                    relation_id += '_'
                etree.SubElement(root, '{http://schemas.openxmlformats.org/package/2006/relationships}Relationship',
                                 Id=relation_id, Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles', Target='styles.xml')
                raw = etree.tostring(root, encoding='utf-8', xml_declaration=True)
            elif entry.filename == 'xl/sharedStrings.xml':
                root = etree.fromstring(raw, parser)
                for color in root.findall('.//' + NS + 'rPr/' + NS + 'color'):
                    color.attrib.clear()
                    color.set('rgb', 'FF000000')
                raw = etree.tostring(root, encoding='utf-8', xml_declaration=True)
            elif entry.filename.startswith('xl/tables/') and entry.filename.endswith('.xml'):
                root = etree.fromstring(raw, parser)
                for info in root.findall(NS + 'tableStyleInfo'):
                    info.attrib.update({'name': '', 'showRowStripes': '0', 'showColumnStripes': '0', 'showFirstColumn': '0', 'showLastColumn': '0'})
                raw = etree.tostring(root, encoding='utf-8', xml_declaration=True)
            target.writestr(entry, raw, compress_type=ZIP_DEFLATED)
        if missing_styles:
            target.writestr('xl/styles.xml', etree.tostring(styles, encoding='utf-8', xml_declaration=True))
    return output.getvalue()
