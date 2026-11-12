"""Lossless FB section conversion and typed INPUTS/OUTPUTS copy generation."""
from __future__ import annotations
import copy
import re
import uuid

PATH = r'(?:PA|COM|EXC|IO|INTERN)\.[A-Za-z_]\w*(?:\[-?\d+\])?(?:\.[A-Za-z_]\w*(?:\[-?\d+\])?)*'
ASSIGN = re.compile(rf'^(\s*)({PATH})\s*:=\s*({PATH})\s*;\s*(?://[^\r\n]*)?(?:\r?\n)?$')
HEAD = lambda name: re.compile(r'^\s*//\s*(?:[123]\.\s*)?' + name + r'\s*\r?\n', re.M | re.I)
BOOL_TOKEN = re.compile(rf'({PATH})|\b(AND|OR)\b|([()])', re.I)


def source_paths(source):
    """Read a variable or a Boolean expression without accepting arbitrary ST."""
    if not isinstance(source, str) or len(source) > 4096:
        raise ValueError('Source du mapping invalide.')
    paths, depth, expect_operand = [], 0, True
    position = 0
    while position < len(source):
        if source[position].isspace():
            position += 1
            continue
        match = BOOL_TOKEN.match(source, position)
        if match:
            path, operator, paren = match.groups()
            if expect_operand and path:
                paths.append(path)
                expect_operand = False
            elif expect_operand and paren == '(':
                depth += 1
            elif not expect_operand and operator:
                expect_operand = True
            elif not expect_operand and paren == ')' and depth:
                depth -= 1
            else:
                break
            position = match.end()
            continue
        break
    if position != len(source) or expect_operand or depth:
        raise ValueError('Expression invalide. Utilisez des variables, AND, OR et des parenthèses équilibrées.')
    return paths


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


def _render_segments(segments):
    return ''.join(s.get('code', '') if s.get('kind') == 'preserved' else
                   s.get('original', '\t' + s['destination'] + ' := ' + s['source'] + ';\n') for s in segments)


def convert_body(body, *, legacy_order=False):
    body = str(body or '')
    heads = [HEAD(name).search(body) for name in ('INPUTS','PROCESSING','OUTPUTS')]
    if all(heads) and heads[0].end() <= heads[1].start() < heads[2].start():
        a,b,c = heads
        input_segments = _segments(body[a.end():b.start()], 'inputs')
        output_segments = _segments(body[c.end():], 'outputs')
        if legacy_order:
            # Reproduce the old output solely to identify untouched migrations.
            input_legacy = _render_segments(s for s in input_segments if s['kind'] == 'preserved')
            output_legacy = _render_segments(s for s in output_segments if s['kind'] == 'preserved')
            input_segments = [s for s in input_segments if s['kind'] == 'mapping']
            output_segments = [s for s in output_segments if s['kind'] == 'mapping']
        else:
            from backend.v2_plc_validation import _st_for_compare

            def handwritten(segments):
                return [i for i, s in enumerate(segments)
                        if s['kind'] == 'preserved' and _st_for_compare(s['code'])]

            # Only leading input copies and trailing output copies can be moved
            # to generated sections without crossing handwritten instructions.
            input_split = next(iter(handwritten(input_segments)), len(input_segments))
            output_split = max(handwritten(output_segments), default=-1) + 1
            input_legacy = _render_segments(input_segments[input_split:])
            output_legacy = _render_segments(output_segments[:output_split])
            input_segments = input_segments[:input_split]
            output_segments = output_segments[output_split:]
        processing = input_legacy + body[b.end():c.start()] + output_legacy
        return {'version':1,'prefix':body[:a.start()], 'input_header':a.group(),
                'input_segments':input_segments,
                'processing_header':b.group(), 'processing_code':processing,
                'output_header':c.group(), 'output_segments':output_segments,
                'legacy_migrated':bool(input_legacy or output_legacy)}
    return {'version':1,'prefix':'','input_header':'','input_segments':[], 'processing_header':'',
            'processing_code':body,'output_header':'','output_segments':[]}


