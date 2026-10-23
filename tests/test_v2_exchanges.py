from __future__ import annotations
import copy
import pytest
from backend.v2_engine import load_pack
from backend.v2_exchanges import compile_function, from_workbook, normalize_detail, validate_mappings, _view

def test_legacy_exchanges_are_read_without_mutating_source_and_compile_in_both_directions():
    state=load_pack(); original=copy.deepcopy(state['exchange_functions_detail'])
    view=_view(state)
    assert len(view['functions'])==len(state['exchange_functions'])
    assert state['exchange_functions_detail']==original
    fn=state['exchange_functions'][0]
    body=compile_function(state,fn)
    assert 'FC_EXC_' in body and ':=' in body and body.rstrip().endswith('TRUE;')
    reverse=next((f for f in state['exchange_functions'] if any(r.get('direction')=='B -> A' and r.get('sheet')==f.get('sheet') for r in state['exchange_functions_detail'])),None)
    if reverse:
        assert compile_function(state,reverse)

def _fixture():
    state=copy.deepcopy(load_pack())
    typ=copy.deepcopy(state['types'][0]); typ['id']='T'; typ['name']='T'; typ['duts']=[{'name':'DUT_EXC_T','role':'EXC','members':[{'name':'Flag','iec_type':'BOOL'},{'name':'Code','iec_type':'WORD'}]}]
    typ2=copy.deepcopy(typ); typ2['id']='U'; typ2['name']='U'; typ2['duts']=[{'name':'DUT_EXC_U','role':'EXC','members':[{'name':'Flag','iec_type':'BOOL'},{'name':'Code','iec_type':'WORD'}]}]
    state['types']=[typ,typ2]
    return state,{'id':'FC_EXC_T_U','name':'FC_EXC_T_U','type_a_id':'T','type_b_id':'U','parameter_a':'EXC_T','parameter_b':'EXC_U','mappings':[]}

def test_mapping_conditions_any_all_else_and_word_comparison_compile():
    state,fn=_fixture()
    fn['mappings']=[{'id':'m','source':'EXC_T.Flag','destination':'EXC_U.Flag','condition':{'mode':'any','terms':[{'left':'EXC_T.Flag','operator':'is_true','right':{'kind':'literal','value':'TRUE'}},{'left':'EXC_T.Code','operator':'>','right':{'kind':'literal','value':'0'}}]},'else_value':{'kind':'literal','value':'FALSE'}}]
    body=compile_function(state,fn)
    assert ' OR ' in body and 'WORD_TO_UINT(EXC_T.Code) > 0' in body
    assert 'ELSE' in body and 'EXC_U.Flag := FALSE;' in body and body.endswith('FC_EXC_T_U := TRUE;')
    fn['mappings']=[{'id':'reverse','source':'EXC_U.Code','destination':'EXC_T.Code','condition':{'mode':'all','terms':[{'left':'EXC_T.Code','operator':'>=','right':{'kind':'variable','value':'EXC_U.Code'}}]},'else_value':{'kind':'variable','value':'EXC_U.Code'}}]
    body=compile_function(state,fn)
    assert 'WORD_TO_UINT(EXC_T.Code) >= WORD_TO_UINT(EXC_U.Code)' in body
    assert 'EXC_T.Code := EXC_U.Code;' in body

def test_rejects_unknown_paths_duplicate_destinations_bad_types_and_injection():
    state,fn=_fixture()
    bad=[[{'id':'x','source':'EXC_T.Flag; evil()','destination':'EXC_U.Flag'}],
         [{'id':'x','source':'EXC_T.Code','destination':'EXC_U.Flag'}],
         [{'id':'x','source':'EXC_T.Flag','destination':'EXC_U.Flag'},{'id':'y','source':'EXC_T.Flag','destination':'EXC_U.Flag'}]]
    for maps in bad:
        with pytest.raises(ValueError): validate_mappings(fn,maps,state)

def test_workbook_condition_and_else_json_is_strict():
    row=normalize_detail({'Source':'EXC_A.X','Destination':'EXC_B.Y','Condition':'{"mode":"all","terms":[]}','Else':'0'})
    assert row['condition']['mode']=='all' and row['else_value']=={'kind':'literal','value':'0'}
    with pytest.raises(ValueError,match='Invalid condition JSON'):
        from_workbook({'Condition':'{broken'})


def test_sync_retains_legacy_copies_comments_and_removes_deleted_mapping():
    from backend.v2_exchanges import _sync
    state=copy.deepcopy(load_pack())
    original={f['id']:compile_function(state,f) for f in state['exchange_functions']}
    comments=[r['Comment'] for r in state['exchange_functions_detail']]
    _sync(state)
    assert {f['id']:compile_function(state,f) for f in state['exchange_functions']}==original
    assert [r['Comment'] for r in state['exchange_functions_detail']]==comments
    removed=state['exchange_functions'][0]['mappings'][0]['id']
    state['exchange_functions'][0]['mappings']=[]
    _sync(state)
    assert not any(r['id']==removed for r in state['exchange_functions_detail'])


