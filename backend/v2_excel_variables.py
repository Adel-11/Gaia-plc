"""Copyable variable catalogues for Excel mappings; no variable dropdowns."""
VARIABLE_HEADERS = {'member', 'source', 'destination', 'gvl_com variable', 'gvl_io member',
                    'gvl_iomap variable', 'gvl_modbus variable', 'dut_com member', 'parameter a value', 'parameter b value'}


def remove_variable_choices(workbook):
    for sheet in workbook:
        headers = {cell.column: str(cell.value or '').strip().lower() for cell in sheet[1]}
        for validation in list(sheet.data_validations.dataValidation):
            if validation.type != 'list':
                continue
            kept = [str(area) for area in validation.sqref.ranges
                    if not any(headers.get(column) in VARIABLE_HEADERS for column in range(area.min_col, area.max_col + 1))]
            if kept:
                validation.sqref = ' '.join(kept)
            else:
                sheet.data_validations.dataValidation.remove(validation)


def project_variables(workbook, state, filename=''):
    from backend.v2_fb_flow import variable_options
    from backend.v2_type_excel import _sheet
    remove_variable_choices(workbook)
    if 'VARIABLES' in workbook.sheetnames:
        del workbook['VARIABLES']
    roles = {'COM', 'HMI'} if 'MODBUS' in filename else {'IO'} if 'IO_PHYSICAL' in filename else {'EXC'} if 'EXCHANGES' in filename else {'PA', 'COM', 'HMI', 'EXC', 'IO', 'INTERN'}
    rows, seen = [], set()
    types = {str(key): typ for typ in state.get('types', []) for key in (typ.get('id'), typ.get('name')) if key}
    selected_type = next((typ for typ in state.get('types', []) if typ.get('source_file') == filename or filename == f"TYPE_{typ.get('name')}.xlsx"), None) if filename.startswith('TYPE_') else None
    instances = state.get('instances', [])
    if selected_type:
        instances = [row for row in instances if str(row.get('type_id')) in {str(selected_type.get('id')), str(selected_type.get('name'))}]
        if not instances:
            instances = [{'name': '<INSTANCE>', 'type_id': selected_type['id']}]
    for instance in instances:
        name = str(instance.get('name') or instance.get('id'))
        typ = types.get(str(instance.get('type_id')), {})
        for variable in variable_options(typ, state):
            role, _, leaf = variable['path'].partition('.')
            if role not in roles or leaf == 'SPARE':
                continue
            if str(variable['iec_type']).upper().startswith(('ARRAY', 'DUT_', 'FB_')):
                continue
            path = f'GVL_{role}.{role}_{name}.{leaf}' if role != 'INTERN' else variable['path']
            if path in seen:
                continue
            seen.add(path)
            rows.append({'path': path, 'member': leaf, 'relative': variable['path'], 'iec_type': variable['iec_type'],
                         'role': role, 'instance': name, 'type': typ.get('name', '')})
    if 'IO_PHYSICAL' in filename:
        for collection in ('io_channels', 'virtual_io'):
            for row in state.get(collection, []):
                path = row.get('GVL_IOMAP variable') or row.get('variable')
                if path and path not in seen:
                    seen.add(path)
                    rows.append({'path': path, 'member': str(path).split('.')[-1], 'role': 'IOMAP',
                                 'iec_type': row.get('IOMAP type') or row.get('iec_type') or row.get('Type'),
                                 'instance': row.get('Instance') or ''})
    _sheet(workbook, 'VARIABLES', [
        ('path', 'Path', 'Copy this qualified path to a project mapping variable column.', None),
        ('iec_type', 'IEC type', 'Type of the variable. Source and destination must be compatible.', None),
        ('role', 'Domain', 'COM = field equipment; HMI = supervision; IO = physical signals.', None),
        ('instance', 'Instance', 'Owner of the variable.', None),
        ('type', 'Equipment type', 'Shared type defining this variable.', None),
        ('relative', 'FB path', 'Relative name used inside the FB.', None),
        ('member', 'Member', 'Copy this member name to a type-level mapping.', None),
    ], rows)
    return workbook
