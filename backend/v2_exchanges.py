"""Reviewed editor for typed FC_EXC exchange functions and instance calls."""
from __future__ import annotations
import copy, json, math, re, secrets
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from backend.db import ImportPreview

class Preview(BaseModel):
    revision:int; action:str; function_id:str|None=None; type_a_id:str|None=None; type_b_id:str|None=None
    instance_a_id:str|None=None; instance_b_id:str|None=None; call_id:str|None=None
    mappings:list[dict]|None=None; custom_code:str|None=None; layout:dict|None=None
class Apply(BaseModel): token:str

def _type(state, ident): return next((t for t in state.get('types',[]) if str(t.get('id'))==str(ident) or str(t.get('name'))==str(ident)),None)
def _name(i): return str(i.get('name') or i.get('id') or '')
def _fname(f): return str(f.get('name') or f.get('FC') or f.get('id') or '')
def _key(t): return str(t.get('id') or t.get('name') or '')
def _members(t,state):
    if not t:return []
    from backend.v2_fb_flow import variable_options
    dut=next((d for d in t.get('duts',[]) if d.get('role')=='EXC'),None)
    if not dut: return []
    opts=variable_options({'duts':[dut]},state)
    return [{'path':o['path'],'iec_type':o['iec_type'],'member':o['path'].split('.',1)[-1]} for o in opts]
def _normal_mappings(state, fn):
    details=[r for r in state.get('exchange_functions_detail',[]) if str(r.get('sheet') or r.get('Sheet'))==str(fn.get('sheet') or fn.get('Sheet'))]
    if fn.get('mappings') is not None: return copy.deepcopy(fn['mappings'])
    return [normalize_detail(r) for r in details]
def _view(state):
    types={_key(t):t for t in state.get('types',[])}; functions=[]
    for old in state.get('exchange_functions',[]):
        f=copy.deepcopy(old); a=types.get(str(f.get('type_a_id') or f.get('Object type A') or '')); b=types.get(str(f.get('type_b_id') or f.get('Object type B') or ''))
        pa=f.get('parameter_a') or f.get('Parameter A') or ('EXC_'+a['name'] if a else 'EXC_A'); pb=f.get('parameter_b') or f.get('Parameter B') or ('EXC_'+b['name'] if b else 'EXC_B')
        f.update(id=str(f.get('id') or _fname(f)),name=_fname(f),type_a_id=_key(a) if a else f.get('type_a_id'),type_b_id=_key(b) if b else f.get('type_b_id'),parameter_a=pa,parameter_b=pb,sheet=f.get('sheet') or f.get('Sheet'),custom_code=f.get('custom_code') or '',mappings=_normal_mappings(state,f),members_a=[{**m,'path':pa+'.'+m['member']} for m in _members(a,state)],members_b=[{**m,'path':pb+'.'+m['member']} for m in _members(b,state)])
        f['generated_st']=compile_function(state,f,include_custom=True)
        functions.append(f)
    calls=[]
    for c in state.get('exchange_calls',[]):
        x=copy.deepcopy(c); x.update(id=str(c.get('id') or f"{c.get('function_id') or c.get('FC')}:{c.get('instance_a_id') or c.get('Instance A')}:{c.get('instance_b_id') or c.get('Instance B')}"),function_id=c.get('function_id') or c.get('FC'),instance_a_id=c.get('instance_a_id') or c.get('Instance A'),instance_b_id=c.get('instance_b_id') or c.get('Instance B')) ; calls.append(x)
    return {'revision':state.get('revision',0),'functions':functions,'calls':calls,'instances':copy.deepcopy(state.get('instances',[])),'types':copy.deepcopy(state.get('types',[])),'layout':copy.deepcopy(state.get('exchange_layout',{'nodes':{},'viewport':{'x':0,'y':0,'zoom':1}}))}

