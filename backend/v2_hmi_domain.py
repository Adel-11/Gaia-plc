"""Separate field communication from supervision while retaining the Modbus address pool."""
import copy
import re


def domain(row):
    path = str(row.get('GVL_COM variable') or row.get('member_path') or '')
    return 'HMI' if path.upper().startswith(('GVL_HMI.', 'HMI.')) or row.get('role') == 'HMI' else 'COM'


def _rewrite(text, names=None, instances=None):
    from backend.v2_member_sync import TOKEN, COMMENT_EDGE, DOT
    if not isinstance(text, str):
        return text
    names = {name.casefold() for name in names or []}
    instances = instances or {}
    out, end = [], 0
    while match := TOKEN.search(text, end):
        out.append(text[end:match.start()])
        token, end = match.group(), match.end()
        if token == '(*':
            start, depth = match.start(), 1
            while depth and (edge := COMMENT_EDGE.search(text, end)):
                depth += 1 if edge.group() == '(*' else -1
                end = edge.end()
            if depth:
                end = len(text)
            token = text[start:end]
        elif token[0].isalpha() or token[0] == '_':
            parts = DOT.split(token)
            if len(parts) > 1 and parts[0].upper() == 'COM' and parts[1].casefold() in names:
                parts[0] = 'HMI'
                token = '.'.join(parts)
            for index in range(len(parts) - 2):
                alias = parts[index + 1]
                if parts[index].upper() == 'GVL_COM' and alias.upper().startswith('COM_'):
                    members = instances.get(alias[4:].casefold(), set())
                    if parts[index + 2].casefold() in members:
                        parts[index:index + 2] = ['GVL_HMI', 'HMI_' + alias[4:]]
                        token = '.'.join(parts)
                        break
        out.append(token)
    out.append(text[end:])
    return ''.join(out)


def _walk(value, names=None, instances=None):
    if isinstance(value, str):
        return _rewrite(value, names, instances)
    if isinstance(value, list):
        return [_walk(item, names, instances) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, names, instances) for key, item in value.items()}
    return value


def supervision_members(state):
    clients = {str(row.get('Client') or row.get('id')).casefold() for row in state.get('tcp_clients', [])
               if re.search(r'hmi|scada|fuxa|webvisu|panelview|supervision', str(row.get('Client') or row.get('id')) + ' ' + str(row.get('Description') or ''), re.I)}
    instances = {str(key): instance for instance in state.get('instances', []) for key in (instance.get('id'), instance.get('name')) if key}
    result = {}
    for point in state.get('tcp_points', []):
        if domain(point) != 'HMI' and (state.get('separate_hmi') or str(point.get('Client') or point.get('client_id')).casefold() not in clients):
            continue
        instance = instances.get(str(point.get('Instance') or point.get('instance_id') or point.get('instance')))
        path = str(point.get('GVL_COM variable') or '').split('.', 2)
        if instance and len(path) == 3:
            result.setdefault(str(instance['type_id']), set()).add(path[2].split('.')[0].split('[')[0].casefold())
    return result


def migrate_state(state, supervised=None):
    """Idempotent conversion of a detached state. The caller owns backup and persistence."""
    moved, instance_members = {}, {}
    supervised = supervision_members(state) if supervised is None else supervised
    for typ in state.get('types', []):
        com = next((dut for dut in typ.get('duts', []) if dut.get('role') == 'COM'), None)
        hmi = next((dut for dut in typ.get('duts', []) if dut.get('role') == 'HMI'), None)
        if hmi is None:
            hmi = {'id': f"{typ['id']}:DUT_HMI", 'role': 'HMI', 'name': f"DUT_HMI_{typ['name']}", 'members': []}
            typ['duts'].insert(typ['duts'].index(com) + 1 if com else 0, hmi)
        existing = {member['name'].casefold(): member for member in hmi['members'] if member['name'] != 'SPARE'}
        for member in (com or {}).get('members', []):
            if member['name'].upper().startswith(('HMI_', 'SCADA_', 'WEBVISU_')) or member['name'].casefold() in supervised.get(str(typ['id']), set()) | supervised.get(str(typ['name']), set()):
                key = member['name'].casefold()
                if key in existing and existing[key].get('iec_type') != member.get('iec_type'):
                    raise ValueError(f"Conflicting HMI member: {typ['name']}.{member['name']}")
                existing.setdefault(key, copy.deepcopy(member))
        names = set(existing)
        moved[typ['id']] = moved[typ['name']] = names
        hmi['members'] = list(existing.values()) or [{'name': 'SPARE', 'iec_type': 'BOOL'}]
        if com:
            com['members'] = [member for member in com['members'] if member['name'].casefold() not in names] or [{'name': 'SPARE', 'iec_type': 'BOOL'}]
        typ['fb'] = _walk(typ.get('fb', {}), names)
        for row in typ.get('modbus_template', []):
            if row.get('member', '').split('.')[0].split('[')[0].casefold() in names:
                row['role'] = 'HMI'
        for collection in ('TEMPLATE_TCP', 'TEMPLATE_RTU'):
            for row in typ.get('templates', {}).get(collection, []):
                path = str(row.get('GVL_COM variable') or '')
                parts = path.split('.', 2)
                if len(parts) == 3 and parts[0] == 'GVL_COM' and parts[2].split('.')[0].split('[')[0].casefold() in names:
                    row['GVL_COM variable'] = 'GVL_HMI.HMI_' + parts[1].removeprefix('COM_') + '.' + parts[2]
                    row['role'] = 'HMI'
        for instance in state.get('instances', []):
            if instance.get('type_id') in {typ['id'], typ['name']}:
                instance_members[str(instance.get('name') or instance['id']).casefold()] = names
    for key in ('tcp_points', 'rtu_channels', 'gfc_copy_overrides', 'functions', 'exchange_functions',
                'generated_custom_code', 'generated_local_variables', 'workbooks', 'special_exc_assignments'):
        if key in state:
            state[key] = _walk(state[key], instances=instance_members)
    for row in state.get('tcp_points', []) + state.get('rtu_channels', []):
        row['role'] = domain(row)
        if row['role'] == 'HMI':
            name = str(row.get('Instance') or row.get('instance_id') or row.get('instance') or '').casefold()
            for key in ('Member', 'member_path', 'member'):
                if row.get(key):
                    row[key] = _rewrite(row[key], instance_members.get(name))
    for template in state.get('generic_objects', []):
        names = {member['name'] for member in template.get('members', []) if member.get('role') == 'COM' and member['name'].upper().startswith(('HMI_', 'SCADA_'))}
        for member in template.get('members', []):
            if member['name'] in names:
                member['role'] = 'HMI'
        template['processing'] = _rewrite(template.get('processing', ''), names)
    # Manual FB source is migrated per type; global declarations need structural changes.
    for typ in state.get('types', []):
        name = typ.get('fb', {}).get('name')
        override = state.get('st_overrides', {}).get(name)
        if override:
            state['st_overrides'][name] = _walk(override, moved[typ['id']], instance_members)
    for name, override in state.get('st_overrides', {}).items():
        if name not in {typ.get('fb', {}).get('name') for typ in state.get('types', [])}:
            for field in ('body',):
                if field in override:
                    override[field] = _rewrite(override[field], instances=instance_members)
    state['separate_hmi'] = True
    from backend.v2_type_creation import _book_for_type
    from backend import v2_engine
    for typ in state.get('types', []):
        filename = typ.get('source_file') or f"TYPE_{typ['name']}.xlsx"
        state.setdefault('workbooks', {})[filename] = _book_for_type(v2_engine, typ, filename)
    return state


