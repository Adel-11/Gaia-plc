"""Reviewed removal of unused types from the shared catalog."""
from __future__ import annotations

import copy
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.db import ImportPreview


class DeleteType(BaseModel):
    revision: int
    type_id: str


class ApplyDelete(BaseModel):
    token: str


def type_objects(typ):
    return {str(name) for name in [typ.get('fb', {}).get('name'),
            *(d.get('name') for d in typ.get('duts', []))] if name}


def code_identifiers(text):
    tokens = re.findall(r"\(\*.*?\*\)|//[^\r\n]*|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z0-9_]*", text or '', re.S)
    return {token.casefold() for token in tokens if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', token)}


def project_blockers(state, typ):
    """Report real declarations/code dependencies, ignoring comments and strings."""
    from backend import v2_plc as plc
    aliases = {str(typ.get('id')), str(typ.get('name'))}
    blockers = [f"Instance {i.get('name') or i.get('id')}" for i in state.get('instances', [])
                if str(i.get('type_id')) in aliases]
    removed = {name.casefold() for name in type_objects(typ)}
    # Build a disposable model: program generation may initialize legacy memberships.
    objects = plc.build_program(copy.deepcopy(state))
    for name, obj in objects.items():
        if name.casefold() in removed:
            continue
        element = obj['element']
        derived = {str(el.get('name', '')).casefold() for el in element.iter(plc.NS + 'derived')}
        text = '\n'.join(el.text or '' for el in element.iter(plc.HNS + 'xhtml'))
        refs = removed & (derived | code_identifiers(text))
        if refs:
            blockers.append(f"Bloc {name} → {', '.join(sorted(refs))}")
    return list(dict.fromkeys(blockers))


def deletion_blockers(session, proposed, typ):
    """Check every project against the proposed shared catalog."""
    from backend import v2_store as store, v2_workspace as ws
    result = []
    for project in session.scalars(select(store.V2Project).order_by(store.V2Project.id)).all():
        if project.id == proposed.get('project_id', session.info.get('workspace_id', 1)):
            state = proposed
        else:
            _, raw = ws._raw(session, project.id)
            state = ws.overlay(raw, ws._catalog(proposed))
        for detail in project_blockers(state, typ):
            result.append(f"{project.name} : {detail}")
    return result


def register_type_deletion_routes(app, get_session, current_user, csrf):
    from backend import v2_store as store

    def project_for(session):
        state = store.load_project(session)
        if state is None:
            raise HTTPException(503, 'Projet non initialisé')
        return state

    @app.post('/api/v2/types/delete/preview')
    def preview(body: DeleteType, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        current = project_for(session)
        if body.revision != current.get('revision', 0):
            raise HTTPException(409, 'Le projet a changé ; actualisez avant de supprimer un type')
        typ = next((t for t in current.get('types', []) if body.type_id in {str(t.get('id')), str(t.get('name'))}), None)
        if typ is None:
            raise HTTPException(404, 'Type introuvable')
        state = copy.deepcopy(current)
        state['types'] = [t for t in state['types'] if t.get('id') != typ.get('id')]
        state.get('workbooks', {}).pop(typ.get('source_file'), None)
        blockers = deletion_blockers(session, state, typ)
        changes = [f"Supprimer le type partagé {typ['name']} de tous les projets",
                   *[f"Supprimer le bloc {name}" for name in sorted(type_objects(typ))]]
        if typ.get('source_file'):
            changes.append(f"Supprimer le classeur {typ['source_file']}")
        if blockers:
            return {'token': None, 'type_name': typ['name'], 'errors': ['Ce type est encore utilisé. Retirez les dépendances ci-dessous avant de le supprimer.'],
                    'blockers': blockers, 'changes': changes}
        token = secrets.token_urlsafe(32)
        metadata = {'kind': 'v2-type-delete', 'base_revision': current['revision'], 'project_id': current['project_id']}
        session.add(ImportPreview(token=token, owner_id=user.id, state_payload=json.dumps(state, ensure_ascii=False),
                    diff_payload=json.dumps(metadata), expires_at=datetime.now(timezone.utc) + timedelta(minutes=20)))
        session.commit()
        return {'token': token, 'type_name': typ['name'], 'errors': [], 'blockers': [], 'changes': changes}

    @app.post('/api/v2/types/delete/apply')
    def apply(body: ApplyDelete, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        preview = session.get(ImportPreview, body.token)
        if not preview or preview.owner_id != user.id:
            raise HTTPException(404, 'Suppression introuvable ou expirée')
        expiry = preview.expires_at.replace(tzinfo=timezone.utc) if preview.expires_at.tzinfo is None else preview.expires_at
        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(404, 'Suppression introuvable ou expirée')
        meta = json.loads(preview.diff_payload)
        if meta.get('kind') != 'v2-type-delete':
            raise HTTPException(404, 'Suppression introuvable ou expirée')
        current = project_for(session)
        if meta.get('project_id') != current['project_id'] or meta.get('base_revision') != current['revision']:
            raise HTTPException(409, 'Le projet a changé ; préparez à nouveau la suppression')
        state = json.loads(preview.state_payload)
        session.info['type_blocks_only'] = True
        try:
            saved = store.save_project(session, state, username=user.username, expected_revision=current['revision'])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            session.info.pop('type_blocks_only', None)
        session.delete(preview)
        session.commit()
        return {'revision': saved['revision']}
