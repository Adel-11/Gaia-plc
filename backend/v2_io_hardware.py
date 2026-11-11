"""Edit the hardware mappings of a native CODESYS rack export."""
from __future__ import annotations

import csv
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO, StringIO
from typing import Literal

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from lxml import etree
from pydantic import BaseModel, Field

from backend.v2_codesys import _safe_parse
from backend.v2_io_copies import assign_copy, authored_references, copy_options, remove_copy, variable_direction, variable_rows
from backend.v2_plc import NS, ODATA, ROOT, _ensure_io_declarations, _source, _source_objects

STATE_KEY = 'codesys_io_hardware'
MAX_XML_BYTES = 2 * 1024 * 1024
VARIABLE = re.compile(r'[A-Za-z_]\w*\.GVL_IOMAP\.([A-Za-z_]\w*)', re.I)
ELEMENT_TYPES = {'bit': 'BOOL', 'byte': 'BYTE', 'word': 'WORD', 'dword': 'DWORD', 'lword': 'LWORD'}
SCALAR_TYPES = set(ELEMENT_TYPES.values())
GAIA_RACK = ROOT / 'reference-v2' / 'v8' / 'rack-io.xml'
GVL_OBJECTS = ('GVL_IO', 'GVL_IOMAP')
GFC_OBJECTS = ('GFC_IO_IN', 'GFC_IO_OUT')
RACK_FILENAME = 'Gaia-mapping-ES.xml'
MAPPING_CSV_FILENAME = 'Gaia-mappage-canaux.csv'
GVL_FILENAME = 'Gaia-GVL-ES.xml'
GFC_FILENAME = 'Gaia-GFC-ES.xml'


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
    if next(root.iter(f'{NS}pou', f'{NS}dataType', f'{NS}globalVars', f'{NS}resource', f'{NS}task'), None) is not None:
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


def _variable_options(state, *, generated=False):
    variables = {}
    manual = (state.get('st_overrides') or {}).get('GVL_IOMAP', {}).get('declaration')
    if manual is None or generated:
        gvl = _source_objects(_source(state)).get('GVL_IOMAP')
        type_ids = {str(typ.get('id') or typ.get('name')) for typ in state.get('types', [])}
        instance_names = {str(row.get('name') or row.get('id')) for row in state.get('instances', []) if str(row.get('type_id')) in type_ids}
        _ensure_io_declarations(gvl, state, instance_names)
        if gvl is not None:
            for variable in gvl.findall(f'{NS}variable'):
                kind = variable.find(f'{NS}type/*')
                if kind is not None:
                    variables[variable.get('name')] = (kind.get('name') or etree.QName(kind).localname).upper()
    else:
        from backend.v2_plc_validation import _st_code
        for names, kind in re.findall(r'(?im)(?:^|(?<=;))\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*(?:AT\s+%[\w*.]+\s*)?:\s*(\w+)', _st_code(manual)):
            variables.update({name.strip(): kind.upper() for name in names.split(',')})
    directions = {}
    for _, row, key in variable_rows(state):
        if direction := variable_direction(row):
            directions[key] = direction
    return {name.casefold(): {'name': name, 'iec_type': kind, 'direction': directions.get(name.casefold())}
            for name, kind in sorted(variables.items(), key=lambda item: str(item[0]).casefold()) if name}


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


def mapping_csv(state):
    """Export the editable channels using the CODESYS I/O mapping CSV format."""
    hardware = state.get(STATE_KEY)
    if not hardware:
        return None
    try:
        _, channels = read_hardware(hardware['xml'].encode('utf-8'))
    except etree.XMLSyntaxError as exc:
        raise ValueError('Le XML du rack enregistré est invalide. Rechargez-le dans Communications → I/O.') from exc
    errors = _mapping_errors(channels, _variable_options(state), hardware['application'])
    if errors:
        details = [f'{channels[key].module} {channels[key].label} : {message}' for key, message in errors.items()]
        raise ValueError('Corrigez le mappage dans Communications → I/O. ' + ' '.join(details))
    output = StringIO(newline='')
    output.write('//CoDeSys Mapping Export V1.2\r\n'
                 '//Mapped variable;//Parameter name @ counter in device;//Unit;//Description;//IEC address;//Device name\r\n'
                 '//Important: change only first, third or fourth column in Excel or add variable name before first\r\n')
    writer = csv.writer(output, delimiter=';')
    seen = set()
    for channel in channels.values():
        key = (channel.module.casefold(), channel.label.casefold())
        if key in seen:
            raise ValueError(f'Plusieurs canaux portent le nom {channel.label} sur {channel.module}. Exportez le mappage depuis CODESYS pour les distinguer.')
        seen.add(key)
        if channel.locked or (channel.variable and not VARIABLE.fullmatch(channel.variable)):
            continue
        fields = [channel.label, '', '', '', channel.module]
        if channel.variable:
            writer.writerow([channel.variable, *fields])
        else:
            # CODESYS trims an unquoted space and keeps the old mapping.
            output.write('" ";')
            writer.writerow(fields)
    return output.getvalue().encode('utf-8')


