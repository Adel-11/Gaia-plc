"""One explicit communication point for one already-declared instance."""
import re
import secrets
from backend.v2_fb_flow import variable_options

WORD_TYPES = {'WORD', 'INT', 'UINT', 'BYTE', 'SINT', 'USINT'}
DOUBLE_WORD_TYPES = {'REAL', 'DWORD', 'DINT', 'UDINT', 'TIME', 'T', 'DATE', 'DT', 'DATE_AND_TIME', 'TOD', 'TIME_OF_DAY'}
WORD_ORDERS = {'MSW_FIRST': ('MSW', 'LSW'), 'LSW_FIRST': ('LSW', 'MSW')}


def pair_key(row):
    """Identify scalar pairs without grouping legacy mapped union/array words."""
    path = str(row.get('GVL_COM variable') or '')
    if (str(row.get('Type') or row.get('data_type')).upper() not in DOUBLE_WORD_TYPES
            or str(row.get('Word') or '').upper() not in {'MSW', 'LSW'}
            or not path or re.search(r'\.Word_value\[', path, re.I)):
        return None
    if not (row.get('Client') or row.get('client_id')) and not row.get('manual_scalar'):
        return None
    return tuple(str(value or '').casefold() for value in (
        row.get('Instance') or row.get('instance_id'), row.get('Client') or row.get('client_id'),
        row.get('table') or row.get('Table'), row.get('Direction') or row.get('direction'), path))


def editable_pair_key(row):
    if key := pair_key(row):
        return key
    path = str(row.get('GVL_COM variable') or '')
    if ((client := row.get('Client') or row.get('client_id'))
            and str(row.get('Word') or '').upper() in {'MSW', 'LSW'}
            and re.search(r'\.Word_value\[\d+\]$', path, re.I)):
        return tuple(str(value or '').casefold() for value in (
            row.get('Instance') or row.get('instance_id'), client, row.get('table') or row.get('Table'),
            row.get('Direction') or row.get('direction'), re.sub(r'\[\d+\]$', '', path), row.get('Name')))
    return None


def pair_rows(rows, row):
    key = editable_pair_key(row)
    return [other for other in rows if editable_pair_key(other) == key] if key else []


def _check_pair_slots(state, collection, row, start, excluded=()):
    table = row['table']
    tcp = collection == 'tcp_points'
    low, high = 0, 65535
    if tcp:
        from backend.v2_addressing import client_ranges
        from backend.v2_capacity import tcp_capacity
        low, high = client_ranges(state, row['Client'])[table]
        capacity = tcp_capacity(state).get(table)
        if capacity:
            low, high = max(low, capacity[0]), min(high, capacity[1])
    if start < low or start + 1 > high:
        raise ValueError(f'Les deux registres doivent se trouver dans la plage {low}–{high}.')
    occupied = set()
    for other in state.get(collection, []):
        if any(other is old for old in excluded):
            continue
        if str(other.get('table') or other.get('Table') or other.get('sheet')).casefold() != table.casefold():
            continue
        if not tcp and str(other.get('Instance') or other.get('instance_id')) != row['Instance']:
            continue
        field = 'Modbus address' if tcp else 'Equipment address'
        raw = other.get(field, other.get('address'))
        try:
            occupied.add(int(str(raw), 16) if str(raw).lower().startswith('0x') else int(raw))
        except (TypeError, ValueError):
            continue
    if occupied.intersection((start, start + 1)):
        raise ValueError('Les deux registres doivent être libres et consécutifs.')


def _assign_word(row, collection, address, word):
    row.update(Word=word, word=word, address=address)
    if collection == 'tcp_points':
        from backend.v2_addressing import client_address_for
        from backend.v2_instance_editor import _tcp_variable
        row.update({'Modbus address': address, 'Client address': client_address_for(row['table'], address, row['Client']),
                    'GVL_MODBUS variable': _tcp_variable(row['table'], address)})
        row['client_address'] = row['Client address']
        path = row['GVL_COM variable']
        row['GVL_COM variable'] = re.sub(r'(\.Word_value)\[\d+\]$', rf'\g<1>[{0 if word == "LSW" else 1}]', path, flags=re.I)
    else:
        row['Equipment address'] = address


