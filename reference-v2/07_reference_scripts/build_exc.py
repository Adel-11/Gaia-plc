"""One FC per exchange between two object types (FC_EXC_<A>_<B>, VAR_IN_OUT = the two DUT_EXC),
called in GFC_EXC with the GVL_EXC variables of the instances. Replaces GFC_EXC_IN / GFC_EXC_OUT.
Output : loose PLCopen XML with only new / modified objects + ST listing."""
import re, uuid, copy, os
from lxml import etree

SRC = '/home/claude/v6/Gaia_V5b.xml'
OUT = '/home/claude/v6/out'
os.makedirs(OUT, exist_ok=True)
NS = 'http://www.plcopen.org/xml/tc6_0200'; XH = 'http://www.w3.org/1999/xhtml'
q = lambda t: f'{{{NS}}}{t}'
r = etree.parse(SRC, etree.XMLParser(remove_blank_text=True)).getroot()
E = lambda tag, parent, **a: etree.SubElement(parent, q(tag), a)
ids = {o.get('Name'): o.get('ObjectId') for o in r.iter(q('Object'))}
oid = lambda n: ids.get(n) or str(uuid.uuid5(uuid.NAMESPACE_URL, 'gaia-plc/' + n))
pou = {p.get('name'): p for p in r.iter(q('pou'))}
body = lambda n: ''.join(pou[n].find(f'.//{q("ST")}').itertext())

def doc(parent, text):
    x = etree.SubElement(E('documentation', parent), f'{{{XH}}}xhtml', nsmap={None: XH}); x.text = ' ' + text
def var(parent, name, typ, cmt=None):
    v = E('variable', parent, name=name); E('derived', E('type', v), name=typ)
    if cmt: doc(v, cmt)

BAR = '//' + '=' * 78; SUB = '//' + '-' * 78
TYPE_OF = lambda inst: re.sub(r'_\d+$', '', inst)

# ------------------------------------------------------------------ exchanges, read from the current GFC_EXC_IN / GFC_EXC_OUT
pairs = {}          # (typeA, typeB) -> list of (dst_member_of_B_or_A, src...) lines written with parameter names
calls = []          # (instA, instB)
for g in ('GFC_EXC_IN', 'GFC_EXC_OUT'):
    for left, right in re.findall(r'^\s*GVL_EXC\.(EXC_\w+?\.\w+)\s*:=\s*GVL_EXC\.(EXC_\w+?\.\w+);', body(g), re.M):
        di, dm = re.match(r'EXC_(\w+?)\.(\w+)$', left).groups()
        si, sm = re.match(r'EXC_(\w+?)\.(\w+)$', right).groups()
        a, b = (si, di)                                   # object 1 = source side (UPI), object 2 = equipment
        ta, tb = TYPE_OF(a), TYPE_OF(b)
        pairs.setdefault((ta, tb), []).append((f'EXC_{tb}.{dm}', f'EXC_{ta}.{sm}'))
        if (a, b) not in calls: calls.append((a, b))
# same lines for every instance pair of the same types : keep one copy per type pair
for k in pairs:
    seen = []; [seen.append(x) for x in pairs[k] if x not in seen]; pairs[k] = seen

# ------------------------------------------------------------------ FC_EXC_<A>_<B>
new_pous = []
listing = []
for (ta, tb), lines in pairs.items():
    name = f'FC_EXC_{ta}_{tb}'
    p = etree.Element(q('pou'), name=name, pouType='function')
    itf = E('interface', p); E('BOOL', E('returnType', itf))
    io = E('inOutVars', itf)
    var(io, f'EXC_{ta}', f'DUT_EXC_{ta}', f'Exchanges of the {ta} instance')
    var(io, f'EXC_{tb}', f'DUT_EXC_{tb}', f'Exchanges of the {tb} instance')
    w = max(len(l) for l, _ in lines)
    st = [BAR, f'// {name}', f'// Exchanges between one {ta} instance and one {tb} instance',
          '// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables',
          '// Simple copies only, no conversion', BAR, '',
          f'//--- {ta} -> {tb}'] + [f'{l.ljust(w)} := {rr};' for l, rr in lines] + ['', f'{name} := TRUE;']
    x = etree.SubElement(E('ST', E('body', p)), f'{{{XH}}}xhtml', nsmap={None: XH}); x.text = '\n' + '\n'.join(st) + '\n'
    E('ObjectId', E('data', E('addData', p), name='http://www.3s-software.com/plcopenxml/objectid', handleUnknown='discard')).text = oid(name)
    new_pous.append((p, 'FC_EXC'))
    listing += [f'(* ===== {name} ===== *)', f'FUNCTION {name} : BOOL', 'VAR_IN_OUT',
                f'\tEXC_{ta} : DUT_EXC_{ta};   // Exchanges of the {ta} instance',
                f'\tEXC_{tb} : DUT_EXC_{tb};   // Exchanges of the {tb} instance', 'END_VAR', ''] + st + ['']

