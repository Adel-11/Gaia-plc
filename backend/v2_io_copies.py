"""Manage the generated copies between instance I/O and GVL_IOMAP."""
from __future__ import annotations

import re

from backend.v2_catalog import IEC_TYPES, _io_type
from backend.v2_plc import HNS, NS, build_program
from backend.v2_program_membership import eligible_instances, freeze_membership

TARGET_FIELDS = ('Instance', 'instance_id', 'GVL_IO member', 'member_name')
DIRECTIONS = {'DI': 'input', 'AI': 'input', 'DO': 'output', 'AO': 'output'}


def copy_block(direction):
    return 'GFC_IO_IN' if direction == 'input' else 'GFC_IO_OUT'


def copy_statement(iomap, target, direction):
    left, right = target, f'GVL_IOMAP.{iomap}'
    if direction == 'output':
        left, right = right, left
    return f'{left} := {right};'


def variable_rows(state):
    for collection in ('io_channels', 'virtual_io'):
        for row in state.get(collection, []):
            name = str(row.get('GVL_IOMAP variable') or row.get('variable') or '')
            match = re.fullmatch(r'(?:GVL_IOMAP\.)?([A-Za-z_]\w*)(?:\[.*\])?', name.strip(), re.I)
            if match:
                yield collection, row, match[1].casefold()


def variable_direction(row):
    return DIRECTIONS.get(str(row.get('I/O type') or '').upper())


def instance_options(state):
    eligible = set(eligible_instances(state)['GFC_IO_IN'])
    types = {str(key): typ for typ in state.get('types', []) for key in (typ.get('id'), typ.get('name')) if key}
    options = []
    for row in state.get('instances', []):
        name = str(row.get('name') or row.get('id'))
        if name not in eligible:
            continue
        typ = types.get(str(row.get('type_id')), {})
        dut = next((dut for dut in typ.get('duts', []) if dut.get('role') == 'IO'), {})
        members = []
        for member in dut.get('members', []):
            leaf = str(member.get('name') or '')
            kind = str(member.get('iec_type') or '').upper().strip()
            if not re.fullmatch(r'[A-Za-z_]\w*', leaf) or kind not in IEC_TYPES:
                continue
            prefix = leaf.split('_', 1)[0].upper()
            direction = {'INPUT': 'input', 'OUTPUT': 'output'}.get(prefix)
            members.append({'name': leaf, 'path': f'GVL_IO.IO_{name}.{leaf}', 'iec_type': kind, 'direction': direction})
        if members:
            options.append({'name': name, 'members': members})
    return options


def copy_options(state, variables):
    overrides = state.get('st_overrides') or {}
    manual = [name for name in ('GFC_IO_IN', 'GFC_IO_OUT', 'PLC_PRG') if overrides.get(name, {}).get('body') is not None]
    stored = state.get('program_membership') or {}
    eligible = eligible_instances(state)
    copies = []
    for _, row, key in variable_rows(state):
        target, direction = row.get('GVL_IO member'), variable_direction(row)
        if key not in variables or not target or not direction:
            continue
        instance = str(row.get('Instance') or row.get('instance_id') or '')
        block = copy_block(direction)
        copies.append({'iomap': variables[key]['name'], 'instance': instance, 'target': target, 'block': block,
                       'direction': direction, 'st': copy_statement(variables[key]['name'], target, direction),
                       'generated': block not in manual and (instance in stored.get(block, eligible[block])
                                     if instance else not stored)})
    return {'variables': list(variables.values()), 'instances': instance_options(state), 'copies': copies, 'manual_blocks': manual}


