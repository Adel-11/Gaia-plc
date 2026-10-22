"""Lossless FB section conversion and typed INPUTS/OUTPUTS copy generation."""
from __future__ import annotations
import copy
import re
import uuid

PATH = r'(?:PA|COM|EXC|IO|INTERN)\.[A-Za-z_]\w*(?:\[-?\d+\])?(?:\.[A-Za-z_]\w*(?:\[-?\d+\])?)*'
ASSIGN = re.compile(rf'^(\s*)({PATH})\s*:=\s*({PATH})\s*;\s*(?://[^\r\n]*)?(?:\r?\n)?$')
HEAD = lambda name: re.compile(r'^\s*//\s*(?:[123]\.\s*)?' + name + r'\s*\r?\n', re.M | re.I)


def _segments(code, section):
    result = []; preserved = ''; depth = 0; in_comment = False
    for line in code.splitlines(keepends=True):
        was_comment = in_comment
        if '(*' in line and '*)' not in line[line.index('(*')+2:]: in_comment = True
        if '*)' in line: in_comment = False
        clean = re.sub(r'//.*', '', line).strip()
        opening = re.match(r'^(IF|FOR|WHILE|CASE|REPEAT)\b', clean, re.I)
        closing = re.match(r'^(END_IF|END_FOR|END_WHILE|END_CASE|UNTIL)\b', clean, re.I)
        match = ASSIGN.fullmatch(line) if depth == 0 and not was_comment and not in_comment else None
        direction = bool(match and ((section == 'inputs' and match[2].startswith('INTERN.') and not match[3].startswith('INTERN.')) or (section == 'outputs' and match[3].startswith('INTERN.') and match[2].split('.')[0] in {'COM','EXC','IO'})))
        if direction:
            if preserved: result.append({'kind':'preserved', 'code':preserved}); preserved = ''
            result.append({'kind':'mapping', 'id':uuid.uuid5(uuid.NAMESPACE_URL, section + ':' + str(len(result)) + ':' + line).hex,
                           'source':match[3], 'destination':match[2], 'original':line})
        else: preserved += line
        if opening and not was_comment and not in_comment: depth += 1
        if closing and not was_comment and not in_comment: depth = max(0, depth - 1)
    if preserved: result.append({'kind':'preserved', 'code':preserved})
    return result


def convert_body(body):
    body = str(body or '')
    heads = [HEAD(name).search(body) for name in ('INPUTS','PROCESSING','OUTPUTS')]
    if all(heads) and heads[0].end() <= heads[1].start() < heads[2].start():
        a,b,c = heads
        return {'version':1,'prefix':body[:a.start()], 'input_header':a.group(),
                'input_segments':_segments(body[a.end():b.start()], 'inputs'),
                'processing_header':b.group(), 'processing_code':body[b.end():c.start()],
                'output_header':c.group(), 'output_segments':_segments(body[c.end():], 'outputs')}
    return {'version':1,'prefix':'','input_header':'','input_segments':[], 'processing_header':'',
            'processing_code':body,'output_header':'','output_segments':[]}


def render_flow(flow):
    def part(segments):
        return ''.join(s.get('code','') if s.get('kind') == 'preserved' else s.get('original', '\t' + s['destination'] + ' := ' + s['source'] + ';\n') for s in segments)
    return flow.get('prefix','') + flow.get('input_header','') + part(flow.get('input_segments',[])) + flow.get('processing_header','') + flow.get('processing_code','') + flow.get('output_header','') + part(flow.get('output_segments',[]))


def ensure_flow(fb):
    if not isinstance(fb.get('flow'), dict) or render_flow(fb['flow']) != str(fb.get('body') or ''):
        fb['flow'] = convert_body(fb.get('body',''))
    return fb['flow']


def flow_view(fb):
    flow = ensure_flow(fb)
    mappings = [{**{k:s[k] for k in ('id','source','destination')}, 'section':section}
                for section in ('inputs','outputs') for s in flow.get(section[:-1] + '_segments',[]) if s.get('kind') == 'mapping']
    preserved = {section: ''.join(s.get('code','') for s in flow.get(section[:-1]+'_segments',[]) if s.get('kind')=='preserved') for section in ('inputs','outputs')}
    return {'processing_code':flow['processing_code'], 'mappings':mappings, 'preserved_sections':preserved,
            'generated_body':render_flow(flow)}


