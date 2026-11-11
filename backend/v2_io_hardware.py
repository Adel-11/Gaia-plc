"""Edit the hardware mappings of a native CODESYS rack export."""
from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from lxml import etree
from pydantic import BaseModel, Field

from backend.v2_codesys import _safe_parse
from backend.v2_plc import NS, ODATA, _ensure_io_declarations, _source, _source_objects

STATE_KEY = 'codesys_io_hardware'
MAX_XML_BYTES = 2 * 1024 * 1024
VARIABLE = re.compile(r'[A-Za-z_]\w*\.GVL_IOMAP\.([A-Za-z_]\w*)', re.I)
ELEMENT_TYPES = {'bit': 'BOOL', 'byte': 'BYTE', 'word': 'WORD', 'dword': 'DWORD', 'lword': 'LWORD'}
SCALAR_TYPES = set(ELEMENT_TYPES.values())


def elements(parent, prefix=()):
    for node in parent.findall('Element'):
        name = node.get('name')
        if name:
            path = (*prefix, name)
            yield path, node
            yield from elements(node, path)


@dataclass
class Channel:
    key: str
    module: str
    label: str
    path: tuple[str, ...]
    iec_type: str
    direction: str
    parameter: etree._Element
    variable: str
    locked: bool


def read_hardware(data):
    root = _safe_parse(data)
    if any(root.iter(f'{NS}pou', f'{NS}dataType', f'{NS}globalVars', f'{NS}resource', f'{NS}task')):
        raise ValueError('Chargez un export CODESYS du rack E/S seul, sans objets du programme.')
    channels = {}
    for device in root.iter('Device'):
        if device.getparent().get('name') != 'Device':
            continue
        config = next(device.iterancestors(f'{NS}configuration'), None)
        if config is None:
            continue
        module = config.get('name')
        identity = config.findtext(f'{NS}addData/{NS}data[@name="{ODATA}"]/{NS}ObjectId')
        if not module or not identity:
            continue
        for connector in device.findall('DeviceType/Connector'):
            connector_id = connector.get('connectorId')
            for param in connector.findall('HostParameterSet/Parameter'):
                attributes, values = param.find('Attributes'), param.find('Value')
                if attributes is None or values is None or not connector_id or not param.get('ParameterId'):
                    continue
                direction = attributes.get('channel')
                if direction not in {'input', 'output'}:
                    continue
                mapping = param.find('Mapping')
                nodes = dict(elements(mapping)) if mapping is not None else {}
                for path, node in elements(values):
                    label = node.get('visiblename', '')
                    typed = re.fullmatch(r'(bit|byte|word|dword|lword)\d+', path[-1], re.I)
                    if not typed or len(node) or not re.fullmatch(r'Ch\s*\d+', label, re.I) or not all(re.fullmatch(r'[A-Za-z_]\w*', part) for part in path):
                        continue
                    saved = nodes.get(path)
                    parent_mapped = any((nodes[parent].text or '').strip() for parent in (path[:i] for i in range(1, len(path))) if parent in nodes)
                    locked = parent_mapped or (saved is not None and (len(saved) > 0 or set(saved.attrib) != {'name'}))
                    key = ':'.join((identity, connector_id, param.get('ParameterId'), '/'.join(path)))
                    if key in channels:
                        raise ValueError('Le fichier contient deux canaux E/S avec le même identifiant.')
                    channels[key] = Channel(key, module, label, path, ELEMENT_TYPES[typed[1].lower()], direction, param,
                                            (saved.text or '').strip() if saved is not None else '', locked)
    if not channels:
        raise ValueError('Aucun canal E/S pris en charge dans ce XML. Exportez le rack et ses modules depuis CODESYS.')
    return root, channels


def _xml_bytes(root):
    return etree.tostring(root.getroottree(), encoding='UTF-8', xml_declaration=True)