def render_flow(flow):
    overrides = flow.get('section_overrides', {})
    def line_end(text):
        return text + '\n' if text and not text.endswith(('\n', '\r')) else text
    inputs = line_end(overrides['inputs']) if 'inputs' in overrides else _render_segments(flow.get('input_segments', []))
    outputs = overrides['outputs'] if 'outputs' in overrides else _render_segments(flow.get('output_segments', []))
    return (
        flow.get('prefix', '') + flow.get('input_header', '') + inputs
        + flow.get('processing_header', '') + flow.get('processing_code', '')
        + flow.get('output_header', '') + outputs
    )


def ensure_flow(fb):
    if not isinstance(fb.get('flow'), dict) or render_flow(fb['flow']) != str(fb.get('body') or ''):
        fb['flow'] = convert_body(fb.get('body',''))
    flow = fb['flow']
    if flow.get('legacy_migrated'):
        fb['body'] = render_flow(flow)
    return flow


def repair_migrated_flow(fb, source):
    """Repair the old migration only when the saved body still matches it exactly."""
    flow = fb.get('flow') or {}
    if not source or not flow.get('legacy_migrated') or flow.get('section_overrides'):
        return False
    original_conversion = convert_body(source, legacy_order=True)
    if not original_conversion.get('legacy_migrated') or fb.get('body') != render_flow(original_conversion):
        return False
    fb['flow'] = convert_body(source)
    fb['body'] = render_flow(fb['flow'])
    return fb['body'] != render_flow(original_conversion) or fb['flow'] != flow


def flow_view(fb):
    flow = ensure_flow(fb)
    mappings = [{**{k:s[k] for k in ('id','source','destination')}, 'section':section}
                for section in ('inputs','outputs') for s in flow.get(section[:-1] + '_segments',[]) if s.get('kind') == 'mapping']
    migration = ['Moved legacy handwritten INPUTS / OUTPUTS code into PROCESSING.'] if flow.get('legacy_migrated') else []
    return {'processing_code':flow['processing_code'], 'mappings':mappings, 'migrations':migration,
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
        saved = {(row["id"], row["source"], row["destination"]) for row in flow_view(fb)["mappings"]}
        ids = set(); destinations = set()
        for mapping in mappings:
            if not isinstance(mapping,dict): errors.append('Invalid mapping row'); continue
            section, src, dst = mapping.get('section'), mapping.get('source',''), mapping.get('destination','')
            if section not in {'inputs','outputs'}: errors.append('Mapping section must be inputs or outputs'); continue
            try:
                paths = source_paths(src)
            except ValueError as exc:
                errors.append(f'{dst or section} : {exc}')
                continue
            if not isinstance(dst, str) or dst not in allowed or any(path not in allowed for path in paths):
                errors.append(f'Unknown mapping variable: {src} → {dst}')
                continue
            aggregate = any(allowed[path].startswith(("ARRAY[", "DUT_")) for path in [dst, *paths])
            if aggregate and (mapping.get("id"), src, dst) not in saved:
                errors.append(f"{dst} : choisis une variable unitaire ou un élément du tableau.")
                continue
            expression = len(paths) > 1 or src.strip() != paths[0]
            if expression and (allowed[dst] != 'BOOL' or any(allowed[path] != 'BOOL' for path in paths)):
                errors.append(f'{dst} : les expressions AND / OR exigent des variables et une destination BOOL.')
            elif not expression and allowed[paths[0]] != allowed[dst]:
                errors.append(f'Incompatible mapping types: {src} ({allowed[paths[0]]}) → {dst} ({allowed[dst]})')
            if section == 'inputs' and (any(path.split('.')[0] not in {'PA','COM','EXC','IO'} for path in paths) or not dst.startswith('INTERN.')): errors.append('INPUTS must copy PA/COM/EXC/IO to INTERN')
            if section == 'outputs' and (any(not path.startswith('INTERN.') for path in paths) or dst.split('.')[0] not in {'COM','EXC','IO'}): errors.append('OUTPUTS must copy INTERN to COM/EXC/IO')
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
    from backend.v2_codesys import _st
    from backend.v2_plc import _source, _source_objects

    references = _source_objects(_source(state))
    changed = False
    for typ in state.get('types',[]):
        fb = typ.get('fb', {})
        old_flow = copy.deepcopy(fb.get('flow'))
        old_body = str(fb.get('body') or '')
        source = references.get(fb.get('name'))
        if source is not None:
            repair_migrated_flow(fb, _st(source))
        ensure_flow(fb)
        if old_flow != fb.get('flow') or old_body != str(fb.get('body') or ''):
            changed = True
    return changed
