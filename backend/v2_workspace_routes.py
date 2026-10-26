"""Authenticated named workspace, user administration and backup routes."""
from __future__ import annotations
import copy,io,json,re,secrets,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from fastapi import Depends,HTTPException,Request,UploadFile,File
from fastapi.responses import Response
from pydantic import BaseModel,Field
from sqlalchemy import select,func
from pwdlib import PasswordHash
from backend.db import User,ImportPreview
from backend import v2_store as store,v2_workspace as ws

class NewProject(BaseModel):
    name:str=Field(min_length=1,max_length=128)
    copy_current:bool=False
class SelectProject(BaseModel):id:int
class RenameProject(BaseModel):
    name:str=Field(min_length=1,max_length=128)
    revision:int
class MajorVersion(BaseModel):
    revision:int
    comment:str=Field(min_length=1,max_length=2000)
class NewUser(BaseModel):
    username:str=Field(min_length=2,max_length=64)
    password:str=Field(min_length=12,max_length=256)
    is_admin:bool=False
class ApplyRestore(BaseModel):token:str

def _iso(value):return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()
def _filename(name,suffix):return (re.sub(r'[^A-Za-z0-9_-]+','-',name).strip('-') or 'Gaia')+suffix

def register_workspace_routes(app,get_session,current_user,csrf):
    from backend import v2_engine as engine,v2_plc,v2_plc_validation
    def project(session):
        result=store.load_project(session)
        if result is None:raise HTTPException(404,'Projet introuvable')
        return result
    def admin(user):
        if not user.is_admin:raise HTTPException(403,'Administrateur requis')
    def saved(session,state,user,event=None):
        if event:session.info['version_event']=event
        try:result=store.save_project(session,state,user.username,expected_revision=state['revision'])
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        session.commit();return result
    def attachment(data,name,mime):
        safe=re.sub(r'[^A-Za-z0-9_.-]+','-',name)
        return Response(data,media_type=mime,headers={'Content-Disposition':f'attachment; filename="{safe}"','Cache-Control':'no-store'})
    @app.get('/api/v2/workspace')
    def overview(session=Depends(get_session),user=Depends(current_user)):
        state=project(session)
        projects=[]
        for p in session.scalars(select(store.V2Project).order_by(store.V2Project.name)).all():
            _,raw=ws._raw(session,p.id)
            projects.append({'id':p.id,'name':p.name,'version':(raw or {}).get('version','1.0.0'),'updated_at':_iso(p.updated_at)})
        history=session.scalars(select(ws.VersionEvent).where(ws.VersionEvent.project_id==state['project_id']).order_by(ws.VersionEvent.id.desc()).limit(300)).all()
        return {'active_project_id':state['project_id'],'projects':projects,'version':state['version'],'activity':state['activity'],'is_admin':user.is_admin,'history':[{'version':e.version,'date':_iso(e.created_at),'comment':e.comment,'username':e.username,'kind':e.kind} for e in history]}
    @app.post('/api/v2/workspace/select')
    def select_project(body:SelectProject,request:Request,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        if session.get(store.V2Project,body.id) is None:raise HTTPException(404,'Projet introuvable')
        request.session['project_id']=body.id
        return {'id':body.id}
    @app.post('/api/v2/workspace/projects')
    def create_project(body:NewProject,request:Request,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        name=body.name.strip()
        if not name:raise HTTPException(422,'Le nom du projet est obligatoire')
        if session.scalar(select(store.V2Project.id).where(func.lower(store.V2Project.name)==name.lower())):raise HTTPException(409,'Ce nom de projet existe déjà')
        source=project(session)
        if body.copy_current:state=copy.deepcopy(source)
        else:
            state=copy.deepcopy(engine.load_pack())
            state=ws.overlay(state,ws._catalog(source))
            for coll in ('instances','tcp_points','rtu_slaves','rtu_channels','pa_values','exchange_calls','virtual_io'):state[coll]=[]
            state['exchange_layout']={};state['generated_custom_code']={}
            for row in state.get('io_channels',[]):
                for key in ('Instance','instance_id','GVL_IO member','member_name'):row[key]=None
            if state.get('fuxa'):state['fuxa']['points']=[]
            from backend.v2_configuration import synchronize_workbooks
            from backend.v2_instance_editor import _sync_workbooks
            _sync_workbooks(state);synchronize_workbooks(state)
        # Reserve a project identity under the same global write lock as all revisions.
        ws._lock(session)
        pid=(session.scalar(select(func.max(store.V2Project.id))) or 0)+1
        for key in ('revision','project_id','version','activity','_program_digests','_catalog_revision'):state.pop(key,None)
        state['project_name']=name
        session.info['workspace_id']=pid
        try:result=store.save_project(session,state,user.username,expected_revision=0)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        session.commit();request.session['project_id']=pid
        return {'id':pid,'revision':result['revision'],'version':result['version']}
    @app.post('/api/v2/workspace/rename')
    def rename(body:RenameProject,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        state=project(session)
        if body.revision!=state['revision']:raise HTTPException(409,'Le projet a changé')
        name=body.name.strip()
        if not name:raise HTTPException(422,'Le nom est obligatoire')
        if session.scalar(select(store.V2Project.id).where(func.lower(store.V2Project.name)==name.lower(),store.V2Project.id!=state['project_id'])):raise HTTPException(409,'Ce nom de projet existe déjà')
        state['project_name']=name
        result=saved(session,state,user,{'kind':'rename','name':name})
        return {'revision':result['revision'],'version':result['version']}
    @app.post('/api/v2/workspace/major')
    def major(body:MajorVersion,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        state=project(session)
        if body.revision!=state['revision']:raise HTTPException(409,'Le projet a changé')
        if not body.comment.strip():raise HTTPException(422,'Le commentaire est obligatoire')
        result=saved(session,state,user,{'kind':'major','comment':body.comment.strip()})
        return {'revision':result['revision'],'version':result['version']}
    @app.get('/api/v2/workspace/users')
    def users(session=Depends(get_session),user=Depends(current_user)):
        admin(user)
        return {'users':[{'id':u.id,'username':u.username,'is_admin':u.is_admin,'created_at':_iso(u.created_at)} for u in session.scalars(select(User).order_by(User.username)).all()]}
    @app.post('/api/v2/workspace/users')
    def create_user(body:NewUser,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        admin(user)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{1,63}',body.username):raise HTTPException(422,'Utilisez lettres, chiffres, _, ., @ ou - pour le nom utilisateur')
        if session.scalar(select(User.id).where(func.lower(User.username)==body.username.lower())):raise HTTPException(409,'Ce nom utilisateur existe déjà')
        row=User(username=body.username,password_hash=PasswordHash.recommended().hash(body.password),is_admin=body.is_admin)
        session.add(row);session.commit()
        return {'id':row.id,'username':row.username,'is_admin':row.is_admin}
    @app.post('/api/v2/workspace/excel')
    def excel(session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        state=project(session);stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in state.get('workbooks',{}):
                if Path(name).name!=name or not name.endswith('.xlsx'):raise HTTPException(422,'Nom de classeur invalide')
                archive.writestr(name,engine.export_workbook(state,name))
        filename=_filename(state['project_name'],f'-{state["version"]}-Excel.zip')
        saved(session,state,user,{'kind':'excel_export','name':filename})
        return attachment(stream.getvalue(),filename,'application/zip')
    @app.post('/api/v2/workspace/backup')
    def backup(session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        state=project(session);filename=_filename(state['project_name'],f'-{state["version"]}-backup.json')
        state=saved(session,state,user,{'kind':'backup','name':filename})
        payload={'format':'gaia-generator-v2-backup','version':2,'state':state}
        return attachment(json.dumps(payload,ensure_ascii=False,indent=2).encode(),filename,'application/json')
    @app.post('/api/v2/workspace/restore/preview')
    async def preview_restore(file:UploadFile=File(...),session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        current=project(session);raw=await file.read(20*1024*1024+1)
        if len(raw)>20*1024*1024:raise HTTPException(413,'Sauvegarde trop volumineuse (20 Mo maximum)')
        try:
            payload=json.loads(raw,parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nombre invalide')))
            if payload.get('format')!='gaia-generator-v2-backup' or not isinstance(payload.get('state'),dict):raise ValueError('Format de sauvegarde invalide')
            state=payload['state']
            if not isinstance(state.get('types'),list) or not isinstance(state.get('workbooks'),dict):raise ValueError('Le modèle et les classeurs sont requis')
            for name in state['workbooks']:
                if Path(name).name!=name or (name not in engine.PROJECT_FILES and not re.fullmatch(r'TYPE_[A-Za-z0-9_]+\.xlsx',name)):raise ValueError('Nom de classeur non autorisé')
            state['project_id']=current['project_id'];state['project_name']=current['project_name'];state['revision']=current['revision'];state['_catalog_revision']=current['_catalog_revision']
            state['_program_digests']=copy.deepcopy(current.get('_program_digests',{}))
            errors=engine.validate_project(state)['errors']
            objects=v2_plc.build_program(state);xml=v2_plc.serialize_program(objects,state,list(objects));check=v2_plc_validation.validate_xml(xml,state,objects)
            errors+=check['errors']
        except (ValueError,TypeError,KeyError,AttributeError) as exc:return {'token':None,'errors':[str(exc)],'warnings':[],'changes':[]}
        changes=[f'Restaurer le projet actif « {current["project_name"]} » depuis {Path(file.filename or "backup.json").name}', 'Les types de la sauvegarde remplacent le catalogue partagé, sous réserve de compatibilité avec les autres projets.']
        if errors:return {'token':None,'errors':errors,'warnings':check['warnings'],'changes':changes}
        token=secrets.token_urlsafe(32)
        session.add(ImportPreview(token=token,owner_id=user.id,state_payload=ws.dump(state),diff_payload=ws.dump({'kind':'workspace-restore','base_revision':current['revision'],'project_id':current['project_id'],'filename':Path(file.filename or 'backup.json').name}),expires_at=datetime.now(timezone.utc)+timedelta(hours=1)));session.commit()
        return {'token':token,'errors':[],'warnings':check['warnings'],'changes':changes}
    @app.post('/api/v2/workspace/restore/apply')
    def apply_restore(body:ApplyRestore,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        row=session.get(ImportPreview,body.token);current=project(session)
        if row is None or row.owner_id!=user.id:raise HTTPException(404,'Aperçu introuvable')
        expires=row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
        if expires<=datetime.now(timezone.utc):raise HTTPException(404,'Aperçu expiré')
        meta=json.loads(row.diff_payload)
        if meta.get('kind')!='workspace-restore' or meta.get('project_id')!=current['project_id']:raise HTTPException(409,'Ce projet ne correspond pas à la sauvegarde préparée')
        if meta['base_revision']!=current['revision']:raise HTTPException(409,'Le projet a changé depuis la préparation')
        state=json.loads(row.state_payload)
        session.info['version_event']={'kind':'backup_import','name':meta['filename']}
        try:result=store.save_project(session,state,user.username,expected_revision=state['revision'])
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        session.delete(row);session.commit()
        return {'revision':result['revision'],'version':result['version']}