def migrate_project(session, project_id, expected_revision, backup_dir=None):
    """Validate an online SQLite backup, then save one reviewed migration revision."""
    import sqlite3
    from datetime import datetime, timezone
    from pathlib import Path
    from backend import db, v2_store as store
    from backend.v2_workspace import _compatibility_errors

    session.info['workspace_id'] = project_id
    current = store.load_project(session)
    if current is None or current['revision'] != expected_revision:
        raise ValueError('Project changed; inspect its current revision before migrating.')
    if current.get('separate_hmi'):
        return {'already_migrated': True, 'revision': current['revision']}
    url = session.get_bind().engine.url
    if url.get_backend_name() != 'sqlite':
        raise ValueError('A verified database backup is required for this database backend.')
    folder = Path(backup_dir) if backup_dir is not None else Path('/data/backups')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = folder / ('before-hmi-domain-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite')
    with sqlite3.connect('file:' + url.database + '?mode=ro', uri=True) as source, sqlite3.connect(backup) as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup integrity check failed.')
    backup.chmod(0o600)
    state = copy.deepcopy(current)
    supervised = supervision_members(current)
    # A shared catalogue needs the union of supervision members used by all its projects.
    from sqlalchemy import select
    from backend.v2_workspace import catalog_id
    for project in session.scalars(select(store.V2Project)).all():
        session.info['workspace_id'] = project.id
        other = store.load_project(session)
        if other and catalog_id(other) == catalog_id(current):
            for key, names in supervision_members(other).items():
                supervised.setdefault(key, set()).update(names)
    session.info['workspace_id'] = project_id
    migrate_state(state, supervised)
    problems = set(_compatibility_errors(state)) - set(_compatibility_errors(current))
    if problems:
        raise ValueError('Migration would introduce invalid PLC references: ' + '; '.join(sorted(problems)))
    for key in ('tcp_points', 'rtu_channels'):
        before = [(row.get('id'), row.get('table'), row.get('address'), row.get('GVL_MODBUS variable')) for row in current.get(key, [])]
        after = [(row.get('id'), row.get('table'), row.get('address'), row.get('GVL_MODBUS variable')) for row in state.get(key, [])]
        if before != after:
            raise ValueError('Migration must preserve all physical Modbus addresses.')
    session.info['version_event'] = {'kind': 'hmi_domain', 'comment': 'Séparation COM terrain / HMI supervision ; adresses conservées'}
    saved = store.save_project(session, state, username='system:hmi-domain', expected_revision=expected_revision)
    return {'backup': str(backup), 'revision': saved['revision'],
            'hmi_members': sum(len([m for m in d['members'] if m['name'] != 'SPARE']) for t in saved['types'] for d in t['duts'] if d['role'] == 'HMI')}


if __name__ == '__main__':
    import argparse
    import json
    from backend import db
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=int, required=True)
    parser.add_argument('--apply-revision', type=int, required=True)
    args = parser.parse_args()
    with db.make_session_factory(db.make_engine())() as session:
        result = migrate_project(session, args.project, args.apply_revision)
        session.commit()
        print(json.dumps(result))
