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


class FolderRequest(BaseModel):
    revision: int
    path: str = Field(min_length=1, max_length=512)


class PlacementRequest(BaseModel):
    revision: int
    name: str = Field(min_length=1, max_length=255)
    folder: str = Field(max_length=512)


class FolderMoveRequest(BaseModel):
    revision: int
    path: str = Field(min_length=1, max_length=512)
    destination: str = Field(max_length=512)


def _clean_path(value: str, allow_root: bool = False) -> str:
    value = str(value or "").strip().strip("/")
    parts = value.split("/") if value else []
    if (not parts and not allow_root) or any(not p or p in {".", ".."} or "\\" in p for p in parts):
        raise HTTPException(422, "Invalid PLC folder path")
    return "/".join(parts)


def _type_export_names(state, objects, type_id):
    from backend import v2_plc_validation
    typ = next((t for t in state.get('types', []) if str(t.get('id')) == str(type_id)), None)
    if typ is None: raise HTTPException(404, 'PLC type not found')
    names = {str(d.get('name')) for d in typ.get('duts', []) if d.get('name')}
    fb = typ.get('fb', {}).get('name')
    if fb: names.add(str(fb))
    # Pull only true object dependencies referenced by this type's declarations/code.
    byname = {str(v.get('name', k)): k for k, v in objects.items()}
    queue = list(names)
    while queue:
        name = queue.pop()
        if name not in byname: continue
        for dep in v2_plc_validation._refs(objects[byname[name]]):
            if dep in objects and dep not in names:
                names.add(dep); queue.append(dep)
    return typ, [n for n in objects if n in names]