PATH_RE=re.compile(r'^[A-Za-z_]\w*\.[A-Za-z_]\w*(?:\[-?\d+\])?(?:\.[A-Za-z_]\w*(?:\[-?\d+\])?)*$')
def _lit(v, iec):
    if not isinstance(v,dict) or v.get('kind') not in {'variable','literal'}: raise ValueError('Expected a typed variable or literal')
    value=str(v.get('value','')).strip()
    if v['kind']=='variable':
        if not PATH_RE.fullmatch(value): raise ValueError('Unsafe condition variable')
        return value
    typ=re.sub(r'\s+','',str(iec)).upper()
    if typ=='BOOL':
        if value.upper() not in {'TRUE','FALSE'}: raise ValueError('BOOL literal must be TRUE or FALSE')
        return value.upper()
    if typ in {'STRING','WSTRING'}:
        if not re.fullmatch(r"'(?:[^'\x00-\x1f\x7f]|'')*'",value): raise ValueError('Invalid IEC string literal')
        return value
    if typ in {'REAL','LREAL'}:
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?',value): raise ValueError('Invalid numeric literal')
        number=float(value); limit=3.4028235e38 if typ=='REAL' else 1.7976931348623157e308
        if not math.isfinite(number) or abs(number)>limit: raise ValueError(f'{typ} literal is out of range')
        return value
    if typ in {'BYTE','WORD','DWORD','LWORD','USINT','UINT','UDINT','ULINT','SINT','INT','DINT','LINT'}:
        if not re.fullmatch(r'[+-]?\d+',value): raise ValueError('Invalid integer literal')
        n=int(value); bits={'BYTE':8,'USINT':8,'SINT':8,'WORD':16,'UINT':16,'INT':16,'DWORD':32,'UDINT':32,'DINT':32,'LWORD':64,'ULINT':64,'LINT':64}.get(typ,16); signed=typ in {'SINT','INT','DINT','LINT'}
        if not (-2**(bits-1) if signed else 0)<=n<=(2**(bits-(1 if signed else 0))-1): raise ValueError('Integer literal is out of range')
        return value
    raise ValueError('Literals are unsupported for this IEC type')
def validate_mappings(fn, mappings, state):
    if len(str(fn.get('custom_code') or ''))>32767: raise ValueError('Custom code exceeds the Excel cell limit')
    a=_type(state,fn.get('type_a_id')); b=_type(state,fn.get('type_b_id'))
    aa={m['path']:re.sub(r'\s+','',str(m['iec_type'])).upper() for m in _members(a,state)} if a else {}
    bb={m['path']:re.sub(r'\s+','',str(m['iec_type'])).upper() for m in _members(b,state)} if b else {}
    pa=str(fn.get('parameter_a') or fn.get('Parameter A') or 'EXC_A'); pb=str(fn.get('parameter_b') or fn.get('Parameter B') or 'EXC_B')
    if not re.fullmatch(r'[A-Za-z_]\w*',pa) or not re.fullmatch(r'[A-Za-z_]\w*',pb) or pa.casefold()==pb.casefold(): raise ValueError('Exchange parameter names must be distinct IEC identifiers')
    def resolve(path):
        if not isinstance(path,str): return None
        prefix,sep,tail=path.partition('.')
        if not sep:return None
        side=aa if prefix==pa else bb if prefix==pb else None
        return (side.get('EXC.'+tail),side) if side is not None else (None,None)
    seen=set(); ids=set()
    for m in mappings:
        if not isinstance(m,dict): raise ValueError('Invalid exchange mapping row')
        for cell in (m.get('condition'),m.get('else_value')):
            if cell is not None and len(json.dumps(cell,ensure_ascii=False,separators=(',',':')))>32767: raise ValueError('Condition or Else exceeds the Excel cell limit')
        src=m.get('source'); dst=m.get('destination')
        rs,rd=resolve(src),resolve(dst)
        if not rs or not rd or rs[0] is None or rd[0] is None: raise ValueError('Unknown exchange mapping path')
        if src.partition('.')[0]==dst.partition('.')[0]: raise ValueError('Exchange mappings must connect opposite function parameters')
        if rs[0]!=rd[0]: raise ValueError(f'Incompatible mapping types: {src} → {dst}')
        if dst in seen: raise ValueError(f'Duplicate mapping destination: {dst}')
        seen.add(dst)
        if m.get('id') in ids: raise ValueError('Duplicate mapping identifier')
        if m.get('id'): ids.add(m['id'])
        condition=m.get('condition'); otherwise=m.get('else_value')
        if condition is not None and not isinstance(condition,dict): raise ValueError('Condition must be an object')
        if condition is None and otherwise is not None: raise ValueError('Else requires a condition')
        if otherwise is not None:
            _lit(otherwise,rd[0])
            if otherwise.get('kind')=='variable':
                rr=resolve(otherwise['value'])
                if not rr or rr[0]!=rd[0]: raise ValueError('Else variable must have the destination IEC type')
        if condition is not None:
            if condition.get('mode') not in {'all','any'} or not condition.get('terms'): raise ValueError('Condition requires all/any and at least one term')
            for term in condition['terms']:
                if not isinstance(term,dict): raise ValueError('Invalid condition term')
                left=term.get('left'); op=term.get('operator')
                rr=resolve(left)
                if not rr or rr[0] is None: raise ValueError('Unknown condition path')
                typ=rr[0]
                if op not in {'is_true','is_false','=','<>','>','>=','<','<='}: raise ValueError('Unsupported condition operator')
                if op in {'is_true','is_false'}:
                    if typ!='BOOL': raise ValueError('Boolean predicates require BOOL paths')
                else:
                    if op in {'>','>=','<','<='} and typ not in {'BYTE','WORD','DWORD','LWORD','USINT','UINT','UDINT','ULINT','SINT','INT','DINT','LINT','REAL','LREAL'}: raise ValueError('Ordered comparisons require numeric paths')
                    _lit(term.get('right'),typ)
                    if term['right'].get('kind')=='variable':
                        rr=resolve(term['right']['value'])
                        if not rr or rr[0]!=typ: raise ValueError('Condition variable must have the same IEC type as its left path')
    return {**aa,**bb}
