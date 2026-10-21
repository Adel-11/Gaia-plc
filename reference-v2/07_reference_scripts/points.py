"""Single source of truth for the Modbus TCP server exchange (FUXA + HMI).
FUXA addresses of existing points are taken from the current IO List and never changed.
New points take the next free FUXA addresses. HMI = same point, address + 1000."""
import re, collections, openpyxl

XL_SRC = '/home/claude/v2/GAIA-ELEC-1451-V1 - IO List.xlsx'
HMI_OFFSET = 1000
TABLES = [('COIL', 'SCADA-Coils'), ('DI', 'SCADA-Discrete Inputs'), ('IR', 'SCADA-Input Registers'), ('HR', 'SCADA-Holding Registers')]
DIRECTION = {'COIL': 'INPUT', 'HR': 'INPUT', 'DI': 'OUTPUT', 'IR': 'OUTPUT'}   # seen from the PLC

class P:
    """One exchanged value (one FUXA tag / one HMI tag). 32-bit REAL = one point on 2 registers."""
    def __init__(self, obj, suffix, table, addr, typ, name, unit=None, scale=None, source='SCADA'):
        self.obj, self.suffix, self.table, self.addr, self.typ = obj, suffix, table, addr, typ
        self.name, self.unit, self.scale, self.source = name, unit, scale, source
    @property
    def words(self): return 2 if self.typ == 'REAL' else 1
    def member(self, source=None):                     # member name in DUT_COM_<obj>
        return f'{source or self.source}_{self.suffix}'

def mb_ref(table, addr, k=0):
    """GVL_MODBUS element for a Modbus address (coil 1-based, DI 10001-based, registers 1-based)."""
    if table == 'COIL':
        i = addr - 1; return f'GVL_MODBUS.Coils_TCP_HMI[{i // 8}].{i % 8}'
    if table == 'DI':
        i = addr - 10001; return f'GVL_MODBUS.Discret_input_TCP_HMI[{i // 8}].{i % 8}'
    arr = 'Input_register_TCP_HMI' if table == 'IR' else 'Holding_register_TCP_HMI'
    return f'GVL_MODBUS.{arr}[{addr - 1 + k}]'

def hmi_addr(table, addr):
    return addr + HMI_OFFSET

# ------------------------------------------------------------------ existing FUXA points (from IO List)
wb = openpyxl.load_workbook(XL_SRC)
existing = []
for tab, sh in TABLES:
    seen = set()
    for r in wb[sh].iter_rows(min_row=2, values_only=True):
        if not r[0]:
            continue
        m = re.fullmatch(r'GVL_SCADA\.SCADA_(\w+?_\d+)\.(\w+)', r[0])
        obj, suffix = m.groups()
        if (obj, suffix) in seen:
            continue                                     # 2nd register of a REAL
        seen.add((obj, suffix))
        name = r[3] or 'DO Sensor Signal'
        existing.append(P(obj, suffix, tab, r[2], r[8], name, r[10], r[9]))

# ------------------------------------------------------------------ new points : local / auto modes
EQUIP = ['DOSENSOR_1', 'DIP1500_1', 'ADM100W_1']
LABEL = {'DOSENSOR_1': 'DO Sensor', 'DIP1500_1': 'DIP1500', 'ADM100W_1': 'ADM100W'}

def next_free(table):
    used = [p.addr + w for p in existing + new for w in range(p.words) if p.table == table]
    base = {'COIL': 1, 'DI': 10001, 'IR': 1, 'HR': 1}[table]
    return max(used) + 1 if used else base

new = []
def add(obj, suffix, table, name, source='SCADA'):
    new.append(P(obj, suffix, table, next_free(table), 'BOOL', name, source=source))

for e in EQUIP:                                         # requests (FUXA coils, HMI at +1000)
    add(e, 'INPUT_LOCAL_REQUEST', 'COIL', f'{LABEL[e]} Local Request')
    add(e, 'INPUT_AUTO_REQUEST', 'COIL', f'{LABEL[e]} Auto Request')
add('UPI_LOCAL_FUXA', 'INPUT_LOCAL_REQUEST', 'COIL', 'Global Local FUXA Request')
add('UPI_LOCAL_FUXA', 'INPUT_AUTO_REQUEST', 'COIL', 'Global Auto FUXA Request')
for e in EQUIP:                                         # status (FUXA discrete inputs)
    add(e, 'OUTPUT_LOCAL_FUXA_ACTIVE', 'DI', f'{LABEL[e]} Local FUXA Active')
    add(e, 'OUTPUT_LOCAL_HMI_ACTIVE', 'DI', f'{LABEL[e]} Local HMI Active')
add('UPI_LOCAL_FUXA', 'OUTPUT_LOCAL_ACTIVE', 'DI', 'Global Local FUXA Active')
add('UPI_LOCAL_HMI', 'OUTPUT_LOCAL_ACTIVE', 'DI', 'Global Local HMI Active')

FUXA = existing + new

# ------------------------------------------------------------------ HMI points (+1000)
# Every FUXA point is duplicated for the HMI, except the FUXA-specific mode points, which get their HMI counterpart:
#   equipment  : SCADA local/auto request  -> HMI local/auto request (same +1000 address)
#                SCADA LOCAL_HMI_ACTIVE    -> HMI LOCAL_ACTIVE      (+1000 of the same bit)
#                SCADA LOCAL_FUXA_ACTIVE   -> no HMI counterpart
#   global     : UPI_LOCAL_FUXA requests   -> UPI_LOCAL_HMI requests (+1000)
#                UPI_LOCAL_HMI active      -> HMI copy (+1000), UPI_LOCAL_FUXA active -> none
HMI = []
for p in FUXA:
    a = hmi_addr(p.table, p.addr)
    if p.suffix == 'OUTPUT_LOCAL_FUXA_ACTIVE' or (p.obj == 'UPI_LOCAL_FUXA' and p.suffix == 'OUTPUT_LOCAL_ACTIVE'):
        continue
    obj, suffix, name = p.obj, p.suffix, p.name
    if p.suffix == 'OUTPUT_LOCAL_HMI_ACTIVE':
        suffix, name = 'OUTPUT_LOCAL_ACTIVE', p.name.replace('Local HMI Active', 'Local Active')
    if p.obj == 'UPI_LOCAL_FUXA':
        obj, name = 'UPI_LOCAL_HMI', p.name.replace('FUXA', 'HMI')
    HMI.append(P(obj, suffix, p.table, a, p.typ, name, p.unit, p.scale, source='HMI'))

ALL = FUXA + HMI

def check():
    occ = collections.Counter()
    for p in ALL:
        for w in range(p.words):
            occ[(p.table, p.addr + w)] += 1
    dup = [k for k, c in occ.items() if c > 1]
    assert not dup, dup
    mem = collections.Counter((p.obj, p.member()) for p in ALL)
    assert all(c == 1 for c in mem.values()), [k for k, c in mem.items() if c > 1]
    return True

if __name__ == '__main__':
    check()
    for p in new: print('FUXA', p.table, p.addr, p.obj, p.member(), p.name)
    for p in HMI:
        if 'LOCAL' in p.suffix or 'AUTO' in p.suffix: print('HMI ', p.table, p.addr, p.obj, p.member(), p.name)
    print(len(existing), 'existing,', len(new), 'new FUXA,', len(HMI), 'HMI')
    print('max addr', {t: max(p.addr + p.words - 1 for p in ALL if p.table == t) for t in ('COIL', 'DI', 'IR', 'HR')})