def _set_mapping(channel, variable):
    mapping = channel.parameter.find('Mapping')
    if variable:
        if mapping is None:
            mapping = etree.SubElement(channel.parameter, 'Mapping')
        parent = mapping
        for name in channel.path:
            child = parent.find(f'Element[@name="{name}"]')
            if child is None:
                child = etree.SubElement(parent, 'Element', name=name)
            parent = child
        parent.text = variable
    elif mapping is not None:
        leaf = dict(elements(mapping)).get(channel.path)
        while leaf is not None and leaf is not mapping:
            parent = leaf.getparent()
            parent.remove(leaf)
            if parent is mapping or len(parent) or (parent.text or '').strip() or set(parent.attrib) != {'name'}:
                break
            leaf = parent
        if not len(mapping) and not mapping.attrib and not (mapping.text or '').strip():
            channel.parameter.remove(mapping)


def _variable_options(state):
    variables = {}
    manual = (state.get('st_overrides') or {}).get('GVL_IOMAP', {}).get('declaration')
    if manual is None:
        gvl = _source_objects(_source(state)).get('GVL_IOMAP')
        type_ids = {str(typ.get('id') or typ.get('name')) for typ in state.get('types', [])}
        instance_names = {str(row.get('name') or row.get('id')) for row in state.get('instances', []) if str(row.get('type_id')) in type_ids}
        _ensure_io_declarations(gvl, state, instance_names)
        if gvl is not None:
            for variable in gvl.findall(f'{NS}variable'):
                kind = variable.find(f'{NS}type/*')
                if kind is not None:
                    variables[variable.get('name')] = etree.QName(kind).localname.upper()
    else:
        from backend.v2_plc_validation import _st_code
        for names, kind in re.findall(r'(?im)^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:\s*(\w+)', _st_code(manual)):
            variables.update({name.strip(): kind.upper() for name in names.split(',')})
    directions = {}
    for row in [*state.get('io_channels', []), *state.get('virtual_io', [])]:
        variable = str(row.get('GVL_IOMAP variable') or '').split('.')[-1].casefold()
        direction = {'DI': 'input', 'AI': 'input', 'DO': 'output', 'AO': 'output'}.get(row.get('I/O type'))
        if direction:
            directions[variable] = direction
    return {name.casefold(): {'name': name, 'iec_type': kind, 'direction': directions.get(name.casefold())}
            for name, kind in variables.items() if name and kind in SCALAR_TYPES}


def _mapping_errors(channels, variables, application):
    errors, input_targets, output_targets = {}, {}, {}
    for channel in channels.values():
        match = VARIABLE.fullmatch(channel.variable)
        if not match or channel.locked:
            continue
        name = match[1].casefold()
        option = variables.get(name)
        target = channel.variable.casefold()
        if target.startswith(f'{application.casefold()}.'):
            if not option:
                errors[channel.key] = f'{match[1]} est absent de GVL_IOMAP.'
            elif option['iec_type'] != channel.iec_type or option['direction'] not in {None, channel.direction}:
                errors[channel.key] = f'{match[1]} doit être compatible avec {channel.iec_type} et le sens du canal.'
        if channel.direction == 'input':
            input_targets.setdefault(target, []).append(channel.key)
        else:
            output_targets.setdefault(target, []).append(channel.key)
    for target, keys in input_targets.items():
        if len(keys) > 1 or target in output_targets:
            for key in keys + output_targets.get(target, []):
                errors[key] = 'Une variable reliée à une entrée ne peut pas être partagée avec un autre canal.'
    return errors


class MappingUpdate(BaseModel):
    revision: int
    changes: dict[str, str | None] = Field(max_length=500)