def _cards(state, channels):
    inventory = {str(row.get('CODESYS device') or row.get('device')): row for row in state.get('io_modules', [])
                 if row.get('Module') or row.get('module')}
    cards = []
    for name in dict.fromkeys(channel.module for channel in channels.values()):
        row = inventory.get(name, {})
        cards.append({'name': name, 'slot': row.get('Slot'), 'model': row.get('Module')})
    return sorted(cards, key=lambda card: (card['slot'] is None, str(card['slot']).zfill(3)))


def _can_load_gaia(state):
    from backend.v2_hmi import is_hmi
    return not state.get(STATE_KEY) and is_hmi(state) and str(state.get('project_name', '')).casefold() == 'gaia' and GAIA_RACK.is_file()


def _attachment(data, filename, media_type='application/xml', revision=None):
    headers = {'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'}
    if revision is not None:
        headers['X-Gaia-Revision'] = str(revision)
    return Response(data, media_type=media_type, headers=headers)


class MappingUpdate(BaseModel):
    revision: int
    changes: dict[str, str | None] = Field(max_length=500)


class RackRevision(BaseModel):
    revision: int


class CreateChannelVariable(RackRevision):
    channel_key: str = Field(max_length=300)
    signal: str = Field(max_length=80)


class CopyUpdate(BaseModel):
    revision: int
    action: Literal['assign', 'disconnect', 'delete']
    iomap: str = Field(max_length=256)
    instance: str | None = None
    member: str | None = None
    direction: Literal['input', 'output'] | None = None
    channel_key: str | None = None


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
        options = _variable_options(state)
        listed = options
        if (state.get('st_overrides') or {}).get('GVL_IOMAP', {}).get('declaration') is not None:
            listed = {**options, **{key: {**option, 'missing': True}
                                   for key, option in _variable_options(state, generated=True).items() if key not in options}}
        result = {'revision': state['revision'], 'hardware': None, 'can_load_gaia': _can_load_gaia(state), **copy_options(state, listed)}
        if not hardware:
            return result
        _, channels = parse(hardware['xml'].encode('utf-8'))
        errors = _mapping_errors(channels, options, hardware['application'])
        result['hardware'] = {'filename': hardware['filename'], 'application': hardware['application'],
                'cards': _cards(state, channels),
                'channels': [{key: getattr(c, key) for key in ('key', 'module', 'label', 'iec_type', 'direction', 'variable', 'locked')}
                             | {'error': errors.get(c.key)} for c in channels.values()],
                'variables': [option for option in options.values() if option['iec_type'] in SCALAR_TYPES]}
        return result

    def save(session, state, user):
        try:
            saved = v2_store.save_project(session, state, username=user.username, expected_revision=state['revision'])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return view(saved)

    def install_template(state, data, filename):
        root, channels = parse(data)
        applications = {channel.variable.split('.', 1)[0] for channel in channels.values() if VARIABLE.fullmatch(channel.variable)}
        application = next(iter(applications)) if len(applications) == 1 else 'Application'
        state[STATE_KEY] = {'filename': filename, 'application': application, 'xml': _xml_bytes(root).decode('utf-8')}

    def saved_rack(state):
        hardware = state.get(STATE_KEY)
        if not hardware:
            raise HTTPException(409, 'Chargez d’abord le XML du rack.')
        return hardware, *parse(hardware['xml'].encode('utf-8'))

    def rack_export(state):
        hardware, _, channels = saved_rack(state)
        if _mapping_errors(channels, _variable_options(state), hardware['application']):
            raise HTTPException(409, 'Corrigez les liaisons signalées avant de télécharger le XML du rack.')
        return hardware['xml'].encode('utf-8')

    def csv_export(state):
        try:
            data = mapping_csv(state)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if data is None:
            raise HTTPException(409, 'Chargez d’abord le XML du rack.')
        return data

    @app.get('/api/v2/io-hardware')
    def load(session=Depends(get_session), user=Depends(current_user)):
        return view(project(session))

    @app.post('/api/v2/io-hardware/copies')
    def update_copy(body: CopyUpdate, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        from backend.v2_configuration import synchronize_io_workbook

        state = project(session)
        check_revision(state, body.revision)
        options = _variable_options(state)
        key = body.iomap.casefold()
        option = options.get(key)
        if option is None and body.action in {'delete', 'disconnect'}:
            option = _variable_options(state, generated=True).get(key)
        if option is None:
            raise HTTPException(404, 'Cette variable est absente de GVL_IOMAP.')
        name = option['name']
        hardware = state.get(STATE_KEY)
        try:
            if body.action == 'assign':
                if not body.direction:
                    raise ValueError('Choisissez le sens de la recopie.')
                assign_copy(state, option, body.instance, body.member, body.direction)
            else:
                if body.action == 'delete' and key in options and (state.get('st_overrides') or {}).get('GVL_IOMAP', {}).get('declaration') is not None:
                    raise ValueError(f'GVL_IOMAP utilise une déclaration ST manuelle. Retirez {name} dans l’éditeur ST ou revenez à la déclaration générée avant de supprimer la variable ici.')
                remove_copy(state, name, delete=body.action == 'delete')

            if body.channel_key and (body.action != 'assign' or not hardware):
                raise ValueError('Chargez un XML du rack avant d’assigner un canal matériel.')
            if hardware and body.action in {'assign', 'delete'}:
                root, channels = parse(hardware['xml'].encode('utf-8'))
                path = f'{hardware["application"]}.GVL_IOMAP.{name}'
                linked = [channel for channel in channels.values() if channel.variable.casefold() == path.casefold()]
                if body.action == 'delete':
                    for channel in linked:
                        if channel.locked:
                            raise ValueError('Cette variable est utilisée par un mappage matériel de groupe. Retirez ce mappage dans CODESYS et rechargez le XML du rack.')
                        _set_mapping(channel, '')
                    if any((node.text or '').strip().casefold() == path.casefold() for mapping in root.iter('Mapping') for node in mapping.iter()):
                        raise ValueError('Cette variable est encore référencée dans une partie non éditable du rack. Retirez la liaison dans CODESYS et rechargez le XML.')
                    if linked:
                        state[STATE_KEY] = {**hardware, 'xml': _xml_bytes(root).decode('utf-8')}
                else:
                    if body.channel_key:
                        channel = channels.get(body.channel_key)
                        if channel is None or channel.locked or channel.direction != body.direction or channel.iec_type != option['iec_type']:
                            raise ValueError('Ce canal matériel n’est pas compatible avec la variable et le sens choisis.')
                        if channel.variable and channel.variable.casefold() != path.casefold():
                            raise ValueError('Ce canal est déjà relié à une autre variable. Déliez-le dans le panneau matériel avant de le réutiliser.')
                        channel.variable = path
                        _set_mapping(channel, path)
                        if channel not in linked:
                            linked.append(channel)
                    errors = _mapping_errors(channels, {**options, name.casefold(): {**option, 'direction': body.direction}}, hardware['application'])
                    if blocked := [errors[channel.key] for channel in linked if channel.key in errors]:
                        raise ValueError(' '.join(dict.fromkeys(blocked)))
                    if body.channel_key:
                        state[STATE_KEY] = {**hardware, 'xml': _xml_bytes(root).decode('utf-8')}

            if body.action in {'delete', 'disconnect'} and (references := authored_references(state, name)):
                # A surviving reference is fine for a disconnected declaration, but a
                # manual GFC copy would make the requested disconnection misleading.
                if body.action == 'disconnect':
                    references = [ref for ref in references if ref in {'GFC_IO_IN', 'GFC_IO_OUT'}]
                if references:
                    raise ValueError(f'{name} est encore utilisée dans le ST de {", ".join(references)}. Modifiez ce code dans l’éditeur ST puis réessayez.')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        synchronize_io_workbook(state)
        return save(session, state, user)

    @app.post('/api/v2/io-hardware/template')
    async def upload(file: UploadFile = File(...), revision: int = Form(...), session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        check_revision(state, revision)
        data = await file.read(MAX_XML_BYTES + 1)
        if not data or len(data) > MAX_XML_BYTES:
            raise HTTPException(413, 'Le fichier XML doit contenir au plus 2 Mo.')
        install_template(state, data, re.split(r'[/\\]', file.filename or 'rack.xml')[-1][:200])
        return save(session, state, user)

    @app.post('/api/v2/io-hardware/template/gaia')
    def load_gaia_rack(body: RackRevision, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        check_revision(state, body.revision)
        if not _can_load_gaia(state):
            raise HTTPException(409, 'Le rack de référence est réservé au projet Gaia V8 sans rack enregistré.')
        install_template(state, GAIA_RACK.read_bytes(), 'Gaia V8 — rack de référence.xml')
        return save(session, state, user)

    @app.post('/api/v2/io-hardware/mappings')
    def update(body: MappingUpdate, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = project(session)
        check_revision(state, body.revision)
        hardware, root, channels = saved_rack(state)
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

    @app.post('/api/v2/io-hardware/channels/create-variable')
    def create_channel_variable(body: CreateChannelVariable, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        from backend.v2_catalog import _io_type
        from backend.v2_io_copies import add_iomap_row

        state = project(session)
        check_revision(state, body.revision)
        hardware, root, channels = saved_rack(state)
        channel = channels.get(body.channel_key)
        if channel is None or channel.locked or channel.variable:
            raise HTTPException(422, 'Choisissez un canal libre. Pour remplacer une liaison, retirez-la d’abord du canal.')
        if (state.get('st_overrides') or {}).get('GVL_IOMAP', {}).get('declaration') is not None:
            raise HTTPException(422, 'GVL_IOMAP utilise une déclaration manuelle. Déclarez la variable dans l’éditeur ST, puis sélectionnez-la sur la carte.')
        signal = body.signal.strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', signal):
            raise HTTPException(422, 'Utilisez un nom IEC : lettres, chiffres et soulignés, sans espace.')
        kind, direction = channel.iec_type, channel.direction
        name = f'IOMAP_{_io_type(kind, direction)}_{signal}'
        if name.casefold() in _variable_options(state):
            raise HTTPException(422, f'{name} existe déjà. Sélectionnez-la dans la liste ou choisissez un autre nom.')
        path = f'{hardware["application"]}.GVL_IOMAP.{name}'
        if any(other.variable.casefold() == path.casefold() for other in channels.values()):
            raise HTTPException(422, f'{name} est déjà référencée par un canal du rack. Corrigez cette liaison d’abord.')
        add_iomap_row(state, name, kind, direction)
        _set_mapping(channel, path)
        state[STATE_KEY] = {**hardware, 'xml': _xml_bytes(root).decode('utf-8')}
        return save(session, state, user)

    @app.get('/api/v2/io-hardware/export')
    def download(session=Depends(get_session), user=Depends(current_user)):
        state = project(session)
        return _attachment(rack_export(state), RACK_FILENAME)

    @app.get('/api/v2/io-hardware/mapping.csv')
    def download_mapping_csv(session=Depends(get_session), user=Depends(current_user)):
        return _attachment(csv_export(project(session)), MAPPING_CSV_FILENAME, 'text/csv; charset=utf-8')

    @app.post('/api/v2/io-hardware/exports/{kind}')
    def export_io(kind: Literal['gvl', 'gfc', 'bundle'], body: RackRevision, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        from backend import v2_plc, v2_plc_validation, v2_workspace

        state = project(session)
        check_revision(state, body.revision)
        mapping = csv_export(state) if kind == 'bundle' else None
        try:
            objects = v2_plc.build_program(state, include_union=True)
            names = (*GVL_OBJECTS, *GFC_OBJECTS)
            # Validate the copies together with their current declarations, including
            # DUTs that may not exist in the last imported program yet.
            checks = [*names, *(name for name, obj in objects.items() if obj['kind'] == 'DUT')]
            check_xml = v2_plc.serialize_program(objects, state, checks, include_all_folders=False)
            result = v2_plc_validation.validate_xml(check_xml, {**state, 'rtu_channels': []}, objects)
            if result['errors']:
                raise ValueError('\n'.join(result['errors']))
            gvl = v2_plc.serialize_program(objects, state, list(GVL_OBJECTS), include_all_folders=False)
            gfc = v2_plc.serialize_program(objects, state, list(GFC_OBJECTS), include_all_folders=False)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, f'Export E/S impossible : {exc}') from exc
        filename = {'gvl': GVL_FILENAME, 'gfc': GFC_FILENAME, 'bundle': 'Gaia-exports-ES.zip'}[kind]
        try:
            saved = v2_workspace.record_event(session, state, user.username, 'program_export', filename,
                                              v2_workspace.digest([gvl.decode('utf-8'), gfc.decode('utf-8')]), digest_key='program_export:io')
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        if kind != 'bundle':
            return _attachment(gvl if kind == 'gvl' else gfc, filename, revision=saved['revision'])
        output = BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in ((GVL_FILENAME, gvl), (GFC_FILENAME, gfc), (MAPPING_CSV_FILENAME, mapping)):
                archive.writestr(name, data)
        return _attachment(output.getvalue(), filename, 'application/zip', revision=saved['revision'])
