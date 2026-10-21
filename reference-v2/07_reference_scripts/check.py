"""Reference checker for the v4 model : every path resolves, every member used, every exchange point copied once."""
import sys, re, collections
sys.path.insert(0, '/home/claude/v4')
from model2 import *
from points import ALL, mb_ref

D = {d.name: {m.name: m.typ for m in d.members} for d in duts}
D.update({'TON': {'Q': 'BOOL', 'ET': 'TIME'}, 'TP': {'Q': 'BOOL', 'ET': 'TIME'}, 'TOF': {'Q': 'BOOL', 'ET': 'TIME'}, 'R_TRIG': {'Q': 'BOOL'},
          'DUT_Union_Real_Word': {'Real_value': 'REAL', 'Word_value': 'ARRAY[0..1] OF WORD'}})
G = {g.name: {v.name: v.typ for v in g.vars} for g in gvls}
G['GVL_IOMAP'] = {'IOMAP_AI_SIGNAL_DOSENSOR_1': 'DWORD', 'IOMAP_DI_FAULT_DOSENSOR_1': 'BOOL', 'IOMAP_DO_HIGH_DOSENSOR_1': 'BOOL', 'IOMAP_DO_LOW_DOSENSOR_1': 'BOOL'}
POUN = {p.name for p in pous}
errs = []; used = collections.defaultdict(set)

def resolve(typ, rest, where):
    for tok in re.findall(r'\.\w+|\[\d+\]', rest):
        m = re.fullmatch(r'ARRAY\[(\d+)\.\.(\d+)\] OF (\w+)', typ or '')
        if tok.startswith('['):
            if not m: errs.append(f'{where}: index on {typ}'); return None
            i = int(tok[1:-1])
            if not int(m.group(1)) <= i <= int(m.group(2)): errs.append(f'{where}: index {i} out of {typ}')
            typ = m.group(3); continue
        name = tok[1:]
        if name.isdigit():
            n = {'BYTE': 8, 'WORD': 16, 'DWORD': 32}.get(typ)
            if not n or int(name) >= n: errs.append(f'{where}: bit .{name} on {typ}')
            typ = 'BOOL'; continue
        if typ not in D: errs.append(f'{where}: .{name} on {typ}'); return None
        if name not in D[typ]: errs.append(f'{where}: {typ} has no {name}'); return None
        used[typ].add(name); typ = D[typ][name]
    return typ

for p in pous:
    code = re.sub(r'//.*', '', p.body)
    scope = {v.name: v.typ for v in p.inputs + p.outputs + p.inouts + p.locals}
    for m in re.finditer(r'\b(GVL_\w+)((?:\.\w+|\[\d+\])+)', code):
        g = m.group(1); first = re.match(r'\.(\w+)', m.group(2)).group(1)
        if g not in G or first not in G[g]: errs.append(f'{p.name}: unknown {g}.{first}'); continue
        resolve(G[g][first], m.group(2)[len(first) + 1:], p.name)
    for m in re.finditer(r'(?<![\w.])([A-Za-z_]\w*)((?:\.\w+|\[\d+\])+)', code):
        n = m.group(1)
        if n.startswith('GVL_') or n in ('T', 'TIME'): continue
        if n not in scope: errs.append(f'{p.name}: {n} not declared'); continue
        resolve(scope[n], m.group(2), p.name)
    for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', code):
        n = m.group(1)
        if n in ('IF', 'AND', 'OR', 'NOT', 'ELSIF', 'WHILE') or re.fullmatch(r'[A-Z]+_TO_[A-Z]+|SEL|SHL|SHR|MIN|MAX|LIMIT|ABS', n): continue
        if n not in POUN and n not in scope: errs.append(f'{p.name}: call to unknown {n}')
    for m in re.finditer(r'FNC_(WORDS_TO_REAL|REAL_TO_WORDS)\(([^;]*)\)', code):
        prm = set(re.findall(r'(\w+)\s*(?::=|=>)', m.group(2)))
        exp = {'WORDS_TO_REAL': {'High_word', 'Low_word'}, 'REAL_TO_WORDS': {'Real_value', 'High_word', 'Low_word'}}[m.group(1)]
        if prm != exp: errs.append(f'{p.name}: bad params {prm}')
    depth = 0
    for ln in code.split('\n'):
        s = ln.strip()
        if s and ':=' in s and not s.endswith((';', ',', 'THEN', '(')): errs.append(f'{p.name}: no ; -> {s[:90]}')
        depth += len(re.findall(r'\bIF\b', s)) - len(re.findall(r'\bEND_IF\b', s)) * 2
    if code.count('(') != code.count(')'): errs.append(f'{p.name}: parentheses')
    if len(re.findall(r'\bIF\b', code)) != len(re.findall(r'\bEND_IF\b', code)):
        errs.append(f'{p.name}: IF / END_IF mismatch')
    # every declared local is used
    for v in p.locals + p.inouts + p.inputs:
        if not re.search(rf'\b{v.name}\b', code): errs.append(f'{p.name}: {v.name} declared, not used')

for d in duts:
    if d.members is SPARE: continue
    miss = [m.name for m in d.members if m.name not in used[d.name]]
    if miss: errs.append(f'unused {d.name}: {miss}')

# every TCP point word is copied exactly once, in the right GFC, with the right direction
gin = next(p.body for p in pous if p.name == 'GFC_COM_IN'); gout = next(p.body for p in pous if p.name == 'GFC_COM_OUT')
for pt in ALL:
    to_plc = pt.table in ('COIL', 'HR')
    body = gin if to_plc else gout
    for k in range(pt.words):
        ref = re.escape(mb_ref(pt.table, pt.addr, k))
        dst = f'GVL_COM.COM_{pt.obj}.{pt.member()}' + (f'.Word_value[{k}]' if pt.words == 2 else '')
        pat = rf'{re.escape(dst)}\s*:= {ref};' if to_plc else rf'{ref}\s*:= {re.escape(dst)};'
        if len(re.findall(pat, body)) != 1: errs.append(f'copy {pt.source} {pt.table} {pt.addr}+{k} {dst}')
# no Modbus element written twice
for body in (gin, gout):
    lefts = re.findall(r'^\s*(\S+)\s*:=', body, re.M)
    dup = [x for x, c in collections.Counter(lefts).items() if c > 1]
    if dup: errs.append(f'written twice: {dup[:5]}')

print('ERRORS:', len(errs)); print('\n'.join(errs[:60]))
