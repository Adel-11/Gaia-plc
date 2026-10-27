"""Named project workspaces, a shared type library, and semantic version history."""
from __future__ import annotations
import copy, hashlib, json
from datetime import datetime, timezone
from sqlalchemy import Integer,String,Text,DateTime,select,func,update
from sqlalchemy.orm import Mapped,mapped_column
from backend.db import Base
from backend import v2_store as store


def now(): return datetime.now(timezone.utc)
def dump(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value): return hashlib.sha256(dump(value).encode()).hexdigest()

class SharedCatalog(Base):
    __tablename__='v2_shared_catalog'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,default=1)
    revision:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    payload:Mapped[str]=mapped_column(Text,nullable=False)

class WorkspaceCounter(Base):
    __tablename__='v2_workspace_counter'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,default=1)
    value:Mapped[int]=mapped_column(Integer,nullable=False,default=0)

class VersionEvent(Base):
    __tablename__='v2_version_event'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    project_id:Mapped[int]=mapped_column(Integer,nullable=False,index=True)
    revision:Mapped[int]=mapped_column(Integer,nullable=False)
    version:Mapped[str]=mapped_column(String(64),nullable=False)
    kind:Mapped[str]=mapped_column(String(64),nullable=False)
    comment:Mapped[str]=mapped_column(Text,nullable=False)
    username:Mapped[str]=mapped_column(String(128),nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)

SHARED=('types','catalog_options')
COLLECTIONS={'types':'Types','instances':'Instances','functions':'Fonctions','generated_custom_code':'Code personnalisé','tcp_clients':'Clients TCP','tcp_points':'Adresses TCP','rtu_lines':'Lignes RTU','rtu_slaves':'Esclaves RTU','rtu_channels':'Registres RTU','io_modules':'Modules E/S','io_channels':'Affectations E/S','virtual_io':'Signaux E/S','pa_values':'Paramètres PA','exchange_functions':'Fonctions d’échange','exchange_functions_detail':'Liaisons d’échange','exchange_calls':'Appels d’échange','catalog_options':'Options du catalogue','exchange_layout':'Disposition des échanges','program_membership':'Instances ajoutées aux blocs du programme'}

def _catalog(state):
    result={k:copy.deepcopy(state.get(k,[] if k=='types' else {})) for k in SHARED}
    result['type_workbooks']={k:copy.deepcopy(v) for k,v in state.get('workbooks',{}).items() if k.startswith('TYPE_')}
    return result

def overlay(state, catalog):
    result=copy.deepcopy(state)
    for key in SHARED: result[key]=copy.deepcopy(catalog.get(key,[] if key=='types' else {}))
    result['workbooks']={k:v for k,v in result.get('workbooks',{}).items() if not k.startswith('TYPE_')}
    result['workbooks'].update(copy.deepcopy(catalog.get('type_workbooks',{})))
    return result

def _raw(session,pid):
    project=session.get(store.V2Project,pid)
    revision=session.get(store.V2ProjectRevision,project.current_revision_id) if project and project.current_revision_id else None
    return project,json.loads(revision.state_payload) if revision else None

def ensure_workspace(session):
    counter=session.get(WorkspaceCounter,1)
    if counter is None:
        session.add(WorkspaceCounter(id=1,value=session.scalar(select(func.max(store.V2ProjectRevision.revision))) or 0))
    catalog=session.get(SharedCatalog,1)
    if catalog is None:
        _,state=_raw(session,1)
        if state is not None: session.add(SharedCatalog(id=1,revision=1,payload=dump(_catalog(state))))
    for project in session.scalars(select(store.V2Project)).all():
        if not session.scalar(select(VersionEvent.id).where(VersionEvent.project_id==project.id).limit(1)):
            _,state=_raw(session,project.id)
            if state:session.add(VersionEvent(project_id=project.id,revision=state.get('revision',0),version=state.get('version','1.0.0'),kind='migration',comment='Initialisation du suivi du projet existant',username='system'))
    session.flush()