# ------------------------------------------------------------------ GFC_EXC
st = [BAR, '// GFC_EXC', '// Exchanges between instances : one FC_EXC_<A>_<B> call per pair of instances',
      '// Called after GFB_UPI and before GFB_INST', BAR]
by_dst = {}
for a, b in calls: by_dst.setdefault(b, []).append(a)
for b, srcs in by_dst.items():
    st += ['', SUB, f'// {b}', SUB]
    for a in srcs:
        ta, tb = TYPE_OF(a), TYPE_OF(b)
        pa, pb = f'EXC_{ta}', f'EXC_{tb}'; w = max(len(pa), len(pb))
        st += [f'\t//--- {a} <-> {b}', f'\tFC_EXC_{ta}_{tb}(', f'\t\t{pa.ljust(w)} := GVL_EXC.EXC_{a},', f'\t\t{pb.ljust(w)} := GVL_EXC.EXC_{b});', '']
st += ['GFC_EXC := TRUE;']
p = etree.Element(q('pou'), name='GFC_EXC', pouType='function')
E('BOOL', E('returnType', E('interface', p)))
x = etree.SubElement(E('ST', E('body', p)), f'{{{XH}}}xhtml', nsmap={None: XH}); x.text = '\n' + '\n'.join(st) + '\n'
E('ObjectId', E('data', E('addData', p), name='http://www.3s-software.com/plcopenxml/objectid', handleUnknown='discard')).text = oid('GFC_EXC')
new_pous.append((p, 'GFC'))
listing += ['(* ===== GFC_EXC ===== *)', 'FUNCTION GFC_EXC : BOOL', ''] + st + ['']

# ------------------------------------------------------------------ PLC_PRG : GFC_EXC replaces GFC_EXC_IN / GFC_EXC_OUT
prg = copy.deepcopy(pou['PLC_PRG'])
x = prg.find(f'.//{q("ST")}')[0]
t = x.text
assert t.count('GFC_EXC_IN();') == 1 and t.count('GFC_EXC_OUT();') == 1
t = t.replace('GFC_EXC_IN();', 'GFC_EXC();      // exchanges between instances (FC_EXC_...)').replace('GFC_EXC_OUT();\n', '')
t = t.replace('// 2. FUNCTIONAL UNITS (UPI) and their exchanges to the equipment', '// 2. FUNCTIONAL UNITS (UPI) and exchanges between instances')
x.text = t
new_pous.append((prg, 'Program'))
listing += ['(* ===== PLC_PRG (body) ===== *)', t.strip(), '']

# ------------------------------------------------------------------ loose XML
proj = etree.Element(q('project'), nsmap={None: NS})
proj.append(copy.deepcopy(r.find(q('fileHeader')))); proj.append(copy.deepcopy(r.find(q('contentHeader'))))
types = E('types', proj); E('dataTypes', types); pous = E('pous', types)
for p, _ in new_pous: pous.append(p)
E('configurations', E('instances', proj))
st_ = E('ProjectStructure', E('data', E('addData', proj), name='http://www.3s-software.com/plcopenxml/projectstructure', handleUnknown='discard'))
fp = E('Folder', st_, Name='Program'); ff = E('Folder', st_, Name='Functions')
fg = E('Folder', ff, Name='GFC'); fx = E('Folder', ff, Name='FC_EXC')
for p, f in new_pous:
    n = p.get('name')
    E('Object', {'Program': fp, 'GFC': fg, 'FC_EXC': fx}[f], Name=n, ObjectId=oid(n))
etree.indent(proj, space='  ')
open(f'{OUT}/Gaia_V7_FC_EXC.xml', 'wb').write(b'<?xml version="1.0" encoding="utf-8"?>\n' + etree.tostring(proj, encoding='utf-8'))
open(f'{OUT}/Gaia_V7_FC_EXC.st', 'w').write('\n'.join(listing))
print('pairs', list(pairs)); print('calls', calls)