def update_pair(state, collection, row, group, values):
    from backend.v2_configuration import _num
    if len(group) != 2:
        raise ValueError('Cette liaison est incomplète. Retire-la, puis recrée les deux mots.')
    by_address = sorted(group, key=lambda item: int(item['address']))
    old_start = int(by_address[0]['address'])
    address_field = 'Modbus address' if collection == 'tcp_points' else 'Equipment address'
    permitted = {address_field, 'modbus_address' if collection == 'tcp_points' else 'address', 'Register word order'}
    if set(values) - permitted:
        raise ValueError('Modifie la paire depuis le mapping graphique.')
    address = values.get(address_field, values.get('modbus_address' if collection == 'tcp_points' else 'address'))
    start = old_start if address is None else _num(address, address_field, True) - (int(row['address']) - old_start)
    order = values.get('Register word order')
    words = WORD_ORDERS.get(order) if order else tuple(str(item['Word']).upper() for item in by_address)
    if not words:
        raise ValueError('Choisis l’ordre des deux mots.')
    _check_pair_slots(state, collection, row, start, group)
    for offset, (item, word) in enumerate(zip(by_address, words)):
        _assign_word(item, collection, start + offset, word)
        if order and item.get('FUXA type') in {'Float32', 'Float32MLE', 'Int32', 'Int32MLE', 'UInt32', 'UInt32MLE'}:
            fuxa_type = item['FUXA type'].removesuffix('MLE')
            item['FUXA type'] = fuxa_type + ('MLE' if words[0] == 'LSW' else '')


def members_for(state, instance):
    typ = next((t for t in state.get('types', []) if str(instance.get('type_id')) in {str(t.get('id')), str(t.get('name'))}), None)
    return [m for m in variable_options(typ or {}, state)
            if m['path'].startswith('COM.') and m['iec_type'].upper() in WORD_TYPES | DOUBLE_WORD_TYPES | {'BOOL'}]