def load_workspace_project(session):
    pid=int(session.info.get('workspace_id',1));project,state=_raw(session,pid)
    if state is None:return None
    catalog=session.get(SharedCatalog,1)
    if catalog:state=overlay(state,json.loads(catalog.payload))
    state['project_id']=pid;state['project_name']=project.name
    state['_catalog_revision']=catalog.revision if catalog else 0
    state.setdefault('version','1.0.0')
    state.setdefault('activity',{})
    if 'program_import' not in state['activity'] and (state.get('snapshot') or {}).get('imported_at'):
        state['activity']['program_import']={'at':state['snapshot']['imported_at'],'name':state['snapshot'].get('filename','programme.xml')}
    return state

def _lock(session):
    ensure_workspace(session)
    return session.execute(update(WorkspaceCounter).where(WorkspaceCounter.id==1).values(value=WorkspaceCounter.value+1).returning(WorkspaceCounter.value)).scalar_one()

def _changes(previous,state):
    previous=previous or {};changes=[];activities={}
    for key,label in COLLECTIONS.items():
        if digest(previous.get(key))==digest(state.get(key)):continue
        old=previous.get(key,[]);new=state.get(key,[])
        names=[]
        if isinstance(old,list) and isinstance(new,list):
            def ident(r):return str(r.get('id') or r.get('name') or r.get('Member') or r.get('Client') or r.get('Instance') or r.get('Line') or '')
            before={ident(r):r for r in old if isinstance(r,dict)}
            for row in new:
                if isinstance(row,dict) and digest(row)!=digest(before.get(ident(row))): names.append(str(row.get('name') or row.get('id') or row.get('Member') or ident(row)))
            names.extend(f"{name} (supprimé)" for name in before if name not in {ident(r) for r in new if isinstance(r,dict)})
        text=label+(' : '+', '.join(names[:6]) if names else ' : modification enregistrée')
        changes.append(text);activities[key]={'at':now().isoformat(),'name':', '.join(names[:6]) or label}
    if not changes and digest(previous.get('workbooks'))!=digest(state.get('workbooks')):
        changes.append('Classeurs : données mises à jour');activities['workbooks']={'at':now().isoformat(),'name':'Classeurs du projet'}
    if previous and previous.get('project_name') and state.get('project_name')!=previous.get('project_name'):
        changes.append('Nom du projet : '+str(state.get('project_name')))
    return changes,activities

def _persist(session,pid,state,previous,username,revision,event=None):
    project=session.get(store.V2Project,pid)
    state=copy.deepcopy(state); changes,activities=_changes(previous,state)
    version=str((previous or {}).get('version','1.0.0')); parts=[int(x) for x in version.split('.')]
    history_kind=None;comment=''
    if previous is None:
        version='1.0.0';history_kind='created';comment='Création du projet'
    elif event and event['kind']=='major':
        version=f'{parts[0]+1}.0.0';history_kind='major';comment=event['comment']
    elif event and event['kind'] in ('program_export','program_import'):
        fingerprint=event['digest']
        if fingerprint!=(previous or {}).get('_program_digests',{}).get(event['kind']):
            version=f'{parts[0]}.{parts[1]+1}.0';history_kind=event['kind'];comment=('Export' if history_kind=='program_export' else 'Import')+' du programme après modification : '+event.get('name','programme')
        state['_program_digests']=copy.deepcopy((previous or {}).get('_program_digests',{}))
        state['_program_digests'][event['kind']]=fingerprint
    elif changes:
        version=f'{parts[0]}.{parts[1]}.{parts[2]+1}';history_kind='change';comment=' ; '.join(changes)
    state['version']=version
    activity=copy.deepcopy((previous or {}).get('activity',{}));activity.update(activities)
    if event:
        activity[event['kind']]={'at':now().isoformat(),'name':event.get('name',event.get('comment','Projet'))}
    if state.get('project_name') and project is not None and state['project_name']!=project.name:project.name=state['project_name']
    state['activity']=activity;state['revision']=revision;state['project_id']=pid
    state['project_name']=project.name if project else state.get('project_name','Gaia')
    state.pop('_catalog_revision',None)
    # Legacy relational tables remain a queryable projection of the original project.
    if pid==1:store._replace_relational(session,state)
    row=store.V2ProjectRevision(project_id=pid,revision=revision,username=username or 'admin',state_payload=dump(state))
    session.add(row);session.flush()
    if project is None:
        project=store.V2Project(id=pid,name=state['project_name'],current_revision_id=row.id,created_at=now(),updated_at=now());session.add(project)
    else:project.current_revision_id=row.id;project.updated_at=now()
    if history_kind:session.add(VersionEvent(project_id=pid,revision=revision,version=version,kind=history_kind,comment=comment,username=username or 'admin'))
    session.flush()
    return state

