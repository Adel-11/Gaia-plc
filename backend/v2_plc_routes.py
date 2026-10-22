"""Reviewed, authenticated PLCopen exports; never writes to a PLC."""
from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.db import ImportPreview


class PlcPreviewRequest(BaseModel):
    mode: Literal['full', 'partial'] = 'full'
    focus: str | None = Field(default=None, max_length=250)
    selected: list[str] | None = Field(default=None, max_length=500)


def format_report(plan: dict) -> str:
    lines = ['GAIA PLC GENERATOR — PLCopen export review',
             f"Project revision: {plan.get('revision', 0)}", '', 'Included objects:']
    lines.extend(f"- {row['name']} ({row['kind']}): {row['reason']}" for row in plan.get('included', []))
    for key, title in [('manual_deletions', 'Delete manually in CODESYS'),
                       ('manual_actions', 'Manual actions'), ('warnings', 'Warnings'), ('errors', 'Blocking errors')]:
        lines.extend(['', title + ':'])
        lines.extend(f'- {message}' for message in plan.get(key, []))
        if not plan.get(key):
            lines.append('- None')
    lines.extend(['', 'Import partial and full XML into a COPY of the CODESYS project and compile.',
                  'XSD and consistency checks do not replace the CODESYS compiler.', ''])
    return '\n'.join(lines)


def register_plc_routes(app, get_session, current_user, csrf):
    from backend import v2_engine, v2_plc, v2_plc_validation, v2_store

    def project(session):
        state = v2_store.load_project(session)
        if state is None:
            raise HTTPException(503, 'The v2 project has not been initialized')
        return state

    def program(state):
        try:
            return v2_plc.build_program(state)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, f'Cannot generate the PLC program: {exc}') from exc

    @app.get('/api/v2/plc/options')
    def options(session=Depends(get_session), user=Depends(current_user)):
        state = project(session)
        objects = program(state)
        focus = [{'value': f"type:{t['id']}", 'label': t['name'], 'kind': 'Type'} for t in state.get('types', [])]
        focus += [{'value': f"instance:{i['id']}", 'label': i['name'], 'kind': 'Instance'} for i in state.get('instances', [])]
        focus += [{'value': f'object:{name}', 'label': name, 'kind': obj['kind']} for name, obj in objects.items()]
        return {'focus': focus, 'objects': [{'name': name, 'kind': obj['kind']} for name, obj in objects.items()]}

    @app.post('/api/v2/plc/preview')
    def preview(body: PlcPreviewRequest, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        objects = program(state)
        plan = v2_plc_validation.plan_export(state, objects, mode=body.mode, focus=body.focus, selected=body.selected)
        plan['revision'] = state.get('revision', 0)
        model_validation = v2_engine.validate_project(state)
        plan['errors'] = list(dict.fromkeys([*model_validation.get('errors', []), *plan.get('errors', [])]))
        plan['warnings'] = list(dict.fromkeys([*model_validation.get('warnings', []), *plan.get('warnings', [])]))
        xml = b''
        if not plan['errors']:
            try:
                xml = v2_plc.serialize_program(objects, state, [row['name'] for row in plan['included']])
                validation = v2_plc_validation.validate_xml(xml, state, objects)
                plan['errors'] = list(dict.fromkeys([*plan['errors'], *validation.get('errors', [])]))
                plan['warnings'] = list(dict.fromkeys([*plan.get('warnings', []), *validation.get('warnings', [])]))
            except ValueError as exc:
                plan['errors'].append(str(exc))
        token = secrets.token_urlsafe(32)
        metadata = {'kind': 'v2-plc-export', 'base_revision': plan['revision'], 'mode': body.mode}
        # Store exactly the reviewed bytes and the identities in the database;
        # downloading never silently regenerates a different program.
        identities = {name: str(obj['element'].xpath('string(.//*[local-name()="ObjectId"])')) for name, obj in objects.items()}
        payload = {'plan': plan, 'xml': base64.b64encode(xml).decode('ascii'), 'object_ids': identities}
        session.add(ImportPreview(token=token, owner_id=user.id,
                                  state_payload=json.dumps(payload, ensure_ascii=False),
                                  diff_payload=json.dumps(metadata),
                                  expires_at=datetime.now(timezone.utc) + timedelta(hours=2)))
        session.commit()
        return {'token': token, **plan}

    def reviewed(token, session, user):
        row = session.get(ImportPreview, token)
        if row is None or row.owner_id != user.id:
            raise HTTPException(404, 'Export preview not found or expired')
        expiry = row.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(404, 'Export preview not found or expired')
        metadata = json.loads(row.diff_payload)
        if metadata.get('kind') != 'v2-plc-export':
            raise HTTPException(404, 'Export preview not found or expired')
        state = project(session)
        if state.get('revision', 0) != metadata['base_revision']:
            raise HTTPException(409, 'Project changed after preview; create a new export preview')
        return state, json.loads(row.state_payload)

    @app.get('/api/v2/plc/download/{token}')
    def download_xml(token: str, session=Depends(get_session), user=Depends(current_user)):
        state, payload = reviewed(token, session, user)
        if payload['plan'].get('errors'):
            raise HTTPException(409, detail={'errors': payload['plan']['errors']})
        xml = base64.b64decode(payload['xml'])
        validation = v2_plc_validation.validate_xml(xml, state, program(state))
        if validation.get('errors'):
            raise HTTPException(409, detail=validation)
        return Response(xml, media_type='application/xml', headers={
            'Content-Disposition': 'attachment; filename="Gaia-program.xml"', 'Cache-Control': 'no-store'})

    @app.get('/api/v2/plc/report/{token}')
    def download_report(token: str, session=Depends(get_session), user=Depends(current_user)):
        _, payload = reviewed(token, session, user)
        return Response(format_report(payload['plan']), media_type='text/plain; charset=utf-8', headers={
            'Content-Disposition': 'attachment; filename="Gaia-export-review.txt"', 'Cache-Control': 'no-store'})