def _expr(v, typ, word_compare=False):
    val=_lit(v,typ)
    if v['kind']=='variable' and typ=='WORD' and word_compare: return f'WORD_TO_UINT({val})'
    return val
def compile_function(state, fn, include_custom=False):
    maps=fn.get('mappings') if fn.get('mappings') is not None else _normal_mappings(state,fn); validate_mappings(fn,maps,state); lines=[]
    aa={m['path']:re.sub(r'\s+','',str(m['iec_type'])).upper() for m in _members(_type(state,fn.get('type_a_id')),state)}
    bb={m['path']:re.sub(r'\s+','',str(m['iec_type'])).upper() for m in _members(_type(state,fn.get('type_b_id')),state)}
    pa=str(fn.get('parameter_a') or fn.get('Parameter A') or 'EXC_A'); pb=str(fn.get('parameter_b') or fn.get('Parameter B') or 'EXC_B')
    def path_type(path):
        prefix,_,tail=path.partition('.'); side=aa if prefix==pa else bb
        return side.get('EXC.'+tail)
    def path_st(path):
        prefix,_,tail=path.partition('.'); target=pa if prefix==pa else pb
        return target+'.'+tail
    for m in maps:
        dest=m['destination']; src=m['source']; cond=m.get('condition'); otherwise=m.get('else_value')
        if cond:
            terms=[]
            for t in cond['terms']:
                typ=path_type(t['left']); op=t['operator']
                leftpath=path_st(t['left'])
                if op in {'is_true','is_false'}: expr=leftpath if op=='is_true' else f'NOT {leftpath}'
                else:
                    left=f'WORD_TO_UINT({leftpath})' if typ=='WORD' else leftpath; right=f"WORD_TO_UINT({path_st(t['right']['value'])})" if typ=='WORD' and t['right']['kind']=='variable' else path_st(t['right']['value']) if t['right']['kind']=='variable' else _expr(t['right'],typ,word_compare=typ=='WORD')
                    expr=f'{left} {op} {right}'
                terms.append('('+expr+')')
            lines.append('IF '+(' AND ' if cond['mode']=='all' else ' OR ').join(terms)+' THEN'); lines.append(f'\t{path_st(dest)} := {path_st(src)};')
            if otherwise is not None:
                other=path_st(otherwise['value']) if otherwise.get('kind')=='variable' else _expr(otherwise,path_type(dest))
                lines += ['ELSE',f'\t{path_st(dest)} := {other};']
            lines.append('END_IF;')
        else: lines.append(f'{path_st(dest)} := {path_st(src)};')
    if include_custom and fn.get('custom_code'): lines.extend([str(fn['custom_code'])])
    lines.append(f"{_fname(fn)} := TRUE;")
    return '\n'.join(lines)