def variable_options(typ, state):
    duts = {d['name']: d for t in state.get('types',[]) for d in t.get('duts',[])}
    options = []
    def add(path, iec, depth=0):
        text = re.sub(r'\s+','',str(iec)).upper()
        options.append({'path':path,'iec_type':iec})
        if depth > 2: return
        arr = re.fullmatch(r'ARRAY\[(-?\d+)\.\.(-?\d+)\]OF(.+)',text)
        if arr and 0 <= int(arr[2])-int(arr[1]) < 128:
            for i in range(int(arr[1]),int(arr[2])+1): add(f'{path}[{i}]',arr[3],depth+1)
        elif text == 'DUT_UNION_REAL_WORD':
            add(path+'.Real_value','REAL',depth+1); add(path+'.Word_value','ARRAY[0..1] OF WORD',depth+1)
        elif str(iec) in duts:
            for m in duts[str(iec)].get('members',[]): add(path+'.'+m['name'],m['iec_type'],depth+1)
    for dut in typ.get('duts',[]):
        role = 'INTERN' if dut.get('role')=='INT' else dut.get('role')
        for m in dut.get('members',[]): add(role+'.'+m['name'],m['iec_type'])
    return options


def update_flow(typ, state, mappings=None, processing_code=None):
    fb = typ.setdefault('fb',{}); old = ensure_flow(fb); proposed = copy.deepcopy(old)
    errors = []
    if processing_code is not None: proposed['processing_code'] = processing_code
    if mappings is not None:
        allowed = {o['path']: re.sub(r'\s+','',str(o['iec_type'])).upper() for o in variable_options(typ,state)}
        ids = set(); destinations = set()
        for mapping in mappings:
            if not isinstance(mapping,dict): errors.append('Invalid mapping row'); continue
            section, src, dst = mapping.get('section'), mapping.get('source',''), mapping.get('destination','')
            if section not in {'inputs','outputs'}: errors.append('Mapping section must be inputs or outputs'); continue
            if src not in allowed or dst not in allowed: errors.append(f'Unknown mapping variable: {src} → {dst}'); continue
            if allowed[src] != allowed[dst]: errors.append(f'Incompatible mapping types: {src} ({allowed[src]}) → {dst} ({allowed[dst]})')
            if section == 'inputs' and (src.split('.')[0] not in {'PA','COM','EXC','IO'} or not dst.startswith('INTERN.')): errors.append('INPUTS must copy PA/COM/EXC/IO to INTERN')
            if section == 'outputs' and (not src.startswith('INTERN.') or dst.split('.')[0] not in {'COM','EXC','IO'}): errors.append('OUTPUTS must copy INTERN to COM/EXC/IO')
            key = (section,dst)
            if key in destinations: errors.append(f'Duplicate mapping destination: {dst}')
            destinations.add(key)
            if mapping.get('id') in ids: errors.append('Duplicate mapping identifier')
            if mapping.get('id'): ids.add(mapping['id'])
        if errors: return errors
        for section in ('inputs','outputs'):
            key = section[:-1]+'_segments'; pending = [dict(m) for m in mappings if m.get('section')==section]; segments = []
            for segment in old.get(key,[]):
                if segment.get('kind')!='mapping': segments.append(copy.deepcopy(segment)); continue
                row = next((m for m in pending if m.get('id')==segment.get('id')),None)
                if row is None: continue
                pending.remove(row)
                if row.get('source')==segment['source'] and row.get('destination')==segment['destination']: segments.append(copy.deepcopy(segment))
                else: segments.append({'kind':'mapping','id':segment['id'],'source':row['source'],'destination':row['destination']})
            for row in pending: segments.append({'kind':'mapping','id':uuid.uuid4().hex,'source':row['source'],'destination':row['destination']})
            # Keep copied assignments on their own line after preserved source text.
            for i, segment in enumerate(segments):
                if i and segment.get('kind')=='mapping' and not segments[i-1].get('code',segments[i-1].get('original','\n')).endswith('\n'):
                    segments.insert(i,{'kind':'preserved','code':'\n'})
            proposed[key] = segments
        if mappings and not proposed.get('input_header'):
            proposed['input_header']='// INPUTS\n'; proposed['processing_header']='\n// PROCESSING\n'; proposed['output_header']='\n// OUTPUTS\n'
    fb['flow']=proposed; fb['body']=render_flow(proposed)
    return errors


def convert_project(state):
    changed=False
    for typ in state.get('types',[]):
        fb=typ.get('fb',{})
        if not isinstance(fb.get('flow'),dict) or render_flow(fb['flow']) != str(fb.get('body') or ''): ensure_flow(fb); changed=True
    return changed
