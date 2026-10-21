"""V5 -> V6 : DIP1500 configuration parameters in PA (persistent, defaults at declaration).
Output : loose PLCopen XML with only the modified objects (no Application, no GVL)."""
import copy, re
from lxml import etree

SRC = '/home/claude/v5/Gaia_V5.xml'
OUT = '/home/claude/v5/out/Gaia_V6_PA_DIP1500.xml'
NS = 'http://www.plcopen.org/xml/tc6_0200'; XH = 'http://www.w3.org/1999/xhtml'
q = lambda t: f'{{{NS}}}{t}'
r = etree.parse(SRC, etree.XMLParser(remove_blank_text=True)).getroot()
E = lambda tag, parent, **a: etree.SubElement(parent, q(tag), a)

def doc(parent, text):
    x = etree.SubElement(E('documentation', parent), f'{{{XH}}}xhtml', nsmap={None: XH}); x.text = ' ' + text

def var(parent, name, typ, init=None, cmt=None):
    v = E('variable', parent, name=name)
    t = E('type', v); E(typ, t) if typ in ('BOOL', 'WORD', 'REAL') else E('derived', t, name=typ)
    if init is not None: E('simpleValue', E('initialValue', v), value=init)
    if cmt: doc(v, cmt)
    return v

dt = {d.get('name'): d for d in r.iter(q('dataType'))}
pou = {p.get('name'): p for p in r.iter(q('pou'))}

# ------------------------------------------------------------ DUT_PA_DIP1500 : parameters with their default values
PA = [('MAX_MOTOR_SPEED',          'REAL', '350.0', 'rpm. Max motor speed (pump register 0x4003)'),
      ('CALIBRATION_COEFFICIENT',  'REAL', '100.0', 'ml/min or rpm. Flow / speed calibration coefficient (0x400B)'),
      ('FOOT_SWITCH_INPUT_MODE',   'WORD', '0',     'Foot switch input mode (0x400D)'),
      ('LIQUID_LEVEL_INPUT_MODE',  'WORD', '100',   'Liquid level input mode (0x400E)'),
      ('STATE_OUTPUT_MODE',        'WORD', '7',     'State output mode (0x400F)'),
      ('COMMUNICATION_ADDRESS',    'WORD', '11',    'Pump Modbus address, 1..31 (0x4010)'),
      ('BAUD_RATE_INDEX',          'WORD', '3',     '0=1200 .. 3=9600 .. 7=115200 (0x4011)')]
st = dt['DUT_PA_DIP1500'].find(f'{q("baseType")}/{q("struct")}')
for c in list(st): st.remove(c)
for i, (n, t, v, c) in enumerate(PA):
    var(st, n, t, v, ('[Pump configuration, default value = declaration value, kept in GVL_PA (persistent)] ' if i == 0 else '') + c)

# ------------------------------------------------------------ DUT_INT_DIP1500 : parameter source and change detection
SRC_V = [('SRC_MAX_MOTOR_SPEED', 'REAL'), ('SRC_CALIBRATION_COEFFICIENT', 'REAL'), ('SRC_FOOT_SWITCH_INPUT_MODE', 'WORD'),
         ('SRC_LIQUID_LEVEL_INPUT_MODE', 'WORD'), ('SRC_STATE_OUTPUT_MODE', 'WORD'), ('SRC_COMMUNICATION_ADDRESS', 'WORD'),
         ('SRC_BAUD_RATE_INDEX', 'WORD')]
st = dt['DUT_INT_DIP1500'].find(f'{q("baseType")}/{q("struct")}')
for i, (n, t) in enumerate(SRC_V):
    var(st, n, t, cmt=('[Parameter source] Value typed in the active local supervision' if i == 0 else None))
for i, (n, t) in enumerate(SRC_V):
    var(st, 'PREV_' + n, t, cmt=('[Parameter change detection] Previous source values' if i == 0 else None))