def _compatibility_errors(state):
    from backend.v2_engine import validate_project
    from backend import v2_plc, v2_plc_validation
    errors = list(validate_project(state)['errors'])
    try:
        objects = v2_plc.build_program(state)
        xml = v2_plc.serialize_program(objects, state, list(objects))
        errors.extend(v2_plc_validation.validate_xml(xml, state, objects)['errors'])
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(str(exc))
    return errors


def save_workspace_project(session,state,username='admin',expected_revision=None):
    if not isinstance(state,dict):raise TypeError('Project state must be an object')
    pid=int(session.info.get('workspace_id',1))
    if state.get('project_id') not in (None,pid):raise ValueError('Le projet a changé depuis la préparation de cette action')
    blocks_only=session.info.pop('type_blocks_only',False)
    revision=_lock(session)
    project=session.get(store.V2Project,pid)
    if project:session.refresh(project)
    _,raw=_raw(session,pid)
    if expected_revision is not None and int(expected_revision)!=(raw or {}).get('revision',0):raise ValueError(f"Project revision conflict: expected {expected_revision}, current is {(raw or {}).get('revision',0)}")
    catalog=session.get(SharedCatalog,1)
    if catalog:session.refresh(catalog)
    if catalog and state.get('_catalog_revision',catalog.revision)!=catalog.revision:raise ValueError('Le catalogue partagé a changé ; actualisez avant de réessayer')
    current=overlay(raw,json.loads(catalog.payload)) if raw and catalog else raw
    shared=_catalog(state);changed=catalog is not None and digest(shared)!=digest(json.loads(catalog.payload))
    affected=[]
    if changed:
        # Recheck deletion under the workspace write lock, including projects
        # edited after the review was prepared.
        from backend.v2_type_deletion import deletion_blockers
        remaining={str(t.get('id')) for t in shared.get('types',[])}
        for removed in json.loads(catalog.payload).get('types',[]):
            if str(removed.get('id')) not in remaining:
                blockers=deletion_blockers(session,state,removed)
                if blockers:raise ValueError('Type encore utilisé : '+ '; '.join(blockers[:8]))
        for other in session.scalars(select(store.V2Project).where(store.V2Project.id!=pid)).all():
            _,old=_raw(session,other.id);before=overlay(old,json.loads(catalog.payload));proposed=overlay(old,shared)
            if blocks_only:
                known={str(t.get('id')) for t in proposed.get('types',[])} | {str(t.get('name')) for t in proposed.get('types',[])}
                errors=[f"Type {i.get('type_id')} is still used by {i.get('name')}" for i in proposed.get('instances',[]) if str(i.get('type_id')) not in known]
            else:
                baseline_errors=set(_compatibility_errors(before))
                errors=[e for e in _compatibility_errors(proposed) if e not in baseline_errors]
            if errors:raise ValueError(f"Catalogue incompatible avec le projet {other.name} : {errors[0]}")
            affected.append((other.id,before,proposed))
    event=session.info.pop('version_event',None)
    saved=_persist(session,pid,state,current,username,revision,event)
    if catalog is None:
        catalog=SharedCatalog(id=1,revision=1,payload=dump(shared));session.add(catalog)
    elif changed:
        catalog.revision+=1;catalog.payload=dump(shared)
    for other_id,before,proposed in affected:
        other_revision=session.execute(update(WorkspaceCounter).where(WorkspaceCounter.id==1).values(value=WorkspaceCounter.value+1).returning(WorkspaceCounter.value)).scalar_one()
        _persist(session,other_id,proposed,before,username,other_revision)
    session.flush();saved['_catalog_revision']=catalog.revision
    return saved

def program_digest(state):
    from backend import v2_plc
    from lxml import etree
    objects=v2_plc.build_program(state)
    return digest({name:etree.tostring(obj['element'],method='c14n').decode() for name,obj in sorted(objects.items())})

def record_event(session,state,username,kind,name,digest_value=None):
    session.info['version_event']={'kind':kind,'name':name,**({'digest':digest_value} if digest_value else {})}
    return save_workspace_project(session,state,username,expected_revision=state.get('revision',0))
