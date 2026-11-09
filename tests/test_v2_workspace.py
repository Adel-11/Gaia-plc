"""Multi-project isolation, shared types, accounts and semantic release history."""
import copy,io,json,zipfile
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app

@pytest.fixture
def workspace(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('ADMIN_USERNAME','workspace-admin');monkeypatch.setenv('ADMIN_PASSWORD','workspace-test-password')
    app=create_app(data_dir=tmp_path,secret_key='workspace-test-key-'*3,cookie_secure=False)
    with TestClient(app) as client:
        result=client.post('/api/login',json={'username':'workspace-admin','password':'workspace-test-password'})
        assert result.status_code==200,result.text
        yield app,client,{'X-CSRF-Token':result.json()['csrf_token']}

def test_projects_isolate_instances_share_types_and_reject_cross_project_tokens(workspace):
    app,c,h=workspace
    original=c.get('/api/v2/project').json();assert original['instances']
    catalog=c.get('/api/v2/code').json()
    token=c.post('/api/v2/code/preview',headers=h,json={'revision':original['revision'],'name':'PLC_PRG','custom_code':'// pending in first project'}).json()['token']
    created=c.post('/api/v2/workspace/projects',headers=h,json={'name':'Independent project','copy_current':False})
    assert created.status_code==200,created.text
    second=c.get('/api/v2/project').json();assert second['project_id']!=original['project_id'];assert not second['instances']
    assert second['types']==original['types']
    assert c.post('/api/v2/code/apply',headers=h,json={'token':token}).status_code==409
    from backend import v2_store as store
    with app.state.session_factory() as session:
        session.info['workspace_id']=second['project_id'];state=store.load_project(session)
        state['types'][0]['description']='Shared edited description'
        result=store.save_project(session,state,'workspace-admin',state['revision']);session.commit()
    assert c.post('/api/v2/workspace/select',headers=h,json={'id':original['project_id']}).status_code==200
    first=c.get('/api/v2/project').json();assert first['instances']==original['instances']
    assert first['types'][0]['description']=='Shared edited description'
    assert first['revision']!=original['revision']
    assert c.post('/api/v2/code/apply',headers=h,json={'token':token}).status_code==409
    assert c.post('/api/v2/workspace/select',headers=h,json={'id':second['project_id']}).status_code==200
    assert not c.get('/api/v2/project').json()['instances']

def test_isolated_catalog_type_change_does_not_touch_shared_project(workspace):
    app, client, headers = workspace
    original = client.get('/api/v2/project').json()
    original_history = client.get('/api/v2/workspace').json()['history']
    created = client.post('/api/v2/workspace/projects', headers=headers, json={
        'name': 'QA isolated', 'copy_current': True, 'isolated_catalog': True,
    })
    assert created.status_code == 200, created.text
    project_id = created.json()['id']
    isolated = client.get('/api/v2/project').json()
    assert isolated['types'] == original['types']
    assert isolated['instances'] == original['instances']
    projects = client.get('/api/v2/workspace').json()['projects']
    assert next(p for p in projects if p['id'] == project_id)['isolated_catalog']

    from backend import v2_store as store
    with app.state.session_factory() as session:
        session.info['workspace_id'] = project_id
        state = store.load_project(session)
        state['types'][0]['description'] = 'Change in QA only'
        store.save_project(session, state, 'workspace-admin', state['revision'])
        session.commit()

    isolated = client.get('/api/v2/project').json()
    assert isolated['types'][0]['description'] == 'Change in QA only'
    assert isolated['version'] == '1.0.1'
    assert client.post('/api/v2/workspace/select', headers=headers, json={'id': original['project_id']}).status_code == 200
    production = client.get('/api/v2/project').json()
    assert production['types'] == original['types']
    assert production['revision'] == original['revision']
    assert client.get('/api/v2/workspace').json()['history'] == original_history


def test_version_patch_minor_repeat_download_major_and_backup(workspace):
    app,c,h=workspace
    initial=c.get('/api/v2/project').json();v=initial['version'].split('.')
    config=c.get('/api/v2/configuration').json();pa=config['pa_values'][0]
    p=c.post('/api/v2/configuration/preview',headers=h,json={'revision':config['revision'],'changes':[{'collection':'pa_values','id':pa['id'],'op':'update','values':{'Instance value':False}}]}).json();assert not p['errors'],p
    a=c.post('/api/v2/configuration/apply',headers=h,json={'token':p['token']});assert a.status_code==200,a.text
    after=c.get('/api/v2/project').json();assert after['version']==f'{v[0]}.{v[1]}.{int(v[2])+1}'
    p=c.post('/api/v2/plc/preview',headers=h,json={'mode':'full'}).json();assert not p['errors'],p
    first=c.get('/api/v2/plc/download/'+p['token']);assert first.status_code==200,first.text
    exported=c.get('/api/v2/project').json();assert exported['version']==f'{v[0]}.{int(v[1])+1}.0'
    again=c.get('/api/v2/plc/download/'+p['token']);assert again.status_code==200,again.text
    assert c.get('/api/v2/project').json()['version']==exported['version']
    for iteration in range(2):
        incoming=c.post('/api/v2/codesys/preview',headers=h,files={'file':('validated.xml',first.content,'application/xml')}).json()
        assert not incoming['errors'],incoming
        resolutions={row['name']:'keep_generator' for row in incoming['changes'] if row.get('conflict')}
        accepted=c.post('/api/v2/codesys/apply',headers=h,json={'token':incoming['token'],'resolutions':resolutions})
        assert accepted.status_code==200,accepted.text
        imported=c.get('/api/v2/project').json()
        assert imported['version']==f'{v[0]}.{int(v[1])+2}.0'
    before=c.get('/api/v2/project').json()
    bad=c.post('/api/v2/workspace/major',headers=h,json={'revision':before['revision'],'comment':' '});assert bad.status_code==422
    major=c.post('/api/v2/workspace/major',headers=h,json={'revision':before['revision'],'comment':'Validated prototype milestone'});assert major.status_code==200,major.text
    assert major.json()['version']==f'{int(v[0])+1}.0.0'
    backup=c.post('/api/v2/workspace/backup',headers=h);assert backup.status_code==200,backup.text
    assert backup.json()['state']['version']==major.json()['version']
    assert '.json' in backup.headers['content-disposition']
    bundle=c.post('/api/v2/workspace/excel',headers=h);assert bundle.status_code==200,bundle.text
    z=zipfile.ZipFile(io.BytesIO(bundle.content));assert 'MODBUS_TCP.xlsx' in z.namelist()
    data=c.get('/api/v2/workspace').json();assert data['activity']['program_export']['name'].endswith('.xml')
    assert any(row['comment']=='Validated prototype milestone' for row in data['history'])
    restored=c.post('/api/v2/workspace/restore/preview',headers=h,files={'file':('backup.json',backup.content,'application/json')}).json()
    assert restored.get('token') and not restored['errors'],restored
    result=c.post('/api/v2/workspace/restore/apply',headers=h,json={'token':restored['token']});assert result.status_code==200,result.text

def test_restore_history_isolates_old_catalog_and_keeps_last_saved_state(workspace):
    app, client, headers = workspace
    primary = client.get('/api/v2/project').json()
    created = client.post('/api/v2/workspace/projects', headers=headers, json={
        'name': 'Historique QA', 'copy_current': True,
    }).json()
    pid = created['id']
    old_revision = created['revision']
    from backend import v2_store as store
    with app.state.session_factory() as session:
        session.info['workspace_id'] = pid
        state = store.load_project(session)
        state['types'][0]['description'] = 'Édition du catalogue partagé'
        store.save_project(session, state, 'workspace-admin', state['revision'])
        session.commit()

    backup = client.post('/api/v2/workspace/backup', headers=headers).json()['state']
    prior_revision = backup['revision']
    assert backup['version'] == '1.0.1'
    prepare = '/api/v2/workspace/history/restore/preview'
    wrong_project = client.post(prepare, headers=headers, json={
        'revision': prior_revision, 'target_revision': primary['revision'],
    })
    assert wrong_project.status_code == 404
    preview = client.post(prepare, headers=headers, json={
        'revision': prior_revision, 'target_revision': old_revision,
    }).json()
    assert preview['version'] == '1.0.0'
    assert any('catalogue de ce projet deviendra isolé' in change for change in preview['changes'])
    assert client.get('/api/v2/project').json()['revision'] == prior_revision
    restored = client.post('/api/v2/workspace/restore/apply', headers=headers, json={'token': preview['token']})
    assert restored.status_code == 200, restored.text
    assert restored.json()['version'] == '1.0.2'
    state = client.get('/api/v2/project').json()
    assert state['types'] == primary['types']
    history = client.get('/api/v2/workspace').json()['history']
    assert history[0]['kind'] == 'history_restore'
    assert history[0]['comment'] == 'Restauration de la version 1.0.0'
    assert next(entry for entry in history if entry['version'] == '1.0.1')['revision'] == prior_revision

    undo = client.post(prepare, headers=headers, json={
        'revision': state['revision'], 'target_revision': prior_revision,
    }).json()
    restored = client.post('/api/v2/workspace/restore/apply', headers=headers, json={'token': undo['token']})
    assert restored.status_code == 200, restored.text
    assert restored.json()['version'] == '1.0.3'
    assert client.get('/api/v2/project').json()['types'] == backup['types']
    assert client.post('/api/v2/workspace/select', headers=headers, json={'id': primary['project_id']}).status_code == 200
    main = client.get('/api/v2/project').json()
    assert main['types'] == backup['types']
    assert client.get('/api/v2/workspace').json()['history'][0]['kind'] != 'history_restore'


def test_delete_project_retires_its_history_and_stale_selection(workspace):
    app, client, headers = workspace
    original = client.get('/api/v2/project').json()
    delete_url = '/api/v2/workspace/delete'
    protected = client.post(delete_url, headers=headers, json={
        'project_id': original['project_id'], 'revision': original['revision'], 'name': original['project_name'],
    })
    assert protected.status_code == 409

    project = client.post('/api/v2/workspace/projects', headers=headers, json={
        'name': 'Jetable QA', 'copy_current': True, 'isolated_catalog': True,
    }).json()
    with TestClient(app) as other:
        login = other.post('/api/login', json={'username':'workspace-admin','password':'workspace-test-password'})
        other_headers = {'X-CSRF-Token': login.json()['csrf_token']}
        assert other.post('/api/v2/workspace/select', headers=other_headers, json={'id': project['id']}).status_code == 200
        wrong = client.post(delete_url, headers=headers, json={
            'project_id': project['id'], 'revision': project['revision'] - 1, 'name': 'Jetable QA',
        })
        assert wrong.status_code == 409
        deleted = client.post(delete_url, headers=headers, json={
            'project_id': project['id'], 'revision': project['revision'], 'name': 'Jetable QA',
        })
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()['active_project_id'] == original['project_id']
        assert other.post('/api/v2/workspace/backup', headers=other_headers).status_code == 409
        assert other.get('/api/v2/project').json()['project_id'] == original['project_id']

    from backend import v2_store as store, v2_workspace as ws
    from sqlalchemy import select
    with app.state.session_factory() as session:
        assert session.get(ws.SharedCatalog, -project['id']) is None
        assert session.scalar(select(store.V2ProjectRevision).where(
            store.V2ProjectRevision.project_id == project['id'],
        )) is None
        assert session.scalar(select(ws.VersionEvent).where(ws.VersionEvent.project_id == project['id'])) is None
    assert client.get('/api/v2/project').json()['revision'] == original['revision']
    created_again = client.post('/api/v2/workspace/projects', headers=headers, json={'name': 'Jetable QA'}).json()
    assert created_again['id'] > project['id']

def test_account_creation_admin_only_and_all_projects_visible(workspace):
    app,c,h=workspace
    assert c.get('/api/v2/workspace').json()['is_admin']
    assert c.post('/api/v2/workspace/users',json={'username':'editor','password':'editor-test-password'}).status_code==403
    made=c.post('/api/v2/workspace/users',headers=h,json={'username':'editor','password':'editor-test-password','is_admin':False});assert made.status_code==200,made.text
    assert c.post('/api/v2/workspace/users',headers=h,json={'username':'EDITOR','password':'editor-test-password'}).status_code==409
    login=c.post('/api/login',json={'username':'editor','password':'editor-test-password'}).json();eh={'X-CSRF-Token':login['csrf_token']}
    assert c.get('/api/v2/workspace/users').status_code==403
    assert c.post('/api/v2/workspace/users',headers=eh,json={'username':'escalation','password':'editor-test-password','is_admin':True}).status_code==403
    assert c.get('/api/v2/workspace').json()['projects']
    assert c.post('/api/v2/workspace/projects',headers=eh,json={'name':'Shared editor project'}).status_code==200


def test_shared_breaking_change_rolls_back_and_sessions_select_independently(workspace):
    app,c,h=workspace
    original=c.get('/api/v2/project').json()
    other=TestClient(app)
    login=other.post('/api/login',json={'username':'workspace-admin','password':'workspace-test-password'}).json()
    oh={'X-CSRF-Token':login['csrf_token']}
    made=c.post('/api/v2/workspace/projects',headers=h,json={'name':'Second session project'}).json()
    assert other.get('/api/v2/project').json()['project_id']==original['project_id']
    from backend import v2_store as store
    with app.state.session_factory() as session:
        session.info['workspace_id']=made['id'];state=store.load_project(session)
        used=original['instances'][0]['type_id']
        state['types']=[t for t in state['types'] if t['id']!=used]
        with pytest.raises(ValueError,match='Catalogue incompatible|Type encore utilisé'):
            store.save_project(session,state,'workspace-admin',state['revision'])
        session.rollback()
    assert other.get('/api/v2/project').json()['types']==original['types']
    assert c.get('/api/v2/project').json()['types']==original['types']
    assert other.post('/api/v2/workspace/select',headers=oh,json={'id':made['id']}).status_code==200
    other.close()


def test_old_schema_upgrade_preserves_credentials_and_creates_verified_backup(tmp_path,monkeypatch):
    import sqlite3
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('ADMIN_USERNAME','migration-admin');monkeypatch.setenv('ADMIN_PASSWORD','migration-test-password')
    app=create_app(data_dir=tmp_path,secret_key='migration-test-key-'*3,cookie_secure=False)
    with TestClient(app) as c:
        c.post('/api/login',json={'username':'migration-admin','password':'migration-test-password'})
        initial=c.get('/api/v2/project').json()
    # Reconstruct the shipped stage-8 schema, keeping real project state and password hashes.
    with sqlite3.connect(tmp_path/'app.db') as sql:
        password_hash=sql.execute('SELECT password_hash FROM users').fetchone()[0]
        sql.execute('DROP TABLE v2_shared_catalog');sql.execute('DROP TABLE v2_workspace_counter');sql.execute('DROP TABLE v2_version_event')
        sql.execute('DROP INDEX IF EXISTS ix_v2_project_revision_project_id')
        sql.execute('ALTER TABLE users DROP COLUMN is_admin')
        sql.execute('ALTER TABLE v2_project DROP COLUMN name')
        sql.execute('ALTER TABLE v2_project_revision DROP COLUMN project_id')
        sql.execute("UPDATE alembic_version SET version_num='0002_v2_model'")
    with TestClient(app) as c:
        assert c.post('/api/login',json={'username':'migration-admin','password':'migration-test-password'}).status_code==200
        assert c.get('/api/me').json()['is_admin']
        assert c.get('/api/v2/project').json()['instances']==initial['instances']
    backups=list((tmp_path/'backups').glob('before-0003_workspaces-*.sqlite3'));assert len(backups)==1
    with sqlite3.connect(backups[0]) as sql:
        assert sql.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert sql.execute('SELECT password_hash FROM users').fetchone()[0]==password_hash
        assert sql.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='0002_v2_model'