def create_point(state, collection, values):
    instance = next((i for i in state.get('instances', []) if str(values.get('Instance')) in {str(i.get('id')), str(i.get('name'))}), None)
    if not instance:
        raise ValueError('Declare an instance before configuring a communication point.')
    name = str(instance.get('name') or instance['id'])
    member = str(values.get('Member') or '').removeprefix('COM.')
    selected = next((m for m in members_for(state, instance) if m['path'] == 'COM.' + member), None)
    if not selected:
        raise ValueError('Choisis une variable COM compatible de cette instance.')
    dtype = selected['iec_type'].upper()
    wide = dtype in DOUBLE_WORD_TYPES
    order = values.get('Register word order') or 'MSW_FIRST'
    if wide and order not in WORD_ORDERS:
        raise ValueError('Choisis l’ordre des deux mots.')
    address_field = 'Modbus address' if collection == 'tcp_points' else 'Equipment address'
    from backend.v2_configuration import _num
    address = _num(values.get(address_field), 'Address', True)
    if not 0 <= address <= 65535:
        raise ValueError('Address must be between 0 and 65535.')
    table = str(values.get('Table') or '')
    bit = table in {'coils', 'discrete_inputs', 'Coil', 'Discrete input'}
    if (dtype == 'BOOL') != bit:
        raise ValueError('BOOL members need a bit table; integer members need a register table.')
    row = {'id': 'MANUAL_' + secrets.token_hex(8), 'Instance': name, 'instance_id': name, 'instance': name,
           'Name': f'{name}.{member}', 'Member': member, 'member_name': re.split(r'[.\[]', member)[0], 'member_path': member, 'Type': dtype, 'data_type': dtype,
           'GVL_COM variable': f'GVL_COM.COM_{name}.{member}', 'manual_scalar': True}
    if collection == 'tcp_points':
        if table not in {'coils', 'discrete_inputs', 'input_registers', 'holding_registers'}:
            raise ValueError('Choose a TCP table.')
        client = str(values.get('Client') or '')
        from backend.v2_addressing import client_ranges, client_address_for
        low, high = client_ranges(state, client)[table]
        if not low <= address <= high:
            raise ValueError(f'Choose an address between {low} and {high} for this client/table.')
        direction = str(values.get('Direction') or '')
        if direction not in {'Client to PLC', 'PLC to client'}:
            raise ValueError('Choose the communication direction.')
        if table in {'discrete_inputs', 'input_registers'} and direction != 'PLC to client':
            raise ValueError('Input tables are read-only from the client: choose PLC to client.')
        from backend.v2_instance_editor import _tcp_variable
        row.update(Client=client, client_id=client, table=table, sheet=table.upper(), address=address,
                   Direction=direction, direction=direction, **{'Modbus address': address,
                   'Client address': client_address_for(table, address, client), 'GVL_MODBUS variable': _tcp_variable(table, address)})
    else:
        if table not in {'Coil', 'Discrete input', 'IR', 'HR'}:
            raise ValueError('Choose an RTU table.')
        slave = next((r for r in state.get('rtu_slaves', []) if str(r.get('Instance') or r.get('instance_id')) == name), None)
        if not slave:
            raise ValueError('Add this instance as an RTU slave first.')
        raw = str(values.get('GVL_MODBUS variable') or '')
        if not re.fullmatch(r'GVL_MODBUS\.[A-Za-z_][A-Za-z0-9_]*', raw):
            raise ValueError('Enter one scalar mapped name, for example GVL_MODBUS.RTU_PUMP_SPEED.')
        from backend import v2_plc
        gvl = v2_plc.build_program(state).get('GVL_MODBUS', {}).get('element')
        names = {raw.split('.')[1].casefold() + suffix for suffix in (('_1', '_2') if wide else ('',))}
        if gvl is not None and any(v.get('name', '').casefold() in names for v in gvl.findall(v2_plc.NS + 'variable')):
            raise ValueError('This mapped name already exists. Choose a unique name for this point.')
        direction = str(values.get('Direction') or '')
        if direction not in {'Equipment to PLC', 'PLC to equipment'}:
            raise ValueError('Choose the communication direction.')
        if table in {'Discrete input', 'IR'} and direction != 'Equipment to PLC':
            raise ValueError('Input tables are read-only: choose Equipment to PLC.')
        row.update(table=table, sheet=name, address=address, Direction=direction, direction=direction,
                   Line=slave.get('Line'), **{'Table': table, 'Equipment address': address,
                   'GVL_MODBUS variable': raw, 'Channel offset': 0})
    rows = state.setdefault(collection, [])
    if wide:
        _check_pair_slots(state, collection, row, address)
        pair = []
        for offset, word in enumerate(WORD_ORDERS[order]):
            item = {**row, 'id': f"{row['id']}_{offset + 1}"}
            if collection == 'rtu_channels':
                item['GVL_MODBUS variable'] = f'{raw}_{offset + 1}'
            _assign_word(item, collection, address + offset, word)
            pair.append(item)
        if pair_rows(rows, pair[0]):
            raise ValueError('Cette variable COM possède déjà une paire dans cette table et ce sens.')
        rows.extend(pair)
    else:
        rows.append(row)
    if collection == 'rtu_channels':
        book = state.get('workbooks', {}).get('MODBUS_RTU.xlsx')
        if book and not any(s.get('name') == name for s in book.get('sheets', [])):
            source = next((s for s in book.get('sheets', []) if s.get('name') not in {'README', 'LISTS', 'LINES', 'SLAVES'}), None)
            if source:
                book['sheets'].append({'name': name, 'headers': list(source.get('headers', [])), 'rows': []})
    return row
