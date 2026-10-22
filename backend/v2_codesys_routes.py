"""Authenticated, reviewed CODESYS snapshot imports for Generator v2."""
from __future__ import annotations

import copy
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.db import ImportPreview


class ApplyCodesysPreview(BaseModel):
    token: str
    resolutions: dict[str, str] = Field(default_factory=dict)


def register_codesys_routes(app, get_session, current_user, csrf):
    from backend import v2_codesys as engine, v2_store as store

    @app.post('/api/v2/codesys/preview')
    async def preview_codesys(file: UploadFile = File(...), scope: str = Form('partial'),
                              session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        if scope not in {'partial', 'full'}:
            raise HTTPException(422, 'Scope must be partial or full')
        filename = Path(file.filename or '').name
        if not filename.lower().endswith('.xml'):
            raise HTTPException(415, 'Upload a CODESYS XML snapshot')
        data = await file.read(20 * 1024 * 1024 + 1)
        if not data or len(data) > 20 * 1024 * 1024:
            raise HTTPException(413, 'Snapshot must contain between 1 byte and 20 MB')
        state = store.load_project(session)
        if state is None:
            raise HTTPException(503, 'The v2 project has not been initialized')
        try:
            result = engine.preview_codesys(data, filename, copy.deepcopy(state), scope=scope)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, f'Could not review CODESYS snapshot: {exc}') from exc
        token = secrets.token_urlsafe(32)
        metadata = {'kind': 'v2-codesys', 'base_revision': state.get('revision', 0),
                    'filename': filename, 'scope': scope}
        session.add(ImportPreview(token=token, owner_id=user.id,
                                  state_payload=json.dumps(result.get('state', state), ensure_ascii=False),
                                  diff_payload=json.dumps({**metadata, 'changes': result.get('changes', []),
                                                           'errors': result.get('errors', [])}, ensure_ascii=False),
                                  expires_at=datetime.now(timezone.utc) + timedelta(hours=2)))
        session.commit()
        return {'token': token, 'revision': state.get('revision', 0),
                'changes': result.get('changes', []), 'errors': result.get('errors', []),
                'warnings': result.get('warnings', [])}

    @app.post('/api/v2/codesys/apply')
    def apply_codesys(body: ApplyCodesysPreview, session=Depends(get_session),
                      user=Depends(current_user), _=Depends(csrf)):
        preview = session.get(ImportPreview, body.token)
        now = datetime.now(timezone.utc)
        if not preview or preview.owner_id != user.id:
            raise HTTPException(404, 'CODESYS preview not found or expired')
        expiry = preview.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now:
            raise HTTPException(404, 'CODESYS preview not found or expired')
        metadata = json.loads(preview.diff_payload)
        if metadata.get('kind') != 'v2-codesys':
            raise HTTPException(404, 'CODESYS preview not found or expired')
        current = store.load_project(session)
        if current is None:
            raise HTTPException(503, 'The v2 project has not been initialized')
        if current.get('revision', 0) != metadata.get('base_revision'):
            raise HTTPException(409, 'Project changed after preview; create a new CODESYS preview')
        state = json.loads(preview.state_payload)
        changes = metadata.get('changes', [])
        if metadata.get('errors'):
            raise HTTPException(409, detail={'errors': metadata['errors']})
        choices = {str(k): v for k, v in body.resolutions.items()}
        required = {str(row.get('name')): row for row in changes if row.get('conflict')}
        if any(name not in choices for name in required):
            raise HTTPException(422, detail={'error': 'Every conflicting object needs an explicit resolution',
                                             'objects': sorted(set(required) - set(choices))})
        if any(value not in {'keep_generator', 'adopt_codesys'} for value in choices.values()):
            raise HTTPException(422, 'Resolution must be keep_generator or adopt_codesys')
        for name, choice in choices.items():
            row = required.get(name)
            if row is None:
                raise HTTPException(422, f'No unresolved CODESYS conflict for {name}')
            if choice == 'adopt_codesys':
                if not row.get('can_adopt'):
                    raise HTTPException(422, f'CODESYS changes for {name} cannot be adopted safely')
                _adopt_body(state, name, row)
        # Keep explicit review decisions with the snapshot so export validation can
        # block unresolved or unacknowledged divergence without losing provenance.
        snapshot = state.setdefault('snapshot', {})
        if not isinstance(snapshot, dict):
            snapshot = state['snapshot'] = {'xml': snapshot}
        old = state.get('codesys_conflicts', {})
        unresolved = dict(old) if isinstance(old, dict) else {}
        for name, choice in choices.items():
            unresolved.pop(name, None)
        state['codesys_conflicts'] = unresolved
        state['codesys_review'] = {
            'filename': metadata.get('filename'), 'scope': metadata.get('scope'),
            'resolutions': choices, 'reviewed_at': now.isoformat(), 'reviewed_by': user.username,
        }
        try:
            saved = store.save_project(session, state, username=user.username,
                                       expected_revision=metadata['base_revision'])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.delete(preview)
        session.commit()
        return {'revision': saved['revision'], 'resolutions': choices}


def _adopt_body(state: dict, name: str, change: dict) -> None:
    kind = str(change.get('kind', '')).casefold()
    imported_body = change.get('imported_st')
    if not isinstance(imported_body, str):
        raise HTTPException(422, f'CODESYS body for {name} is unavailable')
    if kind in {'fb', 'functionblock', 'function_block'} or name.startswith('FB_'):
        types = state.get('types', [])
        if isinstance(types, dict):
            types = list(types.values())
        target = None
        for typ in types:
            if not isinstance(typ, dict):
                continue
            fb = typ.get('fb')
            if isinstance(fb, dict) and (str(fb.get('name', '')) == name or
                                         (not fb.get('name') and f"FB_{typ.get('name', '')}" == name)):
                target = fb
                break
        if target is None:
            raise HTTPException(422, f'No existing function block matches {name}')
        target['body'] = imported_body
        return
    if kind in {'fnc', 'function'} or name.startswith('FNC_'):
        target = next((fn for fn in state.get('functions', [])
                       if isinstance(fn, dict) and str(fn.get('name', '')) == name), None)
        if target is None:
            raise HTTPException(422, f'No existing function matches {name}')
        target['body'] = imported_body
        return
    raise HTTPException(422, f'Only existing FB_ and FNC_ bodies can be adopted ({name})')