def _sync(state):
    """Mirror graph data without losing legacy rows, comments or unknown columns."""
    book=state.get('workbooks',{}).get('EXCHANGES_EXC.xlsx')
    if not book: return
    functions=state.get('exchange_functions',[])
    # Capture legacy mappings before replacing the normalized detail collection.
    mappings_by_sheet={str(f.get('sheet') or f.get('Sheet')):_normal_mappings(state,f) for f in functions}
    prior_by_id={str(r.get('id')):r for r in state.get('exchange_functions_detail',[])}
    function_sheets=set(mappings_by_sheet)
    book['sheets']=[sh for sh in book.get('sheets',[]) if sh.get('name') in {'README','LISTS','FC_EXC','CALLS'} or sh.get('name') in function_sheets]
    rebuilt=[]
    for fn in functions:
        sheet=str(fn.get('sheet') or fn.get('Sheet') or _fname(fn))
        fn['sheet']=fn['Sheet']=sheet
        sh=next((s for s in book['sheets'] if s.get('name')==sheet),None)
        if sh is None:
            sh={'name':sheet,'headers':['Direction','Source','Destination','Type','Comment'],'rows':[]}
            book['sheets'].append(sh)
        headers=sh.setdefault('headers',[])
        for column in ('Condition','Else'):
            if column not in headers: headers.append(column)
        aa={m['path']:str(m['iec_type']) for m in _members(_type(state,fn.get('type_a_id')),state)}
        bb={m['path']:str(m['iec_type']) for m in _members(_type(state,fn.get('type_b_id')),state)}
        pa=str(fn.get('parameter_a') or fn.get('Parameter A'))
        pb=str(fn.get('parameter_b') or fn.get('Parameter B'))
        rows=[]; normalized=[]
        for mapping in mappings_by_sheet.get(sheet,[]):
            src=mapping.get('source',''); dst=mapping.get('destination','')
            sp,_,sm=src.partition('.'); dp,_,dm=dst.partition('.')
            typ=(aa if sp==pa else bb).get('EXC.'+sm)
            direction='A -> B' if sp==pa and dp==pb else 'B -> A'
            row={**copy.deepcopy(prior_by_id.get(str(mapping.get('id')),{})),**copy.deepcopy(mapping),
                 'id':mapping.get('id') or secrets.token_hex(8), 'sheet':sheet,'exchange_function_id':sheet,
                 'Source':src,'Destination':dst,'Direction':direction,'Type':typ,
                 'source':src,'destination':dst,'source_member':sm,'destination_member':dm,
                 'direction':direction,'data_type':typ}
            row['Condition']=json.dumps(row['condition'],ensure_ascii=False) if row.get('condition') is not None else None
            row['Else']=json.dumps(row['else_value'],ensure_ascii=False) if row.get('else_value') is not None else None
            rows.append([row.get(h) for h in headers])
            rebuilt.append(row)
            normalized.append(normalize_detail(row))
        sh['rows']=rows
        fn['mappings']=normalized
    state['exchange_functions_detail']=rebuilt
    for sh in book['sheets']:
        if sh['name']=='FC_EXC':
            headers=sh.setdefault('headers',[])
            if 'Custom code' not in headers: headers.append('Custom code')
            sh['rows']=[[(f.get('custom_code') or '') if h=='Custom code' else f.get(h) for h in headers] for f in functions]
        elif sh['name']=='CALLS':
            headers=sh['headers']
            sh['rows']=[[(r.get('parameter_a_value') or r.get(h)) if h=='Parameter A value' else
                         (r.get('parameter_b_value') or r.get(h)) if h=='Parameter B value' else r.get(h)
                         for h in headers] for r in state.get('exchange_calls',[])]


def from_workbook(row):
    out=copy.deepcopy(row)
    for field in ('condition','else_value'):
        val=out.get(field)
        if val is None: val=out.get('Condition' if field=='condition' else 'Else')
        if val is None: val=out.get(field.replace('_',' ').title())
        if val == '': val=None
        if isinstance(val,str):
            try: val=json.loads(val)
            except ValueError as exc: raise ValueError(f"Invalid {field} JSON in exchange workbook") from exc
        if field=='else_value' and val is not None and not isinstance(val,dict): val={'kind':'literal','value':str(val)}
        out[field]=val
    return out

def normalize_detail(row):
    """Normalize a workbook mapping row, retaining typed predicates and fallback values."""
    r=from_workbook(row)
    return {**r, 'id':str(r.get('id') or r.get('ID') or secrets.token_hex(8)),
            'source':r.get('source') or r.get('Source'),'destination':r.get('destination') or r.get('Destination'),
            'condition':r.get('condition'),'else_value':r.get('else_value')}