def assign_copy(state, variable, instance_name, member_name, direction):
    instance = next((item for item in instance_options(state) if item['name'] == instance_name), None)
    member = next((item for item in (instance or {}).get('members', []) if item['name'] == member_name), None)
    if member is None:
        raise ValueError('Choisissez un membre DUT_IO de cette instance.')
    if variable['iec_type'] != member['iec_type']:
        raise ValueError('La variable GVL_IOMAP et le membre GVL_IO doivent avoir le même type IEC.')
    if any(item not in {None, direction} for item in (variable['direction'], member['direction'])):
        raise ValueError('Le membre GVL_IO et la variable GVL_IOMAP doivent avoir le même sens.')
    block = copy_block(direction)
    overrides = state.get('st_overrides') or {}
    if overrides.get(block, {}).get('body') is not None:
        raise ValueError(f'{block} utilise un corps ST manuel. Pour générer la recopie, revenez au corps généré ou ajoutez-la dans le ST manuel.')
    manual_io = overrides.get('GVL_IO', {}).get('declaration')
    if manual_io is not None:
        from backend.v2_plc_validation import _plain_variables
        if f'io_{instance_name}'.casefold() not in {name.casefold() for name in _plain_variables(manual_io)}:
            raise ValueError(f'IO_{instance_name} est absent de la déclaration ST manuelle de GVL_IO.')

    name = variable['name']
    rows = list(variable_rows(state))
    matches = [row for _, row, key in rows if key == name.casefold()]
    active = [row for row in matches if row.get('GVL_IO member')]
    if len(active) > 1 or any(variable_direction(row) not in {None, direction} for row in matches):
        raise ValueError(f'{name} est déjà utilisé dans plusieurs lignes E/S. Retirez ses recopies avant de le réassigner.')
    target = member['path']
    if any(key != name.casefold() and str(row.get('GVL_IO member') or '').casefold() == target.casefold() for _, row, key in rows):
        raise ValueError(f'{target} est déjà relié à une autre variable IOMAP. Retirez cette recopie d’abord.')
    path = f'GVL_IOMAP.{name}'
    kind = variable['iec_type']
    if matches:
        row = (active or matches)[0]
        if '[' in str(row.get('GVL_IOMAP variable') or ''):
            raise ValueError('Ce mappage indexé doit être modifié dans son éditeur d’origine.')
    else:
        ident = f'VIO_MAP_{name.upper()}'
        row = {'id': ident, 'name': name, 'iec_type': kind, 'Type': kind, 'Module': 'GVL_IO',
               'Slot': 'VIRTUAL', 'Channel': ident, 'Status': 'VIRTUAL'}
        state.setdefault('virtual_io', []).append(row)
    row.update({'Instance': instance_name, 'instance_id': instance_name, 'member_name': member['name'],
                'GVL_IO member': target, 'GVL_IOMAP variable': path, 'variable': path,
                'I/O type': row.get('I/O type') if variable_direction(row) else _io_type(kind, direction), 'IOMAP type': kind})
    if row.get('Slot') == 'VIRTUAL':
        row['direction'] = direction
    membership = freeze_membership(state)[block]
    if instance_name not in membership:
        membership.append(instance_name)


def remove_copy(state, name, *, delete=False):
    key = name.casefold()
    for collection, row, row_key in list(variable_rows(state)):
        if row_key != key:
            continue
        if delete and collection == 'virtual_io':
            state[collection].remove(row)
            continue
        for field in (*TARGET_FIELDS, *(('GVL_IOMAP variable', 'variable') if delete else ())):
            row[field] = ''
        if delete:
            row['Status'] = 'SPARE'
    if not delete:
        return
    removed = state.setdefault('removed_iomap_variables', [])
    if key not in {old.casefold() for old in removed}:
        removed.append(name)


def authored_references(state, name):
    """Check the actual export after the generated declaration and copies are gone."""
    from backend.v2_member_sync import rewrite_st
    from backend.v2_codesys import _st
    from backend.v2_st_overrides import plain_declaration

    delta = {name.casefold(): None}
    roots = {'gvl_iomap': delta, 'application.gvl_iomap': delta, '': delta}
    references = []
    for label, obj in build_program(state, include_union=True).items():
        element = obj['element']
        sources = [plain_declaration(element), *element.xpath('.//p:ST/h:xhtml/text()', namespaces={'p': NS[1:-1], 'h': HNS[1:-1]})]
        if obj['kind'] in {'GVL', 'DUT'} and sources[0] is None:
            sources[0] = _st(element)
        if obj['kind'] == 'POU':
            sources.extend(element.xpath('.//p:initialValue//p:simpleValue/@value', namespaces={'p': NS[1:-1]}))
        found = set()
        for code in sources:
            if code:
                rewrite_st(code, roots, found)
        if found:
            references.append(label)
    return references