def register_io_hardware_routes(app, get_session, current_user, csrf):
    from backend import v2_store

    def project(session):
        state = v2_store.load_project(session)
        if state is None:
            raise HTTPException(503, 'Projet indisponible.')
        return state

    def check_revision(state, revision):
        if state['revision'] != revision:
            raise HTTPException(409, 'Le projet a changé. Actualisez avant de réessayer.')

    def parse(data):
        try:
            return read_hardware(data)
        except (ValueError, etree.XMLSyntaxError) as exc:
            raise HTTPException(422, str(exc)) from exc

    def view(state):
        hardware = state.get(STATE_KEY)
        if not hardware:
            return {'revision': state['revision'], 'hardware': None}
        _, channels = parse(hardware['xml'].encode('utf-8'))
        options = _variable_options(state)
        errors = _mapping_errors(channels, options, hardware['application'])
        return {'revision': state['revision'], 'hardware': {'filename': hardware['filename'], 'application': hardware['application'],
                'channels': [{key: getattr(c, key) for key in ('key', 'module', 'label', 'iec_type', 'direction', 'variable', 'locked')}
                             | {'error': errors.get(c.key)} for c in channels.values()],
                'variables': list(options.values())}}

    def save(session, state, user):
        try:
            saved = v2_store.save_project(session, state, username=user.username, expected_revision=state['revision'])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return view(saved)

    @app.get('/api/v2/io-hardware')
    def load(session=Depends(get_session), user=Depends(current_user)):
        return view(project(session))

    @app.post('/api/v2/io-hardware/template')
    async def upload(file: UploadFile = File(...), revision: int = Form(...), session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        check_revision(state, revision)
        data = await file.read(MAX_XML_BYTES + 1)
        if not data or len(data) > MAX_XML_BYTES:
            raise HTTPException(413, 'Le fichier XML doit contenir au plus 2 Mo.')
        root, channels = parse(data)
        applications = {channel.variable.split('.', 1)[0] for channel in channels.values() if VARIABLE.fullmatch(channel.variable)}
        application = next(iter(applications)) if len(applications) == 1 else 'Application'
        state[STATE_KEY] = {'filename': re.split(r'[/\\]', file.filename or 'rack.xml')[-1][:200],
                            'application': application, 'xml': _xml_bytes(root).decode('utf-8')}
        return save(session, state, user)

    @app.post('/api/v2/io-hardware/mappings')
    def update(body: MappingUpdate, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        check_revision(state, body.revision)
        hardware = state.get(STATE_KEY)
        if not hardware:
            raise HTTPException(409, 'Chargez d’abord un XML du rack.')
        root, channels = parse(hardware['xml'].encode('utf-8'))
        options = _variable_options(state)
        edited = set()
        for key, name in body.changes.items():
            channel = channels.get(key)
            if channel is None or channel.locked:
                raise HTTPException(422, 'Ce canal ne peut pas être modifié dans le générateur.')
            option = options.get(name.casefold()) if name else None
            if name and not option:
                raise HTTPException(422, f'{name} est absent de GVL_IOMAP.')
            variable = f'{hardware["application"]}.GVL_IOMAP.{option["name"]}' if option else ''
            if channel.variable != variable:
                channel.variable = variable
                edited.add(key)
                _set_mapping(channel, variable)
        errors = _mapping_errors(channels, options, hardware['application'])
        if blocked := [message for key, message in errors.items() if key in edited]:
            raise HTTPException(422, ' '.join(dict.fromkeys(blocked)))
        if not edited:
            return view(state)
        state[STATE_KEY] = {**hardware, 'xml': _xml_bytes(root).decode('utf-8')}
        return save(session, state, user)

    @app.get('/api/v2/io-hardware/export')
    def download(session=Depends(get_session), user=Depends(current_user)):
        state = project(session)
        hardware = state.get(STATE_KEY)
        if not hardware:
            raise HTTPException(404, 'Chargez d’abord un XML du rack.')
        data = hardware['xml'].encode('utf-8')
        _, channels = parse(data)
        if _mapping_errors(channels, _variable_options(state), hardware['application']):
            raise HTTPException(409, 'Corrigez les liaisons signalées avant de télécharger le XML.')
        return Response(data, media_type='application/xml', headers={'Content-Disposition': 'attachment; filename="Gaia-mapping-ES.xml"', 'Cache-Control': 'no-store'})