def validate_exchanges(state):
    errors=[]; warnings=[]
    names=set(); function_names={}
    for fn in state.get('exchange_functions',[]):
        name=_fname(fn); folded=name.casefold()
        if folded in names: errors.append(f'Duplicate exchange function name: {name}')
        names.add(folded); function_names[name]=fn
        try:
            maps=fn.get('mappings')
            if maps is None:
                maps=[normalize_detail(r) for r in state.get('exchange_functions_detail',[]) if str(r.get('sheet') or r.get('Sheet'))==str(fn.get('sheet') or fn.get('Sheet'))]
            validate_mappings(fn,maps,state)
        except (ValueError,TypeError,KeyError) as exc: errors.append(f"Exchange {_fname(fn)}: {exc}")
        if not _members(_type(state,fn.get('type_a_id') or fn.get('Object type A')),state) or not _members(_type(state,fn.get('type_b_id') or fn.get('Object type B')),state): warnings.append(f"Exchange {_fname(fn)} has no EXC members to map")
    seen_calls=set(); instance_by_id={str(i.get('id') or i.get('name')):i for i in state.get('instances',[])}
    for call in state.get('exchange_calls',[]):
        fid=str(call.get('function_id') or call.get('FC') or ''); fn=function_names.get(fid)
        if not fn: errors.append(f'Exchange call references unknown function {fid}'); continue
        a=str(call.get('instance_a_id') or call.get('Instance A') or ''); b=str(call.get('instance_b_id') or call.get('Instance B') or '')
        key=(fid,a,b)
        if key in seen_calls: errors.append(f'Duplicate exchange call: {fid} {a} {b}')
        seen_calls.add(key)
        ia,ib=instance_by_id.get(a),instance_by_id.get(b)
        if not ia or not ib: errors.append(f'Exchange call references unknown instance: {a} or {b}'); continue
        ita,itb=_type(state,ia.get('type_id')),_type(state,ib.get('type_id')); fta,ftb=_type(state,fn.get('type_a_id') or fn.get('Object type A')),_type(state,fn.get('type_b_id') or fn.get('Object type B'))
        if not ita or not itb or not fta or not ftb or _key(ita)!=_key(fta) or _key(itb)!=_key(ftb): errors.append(f'Exchange call {fid} has incompatible instance types')
    return {'errors':errors,'warnings':warnings}