var(st, 'PREV_LOCAL_WEBVISU', 'BOOL', cmt='Previous control mode, to ignore the cycle where the source changes')
var(st, 'PREV_LOCAL_HMI', 'BOOL')
var(st, 'PREV_LOCAL_FUXA', 'BOOL')
var(st, 'SOURCE_CHANGED', 'BOOL', cmt='The active local source changed this cycle : no parameter is taken')

# ------------------------------------------------------------ FB_INST_DIP1500 body
p = pou['FB_INST_DIP1500']
stx = p.find(f'.//{q("ST")}')[0]
body = stx.text
CFG = {'MAX_MOTOR_SPEED': 'MAX_MOTOR_SPEED.Real_value', 'CALIBRATION_COEFFICIENT': 'FLOW_RATE_OR_SPEED_CALIBRATION_COEFFICIENT.Real_value',
       'FOOT_SWITCH_INPUT_MODE': 'FOOT_SWITCH_INPUT_MODE', 'LIQUID_LEVEL_INPUT_MODE': 'LIQUID_LEVEL_INPUT_MODE',
       'STATE_OUTPUT_MODE': 'STATE_OUTPUT_MODE', 'COMMUNICATION_ADDRESS': 'COMMUNICATION_ADDRESS', 'BAUD_RATE_INDEX': 'BAUD_RATE_INDEX'}
CMD_OF = {'MAX_MOTOR_SPEED': 'CMD_MAX_SPEED', 'CALIBRATION_COEFFICIENT': 'CMD_CALIB_COEF', 'FOOT_SWITCH_INPUT_MODE': 'CMD_FOOT_MODE',
          'LIQUID_LEVEL_INPUT_MODE': 'CMD_LEVEL_MODE', 'STATE_OUTPUT_MODE': 'CMD_STATE_OUT_MODE', 'COMMUNICATION_ADDRESS': 'CMD_ADDRESS',
          'BAUD_RATE_INDEX': 'CMD_BAUD_INDEX'}

# 1. remove the configuration lines from the command source (they now come from PA)
n0 = len(body)
body = re.sub(r'\n\t\tINTERN\.(CMD_MAX_SPEED|CMD_CALIB_COEF|CMD_FOOT_MODE|CMD_LEVEL_MODE|CMD_STATE_OUT_MODE|CMD_ADDRESS|CMD_BAUD_INDEX) +:= COM\.\w+_INPUT_[\w.]+;', '', body)
assert body.count('INTERN.CMD_MAX_SPEED      :=') == 0
body = body.replace('\t// Max speed, calibration and configuration keep their last value in auto\n', '')

def al(rows, tabs):
    w = max(len(a) for a, _ in rows)
    return [tabs + f'{a.ljust(w)} := {b};' for a, b in rows]

# 2. parameter source block, after the command source
src = ['', '\t//--- Parameter source : values typed in the active local supervision (nothing in auto)']
for i, (mode, pre) in enumerate((('LOCAL_WEBVISU', 'WEBVISU'), ('LOCAL_HMI', 'HMI'), ('LOCAL_FUXA', 'SCADA'))):
    src.append(f'\t{"IF" if i == 0 else "ELSIF"} INTERN.{mode} THEN')
    src += al([(f'INTERN.SRC_{k}', f'COM.{pre}_INPUT_{v}') for k, v in CFG.items()], '\t\t')
src.append('\tEND_IF')
anchor = '\t\tINTERN.FLASH_SAVE_REQUEST := FALSE;\n\tEND_IF\n'
assert body.count(anchor) == 1
body = body.replace(anchor, anchor + '\n'.join(src) + '\n')