def _instance_export_names(state, objects):
    from backend import v2_plc_validation
    from backend.v2_program_membership import freeze_membership
    types = {str(t.get('id')): t for t in state.get('types', [])}
    names = {n for n in ('GVL_COM', 'GVL_EXC', 'GVL_IO', 'GVL_PA') if n in objects}
    membership = freeze_membership(state)
    if any(str(i.get('type_id')) in types and 'UPI' not in str(types[str(i.get('type_id'))].get('kind', '')).upper() for i in state.get('instances', [])) and 'GFB_INST' in objects: names.add('GFB_INST')
    if any(str(i.get('type_id')) in types and 'UPI' in str(types[str(i.get('type_id'))].get('kind', '')).upper() for i in state.get('instances', [])) and 'GFB_UPI' in objects: names.add('GFB_UPI')
    for inst in state.get('instances', []):
        typ = types.get(str(inst.get('type_id')))
        if typ:
            names.update(str(d.get('name')) for d in typ.get('duts', []) if d.get('name'))
            if typ.get('fb', {}).get('name'): names.add(str(typ['fb']['name']))
    names.intersection_update(objects)
    # Resolve declarations and calls transitively; deliberately do not include generated GFCs.
    byname = {str(v.get('name', k)): k for k, v in objects.items()}
    queue = list(names)
    while queue:
        name = queue.pop()
        if name not in byname: continue
        for dep in v2_plc_validation._refs(objects[byname[name]]):
            if dep in objects and dep not in names:
                names.add(dep); queue.append(dep)
    return [n for n in objects if n in names and n != 'PLC_PRG']


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

    def inventory_objects(state):
        """Folder browsing and moves do not compile communication mappings."""
        source = v2_plc._source()
        templates = v2_plc._source_objects(source)
        defaults = v2_plc._folder_map(source)
        snapshot = {o['name']: o.get('folder', '') for o in (state.get('snapshot') or {}).get('objects', []) if o.get('name')}
        placements = state.get('object_folders') or {}
        fb_names = {t.get('fb', {}).get('name') for t in state.get('types', [])}
        dut_names = {d.get('name') for t in state.get('types', []) for d in t.get('duts', [])}
        fc_names = {f.get('name') or f.get('FC') for f in state.get('exchange_functions', [])}
        objects = {}
        def add(name, kind, fallback=''):
            if name:
                if kind == 'POU':
                    kind = ('GFC' if name.startswith('GFC_') else 'GFB' if name.startswith('GFB_') else
                            'FNC' if name.startswith('FNC_') else 'EXCHANGE' if name.startswith('FC_EXC_') else
                            'FB' if name.startswith('FB_') else 'PROGRAM' if name == 'PLC_PRG' else 'POU')
                objects[name] = {'name': name, 'kind': kind, 'folder': placements.get(name, snapshot.get(name, defaults.get(name, fallback)))}
        for name, element in templates.items():
            if name.startswith('DUT_') and name != 'DUT_Union_Real_Word' and name not in dut_names: continue
            if name.startswith('FB_') and name not in fb_names: continue
            if name.startswith('FC_EXC_') and name not in fc_names: continue
            local = v2_plc._local(element)
            add(name, 'DUT' if local == 'dataType' else 'GVL' if local == 'globalVars' else 'POU')
        for typ in state.get('types', []):
            for dut in typ.get('duts', []): add(dut.get('name'), 'DUT', typ.get('folder', 'Function Blocs/FB'))
            add(typ.get('fb', {}).get('name'), 'POU', typ.get('folder', 'Function Blocs/FB'))
        for fn in state.get('exchange_functions', []): add(fn.get('name') or fn.get('FC'), 'POU', 'Functions/FC_EXC')
        return objects

    def folder_inventory(state, objects):
        folders = set(map(str, state.get('plc_folders', [])))
        folders.update(str(o.get('folder') or '') for o in objects.values())
        folders.discard('')
        for path in list(folders):
            bits = path.split('/')
            folders.update('/'.join(bits[:i]) for i in range(1, len(bits)))
        type_by_obj = {}
        for typ in state.get('types', []):
            for dut in typ.get('duts', []):
                if dut.get('name'): type_by_obj[str(dut['name'])] = {'type_id': typ.get('id'), 'role': dut.get('role')}
            if typ.get('fb', {}).get('name'): type_by_obj[str(typ['fb']['name'])] = {'type_id': typ.get('id'), 'role': 'FB'}
        return sorted(folders), [dict({'name': n, 'kind': o.get('kind'), 'folder': o.get('folder') or ''}, **type_by_obj.get(n, {})) for n, o in objects.items()]

    @app.get('/api/v2/plc/tree')
    def plc_tree(session=Depends(get_session), user=Depends(current_user)):
        state = project(session); objects = inventory_objects(state)
        folders, rows = folder_inventory(state, objects)
        return {'revision': state.get('revision', 0), 'folders': folders, 'objects': rows}

    @app.get('/api/v2/plc/object/{name}')
    def inspect_object(name: str, session=Depends(get_session), user=Depends(current_user)):
        import copy
        state = project(session)
        view_state = copy.deepcopy(state)
        if name != 'GFC_EXC':
            view_state['exchange_functions'] = [f for f in state.get('exchange_functions', []) if (f.get('name') or f.get('FC')) == name]
            view_state['exchange_calls'] = []
        try: objects = v2_plc.build_program(view_state, include_union=True)
        except (ValueError, TypeError, KeyError) as exc: raise HTTPException(422, f'Cannot display this PLC object: {exc}') from exc
        obj = objects.get(name)
        if obj is None: raise HTTPException(404, 'PLC object not found')
        from backend.v2_codesys import _declaration, _st
        element = obj['element']
        return {'name': name, 'kind': obj.get('kind'), 'folder': obj.get('folder') or '',
                'declaration': _declaration(element) or (_st(element) if obj.get('kind') in {'DUT', 'GVL'} else ''),
                'body': _st(element) if obj.get('kind') == 'POU' else ''}

    def mutate_folder(state, session, user):
        from backend import v2_store
        try: saved = v2_store.save_project(session, state, username=user.username, expected_revision=state.get('revision', 0))
        except ValueError as exc: raise HTTPException(409, str(exc)) from exc
        session.commit()
        return saved

    @app.post('/api/v2/plc/folders')
    def create_folder(body: FolderRequest, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        if body.revision != state.get('revision', 0): raise HTTPException(409, 'Project changed; refresh the PLC tree')
        path = _clean_path(body.path); folders, rows = folder_inventory(state, inventory_objects(state))
        if path in folders or any(r['name'] == path for r in rows): raise HTTPException(409, 'Folder already exists')
        state.setdefault('plc_folders', []).append(path)
        saved = mutate_folder(state, session, user)
        return {'revision': saved.get('revision'), 'folders': folder_inventory(saved, inventory_objects(saved))[0]}

    @app.post('/api/v2/plc/placement')
    def place_object(body: PlacementRequest, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        if body.revision != state.get('revision', 0): raise HTTPException(409, 'Project changed; refresh the PLC tree')
        folder = _clean_path(body.folder, allow_root=True); objects = inventory_objects(state)
        if body.name not in objects: raise HTTPException(404, 'PLC object not found')
        known = folder_inventory(state, objects)[0]
        if folder and folder not in known: raise HTTPException(404, 'PLC folder not found')
        # Moving the last object out of a directory must leave that directory
        # available, exactly like an explicit empty directory in CODESYS.
        state['plc_folders'] = list(dict.fromkeys([*state.get('plc_folders', []), *known]))
        state.setdefault('object_folders', {})[body.name] = folder
        saved = mutate_folder(state, session, user)
        return {'revision': saved.get('revision')}

    @app.post('/api/v2/plc/folders/move')
    def move_folder(body: FolderMoveRequest, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        if body.revision != state.get('revision', 0): raise HTTPException(409, 'Project changed; refresh the PLC tree')
        src = _clean_path(body.path); dest = _clean_path(body.destination, allow_root=True)
        folders, rows = folder_inventory(state, inventory_objects(state))
        if src not in folders: raise HTTPException(404, 'PLC folder not found')
        if dest and dest not in folders: raise HTTPException(404, 'Destination folder not found')
        if dest == src or dest.startswith(src + '/'): raise HTTPException(422, 'A folder cannot be moved inside itself')
        target = dest + '/' + src.rsplit('/', 1)[-1] if dest else src.rsplit('/', 1)[-1]
        affected = [p for p in folders if p == src or p.startswith(src + '/')]
        remap = {p: target + p[len(src):] for p in affected}
        if any(q in folders and q not in affected for q in remap.values()): raise HTTPException(409, 'Destination contains a folder name collision')
        state['plc_folders'] = list(dict.fromkeys(remap.get(p, p) for p in folders))
        state['object_folders'] = {n: remap.get(p, target + p[len(src):] if p.startswith(src + '/') else p) for n, p in state.get('object_folders', {}).items()}
        # Snapshot defaults also need explicit overrides so later imports cannot restore old paths.
        for row in rows:
            p = row['folder']
            if p == src or p.startswith(src + '/'):
                state['object_folders'][row['name']] = target + p[len(src):]
        saved = mutate_folder(state, session, user)
        return {'revision': saved.get('revision'), 'folders': folder_inventory(saved, inventory_objects(saved))[0]}

    def direct_export(state, names, filename, session, user, digest_key):
        import copy
        scoped_state = copy.deepcopy(state)
        scoped_state['instances'] = [] if filename.endswith('-type.xml') else scoped_state.get('instances', [])
        for key in ('tcp_points', 'rtu_channels', 'io_channels', 'virtual_io', 'exchange_calls'):
            scoped_state[key] = []
        if filename.endswith('-type.xml'):
            scoped_state['pa_values'] = []
        scoped_state['exchange_functions'] = [f for f in scoped_state.get('exchange_functions', [])
            if str(f.get('name') or f.get('FC') or '') in names]
        # Keep only selected objects plus declarations needed to render those objects.
        try: full_objects = v2_plc.build_program(scoped_state, include_union=True)
        except (ValueError, TypeError, KeyError) as exc: raise HTTPException(422, f'Cannot generate the PLC export: {exc}') from exc
        objects = {n: full_objects[n] for n in names if n in full_objects}
        missing = [n for n in names if n not in objects]
        if missing: raise HTTPException(422, {'errors': [f'Required PLC object {n} is unavailable.' for n in missing]})
        xml = v2_plc.serialize_program(objects, scoped_state, names, include_all_folders=False)
        result = v2_plc_validation.validate_xml(xml, scoped_state, objects)
        if result.get('errors'): raise HTTPException(422, {'errors': result['errors']})
        from backend import v2_workspace as workspace
        try:
            workspace.record_event(session, state, user.username, 'program_export', filename,
                                   workspace.digest(xml.decode('utf-8')), digest_key=digest_key)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return Response(xml, media_type='application/xml', headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})

    @app.post('/api/v2/plc/export/type/{type_id}')
    def export_type(type_id: str, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        import copy, re
        state = project(session)
        typ = next((t for t in state.get('types', []) if str(t.get('id')) == str(type_id)), None)
        if typ is None: raise HTTPException(404, 'PLC type not found')
        isolated = copy.deepcopy(state); isolated['instances'] = []
        for key in ('tcp_points', 'rtu_channels', 'io_channels', 'virtual_io', 'pa_values', 'exchange_calls'): isolated[key] = []
        fbtext = str(typ.get('fb', {}).get('body') or '')
        fn_names = set(re.findall(r'\b(?:FC_EXC|FNC)_[A-Za-z0-9_]+', fbtext))
        isolated['exchange_functions'] = [f for f in state.get('exchange_functions', []) if str(f.get('name') or f.get('FC') or '') in fn_names]
        try: objects = v2_plc.build_program(isolated, include_union=True)
        except (ValueError, TypeError, KeyError) as exc: raise HTTPException(422, f'Cannot generate the PLC export: {exc}') from exc
        typ, names = _type_export_names(isolated, objects, type_id)
        if not names: raise HTTPException(422, {'errors': ['This type has no exportable PLC declarations.']})
        return direct_export(state, names, f"{typ.get('name', type_id)}-type.xml", session, user, f'program_export:type:{type_id}')

    @app.post('/api/v2/plc/export/instances')
    def export_instances(session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        import copy
        state = project(session); isolated = copy.deepcopy(state)
        for key in ('tcp_points', 'rtu_channels', 'io_channels', 'virtual_io', 'pa_values', 'exchange_calls'): isolated[key] = []
        isolated['exchange_functions'] = []
        try: objects = v2_plc.build_program(isolated, include_union=True)
        except (ValueError, TypeError, KeyError) as exc: raise HTTPException(422, f'Cannot generate the PLC export: {exc}') from exc
        names = _instance_export_names(isolated, objects)
        if not names: raise HTTPException(422, {'errors': ['No declared instances or instance program objects are available.']})
        return direct_export(state, names, 'Gaia-instances.xml', session, user, 'program_export:instances')

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
        plan['mode'] = body.mode
        model_validation = v2_engine.validate_project(state)
        plan['errors'] = list(dict.fromkeys([*model_validation.get('errors', []), *plan.get('errors', [])]))
        plan['warnings'] = list(dict.fromkeys([*model_validation.get('warnings', []), *plan.get('warnings', [])]))
        xml = b''
        if not plan['errors']:
            try:
                xml = v2_plc.serialize_program(objects, state, [row['name'] for row in plan['included']], include_all_folders=body.mode == 'full')
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
        from backend import v2_workspace as workspace
        import re
        fingerprint=workspace.program_digest(state)
        major,minor,patch=[int(x) for x in state.get('version','1.0.0').split('.')]
        version=f'{major}.{minor+1}.0' if fingerprint!=state.get('_program_digests',{}).get('program_export') else state.get('version','1.0.0')
        filename=(re.sub(r'[^A-Za-z0-9_-]+','-',state.get('project_name','Gaia')).strip('-') or 'Gaia')+'-'+version+'-'+payload['plan'].get('mode','program')+'.xml'
        try: saved=workspace.record_event(session,state,user.username,'program_export',filename,fingerprint)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        row=session.get(ImportPreview,token); metadata=json.loads(row.diff_payload)
        metadata['base_revision']=saved['revision']; row.diff_payload=json.dumps(metadata)
        payload['plan']['revision']=saved['revision']; row.state_payload=json.dumps(payload,ensure_ascii=False)
        session.commit()
        return Response(xml, media_type='application/xml', headers={
            'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control':'no-store'})

    @app.get('/api/v2/plc/report/{token}')
    def download_report(token: str, session=Depends(get_session), user=Depends(current_user)):
        _, payload = reviewed(token, session, user)
        return Response(format_report(payload['plan']), media_type='text/plain; charset=utf-8', headers={
            'Content-Disposition': 'attachment; filename="Gaia-export-review.txt"', 'Cache-Control': 'no-store'})