def register_exchange_routes(app,get_session,current_user,csrf):
    from backend import v2_store as store, v2_engine
    def project(s):
        state=store.load_project(s)
        if state is None: raise HTTPException(503,'The v2 project has not been initialized')
        return state
    @app.get('/api/v2/exchanges')
    def get_exchanges(session=Depends(get_session),user=Depends(current_user)): return _view(project(session))
    @app.post('/api/v2/exchanges/preview')
    def preview(body:Preview,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        current=project(session)
        if body.revision!=current.get('revision',0): raise HTTPException(409,'Project revision changed; reload exchanges')
        state=copy.deepcopy(current); warnings=[]; changes=[]
        try:
            if body.action=='layout':
                layout=body.layout or {}; nodes=layout.get('nodes',{}); valid={str(i.get('id') or i.get('name')) for i in state.get('instances',[])}
                for k,v in nodes.items():
                    if k not in valid or not isinstance(v,dict) or not all(math.isfinite(float(v.get(axis))) and abs(float(v[axis]))<=1e6 for axis in ('x','y')): raise ValueError('Invalid graph layout coordinates')
                vp=layout.get('viewport',{}); zoom=float(vp.get('zoom',1));
                if not all(math.isfinite(float(vp.get(k,0))) and abs(float(vp.get(k,0)))<=1e6 for k in ('x','y')) or not .1<=zoom<=4: raise ValueError('Invalid graph viewport')
                state['exchange_layout']={'nodes':copy.deepcopy(nodes),'viewport':{'x':float(vp.get('x',0)),'y':float(vp.get('y',0)),'zoom':zoom}}; changes.append('Save exchange graph layout')
            elif body.action=='save_function':
                fn=next((f for f in state.get('exchange_functions',[]) if str(f.get('id') or _fname(f))==str(body.function_id)),None)
                if not fn: raise ValueError('Exchange function not found')
                maps=body.mappings if body.mappings is not None else _normal_mappings(state,fn)
                if body.custom_code is not None: fn['custom_code']=body.custom_code
                validate_mappings(fn,maps,state); fn['mappings']=copy.deepcopy(maps); changes.append(f'Update shared function {_fname(fn)}; all its instance pairs use these mappings'); warnings.append('Function edits apply to every connected instance pair.')
            elif body.action=='connect':
                ia=next((i for i in state.get('instances',[]) if str(i.get('id') or i.get('name'))==str(body.instance_a_id)),None); ib=next((i for i in state.get('instances',[]) if str(i.get('id') or i.get('name'))==str(body.instance_b_id)),None)
                if not ia or not ib or ia is ib: raise ValueError('Choose two distinct existing instances')
                ta,tb=_type(state,ia.get('type_id')),_type(state,ib.get('type_id'))
                if not ta or not tb: raise ValueError('Instance type not found')
                fn=next((f for f in state.get('exchange_functions',[]) if str(f.get('id') or _fname(f))==str(body.function_id)),None) if body.function_id else None
                if body.function_id and fn is None: raise ValueError('Exchange function not found')
                if fn:
                    fa=str(fn.get('type_a_id') or fn.get('Object type A')); fb=str(fn.get('type_b_id') or fn.get('Object type B'))
                    if (fa,fb)==(_key(tb),_key(ta)): ia,ib=ib,ia; ta,tb=tb,ta
                    elif (fa,fb)!=(_key(ta),_key(tb)): raise ValueError('Function parameter types do not match the selected instances')
                else:
                    fn=next((f for f in state.get('exchange_functions',[]) if str(f.get('type_a_id') or f.get('Object type A'))==_key(ta) and str(f.get('type_b_id') or f.get('Object type B'))==_key(tb)),None)
                    if fn is None:
                        reverse=next((f for f in state.get('exchange_functions',[]) if str(f.get('type_a_id') or f.get('Object type A'))==_key(tb) and str(f.get('type_b_id') or f.get('Object type B'))==_key(ta)),None)
                        if reverse: fn=reverse; ia,ib=ib,ia; ta,tb=tb,ta
                if fn is None:
                    name=f"FC_EXC_{ta['name']}_{tb['name']}"; used={_fname(x) for x in state.get('exchange_functions',[])}
                    if name in used: name += '_'+secrets.token_hex(3)
                    sheet=ta['name']+'-'+tb['name']
                    sheet=sheet[:22]+'_'+__import__('hashlib').sha1(sheet.encode()).hexdigest()[:8] if len(sheet)>31 else sheet
                    fn={'id':name,'name':name,'FC':name,'type_a_id':_key(ta),'type_b_id':_key(tb),'Object type A':ta['name'],'Object type B':tb['name'],'parameter_a':'EXC_'+ta['name'],'parameter_b':'EXC_'+tb['name'],'Parameter A':'EXC_'+ta['name'],'Parameter B':'EXC_'+tb['name'],'sheet':sheet,'Sheet':sheet,'custom_code':'','mappings':[]}
                    if ta is tb: fn['parameter_b']='EXC_'+tb['name']+'_B'; fn['Parameter B']=fn['parameter_b']; fn['parameter_a']='EXC_'+ta['name']+'_A'; fn['Parameter A']=fn['parameter_a']
                    state.setdefault('exchange_functions',[]).append(fn)
                    if not any(_members(t,state) for t in (ta,tb)): warnings.append('Created an empty FC_EXC; add EXC members to the types before defining mappings.')
                if not any(str(c.get('instance_a_id') or c.get('Instance A'))==str(ia.get('id') or ia.get('name')) and str(c.get('instance_b_id') or c.get('Instance B'))==str(ib.get('id') or ib.get('name')) for c in state.get('exchange_calls',[])):
                    call={'id':f"{_fname(fn)}:{_name(ia)}:{_name(ib)}",'function_id':_fname(fn),'instance_a_id':_name(ia),'instance_b_id':_name(ib),'FC':_fname(fn),'Instance A':_name(ia),'Instance B':_name(ib),'parameter_a_value':f"GVL_EXC.EXC_{_name(ia)}",'parameter_b_value':f"GVL_EXC.EXC_{_name(ib)}"}; state.setdefault('exchange_calls',[]).append(call)
                changes.append(f'Connect {_name(ia)} to {_name(ib)} with {_fname(fn)}')
            elif body.action=='disconnect':
                before=len(state.get('exchange_calls',[])); state['exchange_calls']=[c for c in state.get('exchange_calls',[]) if str(c.get('id'))!=str(body.call_id)]
                if len(state['exchange_calls'])==before: raise ValueError('Exchange call not found')
                changes.append('Disconnect instance pair')
            elif body.action=='delete_function':
                fn=next((f for f in state.get('exchange_functions',[]) if str(f.get('id') or _fname(f))==str(body.function_id)),None)
                if not fn: raise ValueError('Exchange function not found')
                name=_fname(fn); sheet=str(fn.get('sheet') or fn.get('Sheet') or ''); impacted=[c for c in state.get('exchange_calls',[]) if str(c.get('function_id') or c.get('FC'))==name]; state['exchange_functions'].remove(fn); state['exchange_calls']=[c for c in state.get('exchange_calls',[]) if str(c.get('function_id') or c.get('FC'))!=name]; state['exchange_functions_detail']=[r for r in state.get('exchange_functions_detail',[]) if str(r.get('sheet') or r.get('Sheet'))!=sheet]; changes.append(f'Delete {name} and {len(impacted)} connected calls'); warnings.append('Connected instance pairs listed in this preview will also be deleted: '+(', '.join(str(c.get('id')) for c in impacted) if impacted else 'none'))
            else: raise ValueError('Unsupported exchange action')
        except (ValueError,TypeError,KeyError) as exc: return {'token':None,'errors':[str(exc)],'warnings':warnings,'changes':changes}
        try:
            _sync(state)
            checks=v2_engine.validate_project(state)
            baseline=set(v2_engine.validate_project(current).get('errors',[]))
            errors=[e for e in checks.get('errors',[]) if e not in baseline]
            warnings.extend(checks.get('warnings',[]))
            # Check the real final POU, including preserved custom source, just
            # as the object editor does. Existing unrelated issues stay visible.
            if body.action!='layout':
                from backend.v2_plc import build_program, serialize_program
                from backend.v2_plc_validation import validate_xml
                old_objects=build_program(current)
                old_errors=set(validate_xml(serialize_program(old_objects,current,list(old_objects)),current,old_objects)['errors'])
                objects=build_program(state)
                checked=validate_xml(serialize_program(objects,state,list(objects)),state,objects)
                errors.extend(e for e in checked.get('errors',[]) if e not in old_errors)
                warnings.extend(checked.get('warnings',[]))
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            return {'token':None,'errors':[str(exc)],'warnings':warnings,'changes':changes}
        if errors: return {'token':None,'errors':errors,'warnings':warnings,'changes':changes}
        warnings=list(dict.fromkeys(warnings))
        token=secrets.token_urlsafe(32); session.add(ImportPreview(token=token,owner_id=user.id,state_payload=json.dumps(state,ensure_ascii=False),diff_payload=json.dumps({'kind':'v2-exchanges','base_revision':current.get('revision',0)}),expires_at=datetime.now(timezone.utc)+timedelta(hours=2))); session.commit()
        proposed=_view(state)
        focus=body.function_id or (_fname(fn) if body.action=='connect' else None)
        generated=next((f['generated_st'] for f in proposed['functions'] if str(f['id'])==str(focus)), '')
        return {'token':token,'errors':[],'warnings':warnings,'changes':changes,'exchanges':proposed,'generated_st':generated}
    @app.post('/api/v2/exchanges/apply')
    def apply(body:Apply,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        p=session.get(ImportPreview,body.token); now=datetime.now(timezone.utc)
        if not p or p.owner_id!=user.id: raise HTTPException(404,'Exchange preview not found or expired')
        expiry=p.expires_at.replace(tzinfo=timezone.utc) if p.expires_at.tzinfo is None else p.expires_at
        if expiry<=now: raise HTTPException(404,'Exchange preview not found or expired')
        meta=json.loads(p.diff_payload)
        if meta.get('kind')!='v2-exchanges': raise HTTPException(404,'Exchange preview not found or expired')
        current=project(session)
        if current.get('revision',0)!=meta.get('base_revision'): raise HTTPException(409,'Project changed after preview; create a new preview')
        state=json.loads(p.state_payload); _sync(state)
        try: saved=store.save_project(session,state,username=user.username,expected_revision=meta['base_revision'])
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        session.delete(p); session.commit(); return {'revision':saved['revision']}