def test_reverse_mapping_word_operands_and_typed_else_variable():
    state,fn=_fixture()
    fn['mappings']=[{'source':'EXC_U.Code','destination':'EXC_T.Code',
                     'condition':{'mode':'all','terms':[{'left':'EXC_U.Code','operator':'>=',
                                  'right':{'kind':'variable','value':'EXC_T.Code'}}]},
                     'else_value':{'kind':'variable','value':'EXC_U.Code'}}]
    body=compile_function(state,fn)
    assert 'WORD_TO_UINT(EXC_U.Code) >= WORD_TO_UINT(EXC_T.Code)' in body
    assert '\tEXC_T.Code := EXC_U.Code;' in body
    assert 'EXC_T.Code := WORD_TO_UINT' not in body


def test_review_tokens_auth_stale_expiry_and_final_custom_references(tmp_path,monkeypatch):
    from datetime import datetime,timezone,timedelta
    from fastapi.testclient import TestClient
    from backend.main import create_app,_password_hasher
    from backend.db import User,ImportPreview
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('ADMIN_USERNAME','ex-admin'); monkeypatch.setenv('ADMIN_PASSWORD','ex-local-tests')
    app=create_app(data_dir=tmp_path,secret_key='exchange-test-'*4,cookie_secure=False)
    with TestClient(app) as client:
        assert client.get('/api/v2/exchanges').status_code==401
        def login(name='ex-admin',password='ex-local-tests'):
            result=client.post('/api/login',json={'username':name,'password':password})
            assert result.status_code==200
            return {'X-CSRF-Token':result.json()['csrf_token']}
        headers=login()
        graph=client.get('/api/v2/exchanges').json(); fn=graph['functions'][0]
        body={'revision':graph['revision'],'action':'save_function','function_id':fn['id']}
        assert client.post('/api/v2/exchanges/preview',json=body).status_code==403
        bad=client.post('/api/v2/exchanges/preview',headers=headers,json={**body,'custom_code':fn['parameter_a']+'.MISSING := TRUE;'}).json()
        assert bad['errors'] and not bad['token']
        first=client.post('/api/v2/exchanges/preview',headers=headers,json=body).json()
        second=client.post('/api/v2/exchanges/preview',headers=headers,json=body).json()
        assert not first['errors'] and not second['errors']
        with app.state.session_factory() as session:
            session.add(User(username='other-editor',password_hash=_password_hasher.hash('other-local-tests')));session.commit()
        other=login('other-editor','other-local-tests')
        assert client.post('/api/v2/exchanges/apply',headers=other,json={'token':first['token']}).status_code==404
        headers=login()
        assert client.post('/api/v2/exchanges/apply',headers=headers,json={'token':first['token']}).status_code==200
        assert client.post('/api/v2/exchanges/apply',headers=headers,json={'token':first['token']}).status_code==404
        assert client.post('/api/v2/exchanges/apply',headers=headers,json={'token':second['token']}).status_code==409
        graph=client.get('/api/v2/exchanges').json()
        expiring=client.post('/api/v2/exchanges/preview',headers=headers,json={**body,'revision':graph['revision']}).json()
        with app.state.session_factory() as session:
            session.get(ImportPreview,expiring['token']).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);session.commit()
        assert client.post('/api/v2/exchanges/apply',headers=headers,json={'token':expiring['token']}).status_code==404
        def change(action,**values):
            current=client.get('/api/v2/exchanges').json()
            result=client.post('/api/v2/exchanges/preview',headers=headers,json={'revision':current['revision'],'action':action,**values}).json()
            assert not result['errors'],result
            assert client.post('/api/v2/exchanges/apply',headers=headers,json={'token':result['token']}).status_code==200
        current=client.get('/api/v2/exchanges').json()
        call=next(c for c in current['calls'] if c['function_id']==fn['id'])
        change('disconnect',call_id=call['id'])
        assert not any(c['id']==call['id'] for c in client.get('/api/v2/exchanges').json()['calls'])
        change('connect',instance_a_id=call['instance_b_id'],instance_b_id=call['instance_a_id'])
        reconnected=client.get('/api/v2/exchanges').json()
        assert len(reconnected['functions'])==len(current['functions'])
        assert any(c['id']==call['id'] for c in reconnected['calls'])
        change('delete_function',function_id=fn['id'])
        deleted=client.get('/api/v2/exchanges').json()
        assert not any(f['id']==fn['id'] for f in deleted['functions'])
        assert not any(c['function_id']==fn['id'] for c in deleted['calls'])