# 3. PA update + parameters used by the pump, at the start of PROCESSING
proc = ['\t//--- Parameters (PA) : a value changed in the active supervision is saved in PA',
        '\t// PA is persistent : it keeps its value after a restart or a download. Default = declaration value.',
        '\t// The cycle where the source changes is ignored, so switching mode never overwrites PA.',
        '\tINTERN.SOURCE_CHANGED := (INTERN.LOCAL_WEBVISU <> INTERN.PREV_LOCAL_WEBVISU)',
        '\t                         OR (INTERN.LOCAL_HMI <> INTERN.PREV_LOCAL_HMI)',
        '\t                         OR (INTERN.LOCAL_FUXA <> INTERN.PREV_LOCAL_FUXA);',
        '\tINTERN.PREV_LOCAL_WEBVISU := INTERN.LOCAL_WEBVISU;',
        '\tINTERN.PREV_LOCAL_HMI     := INTERN.LOCAL_HMI;',
        '\tINTERN.PREV_LOCAL_FUXA    := INTERN.LOCAL_FUXA;',
        '',
        '\tIF NOT INTERN.SOURCE_CHANGED THEN']
for k in CFG:
    proc += [f'\t\tIF INTERN.SRC_{k} <> INTERN.PREV_SRC_{k} THEN',
             f'\t\t\tPA.{k} := INTERN.SRC_{k};',
             '\t\tEND_IF']
proc += ['\tEND_IF', '']
proc += al([(f'INTERN.PREV_SRC_{k}', f'INTERN.SRC_{k}') for k in CFG], '\t')
proc += ['', '\t//--- Parameters used by the pump : always from PA']
proc += al([(f'INTERN.{CMD_OF[k]}', f'PA.{k}') for k in CFG], '\t')
proc += ['', '']
anchor = '\t//--- Parameter validity\n'
assert body.count(anchor) == 1
body = body.replace(anchor, '\n'.join(proc) + anchor)
body = body.replace('// 3. OUTPUTS    : INTERN -> COM / IO / EXC', '// 3. OUTPUTS    : INTERN -> COM / IO / EXC\n// Parameters    : pump configuration in PA (persistent), changed from the active local supervision')
stx.text = body
open('/home/claude/v5/out/FB_INST_DIP1500_V6.st', 'w').write(body)

# ------------------------------------------------------------ PA becomes VAR_IN_OUT in the 3 equipment FBs (same template)
for n in ('FB_INST_ADM100W', 'FB_INST_DOSENSOR', 'FB_INST_DIP1500'):
    itf = pou[n].find(q('interface'))
    iv = itf.find(q('inputVars')); io = itf.find(q('inOutVars'))
    pa = next(v for v in iv if v.get('name') == 'PA')
    iv.remove(pa); io.insert(0, pa)
    pa.find(f'{q("documentation")}/{{{XH}}}xhtml').text = ' Parameters (persistent, GVL_PA)'
    if len(iv) == 0: itf.remove(iv)

import os; os.makedirs('/home/claude/v5/out', exist_ok=True)
open('/home/claude/v5/out/Gaia_V6_full.xml','wb').write(b'<?xml version="1.0" encoding="utf-8"?>\n' + etree.tostring(r, encoding='utf-8'))
# ------------------------------------------------------------ keep only modified objects
KEEP = {'DUT_PA_DIP1500', 'DUT_INT_DIP1500', 'FB_INST_DIP1500', 'FB_INST_ADM100W', 'FB_INST_DOSENSOR'}
for d in list(r.iter(q('dataType'))):
    if d.get('name') not in KEEP: d.getparent().remove(d)
for p in list(r.iter(q('pou'))):
    if p.get('name') not in KEEP: p.getparent().remove(p)
pad = r.find(q('addData'))
for d in list(pad):
    if d.get('name').endswith(('/globalvars', '/union')): pad.remove(d)
ps = r.find(f'.//{q("ProjectStructure")}')
for o in list(ps.iter(q('Object'))):
    if o.get('Name') not in KEEP: o.getparent().remove(o)
for _ in range(4):
    for f in list(ps.iter(q('Folder'))):
        if len(f) == 0: f.getparent().remove(f)
assert sorted(o.get('Name') for o in ps.iter(q('Object'))) == sorted(KEEP)
etree.indent(r, space='  ')
import os; os.makedirs('/home/claude/v5/out', exist_ok=True)
open(OUT, 'wb').write(b'<?xml version="1.0" encoding="utf-8"?>\n' + etree.tostring(r, encoding='utf-8'))
print('OK', OUT)
