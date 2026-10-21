"""Current full program = Adel's V6 CODESYS export + V7 changes (FC_EXC_*, GFC_EXC, PLC_PRG, without GFC_EXC_IN/OUT)."""
import copy
from lxml import etree
NS = 'http://www.plcopen.org/xml/tc6_0200'; q = lambda t: f'{{{NS}}}{t}'
P = etree.XMLParser(remove_blank_text=True)
base = etree.parse('/home/claude/v6/Gaia_V5b.xml', P).getroot()
v7 = etree.parse('/home/claude/v6/out/Gaia_V7_FC_EXC.xml', P).getroot()
pous = base.find(f'{q("types")}/{q("pous")}')
new = {p.get('name'): p for p in v7.iter(q('pou'))}
for p in list(pous):
    if p.get('name') in ('GFC_EXC_IN', 'GFC_EXC_OUT') or p.get('name') in new:
        pous.remove(p)
for p in new.values():
    pous.append(copy.deepcopy(p))
ps = base.find(f'.//{q("ProjectStructure")}')
for o in list(ps.iter(q('Object'))):
    if o.get('Name') in ('GFC_EXC_IN', 'GFC_EXC_OUT'): o.getparent().remove(o)
funcs = next(f for f in ps.iter(q('Folder')) if f.get('Name') == 'Functions')
gfc = next(f for f in funcs if f.get('Name') == 'GFC')
fx = etree.SubElement(funcs, q('Folder'), Name='FC_EXC')
v7obj = {o.get('Name'): o.get('ObjectId') for o in v7.iter(q('Object'))}
for n, i in v7obj.items():
    if n == 'GFC_EXC': etree.SubElement(gfc, q('Object'), Name=n, ObjectId=i)
    elif n.startswith('FC_EXC_'): etree.SubElement(fx, q('Object'), Name=n, ObjectId=i)
names = [o.get('Name') for o in ps.iter(q('Object'))]
assert len(names) == len(set(names))
declared = {p.get('name') for p in base.iter(q('pou'))} | {d.get('name') for d in base.iter(q('dataType'))} | {g.get('name') for g in base.iter(q('globalVars'))}
assert declared - set(names) == set(), declared - set(names)
etree.indent(base, space='  ')
open('/home/claude/v7/Gaia_V7_reference_full.xml', 'wb').write(b'<?xml version="1.0" encoding="utf-8"?>\n' + etree.tostring(base, encoding='utf-8'))
xsd = etree.XMLSchema(etree.parse('/tmp/claude-0/scratch/pk/plcopen-1.0.1/src/plcopen/tc6_xml_v201.xsd'))
print('XSD', xsd.validate(etree.fromstring(open('/home/claude/v7/Gaia_V7_reference_full.xml', 'rb').read().replace(b'tc6_0200', b'tc6_0201'))))
print(len(names), 'objects ; not in structure :', set(names) - declared)
