"""One explicit communication point for one already-declared instance."""
import re
import secrets
from backend.v2_fb_flow import variable_options

WORD_TYPES = {'WORD', 'INT', 'UINT', 'BYTE', 'SINT', 'USINT'}


def members_for(state, instance):
    typ = next((t for t in state.get('types', []) if str(instance.get('type_id')) in {str(t.get('id')), str(t.get('name'))}), None)
    return [m for m in variable_options(typ or {}, state) if m['path'].startswith('COM.') and m['iec_type'].upper() in WORD_TYPES | {'BOOL'}]


def create_point(state, collection, values):
    instance = next((i for i in state.get('instances', []) if str(values.get('Instance')) in {str(i.get('id')), str(i.get('name'))}), None)
    if not instance:
        raise ValueError('Declare an instance before configuring a communication point.')
    name = str(instance.get('name') or instance['id'])
    member = str(values.get('Member') or '').removeprefix('COM.')
    selected = next((m for m in members_for(state, instance) if m['path'] == 'COM.' + member), None)
    if not selected:
        raise ValueError('Choose a BOOL or 16-bit COM member belonging to this instance.')
    dtype = selected['iec_type'].upper()
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
        if gvl is not None and any(v.get('name', '').casefold() == raw.split('.')[1].casefold() for v in gvl.findall(v2_plc.NS + 'variable')):
            raise ValueError('This mapped name already exists. Choose a unique name for this point.')
        direction = str(values.get('Direction') or '')
        if direction not in {'Equipment to PLC', 'PLC to equipment'}:
            raise ValueError('Choose the communication direction.')
        if table in {'Discrete input', 'IR'} and direction != 'Equipment to PLC':
            raise ValueError('Input tables are read-only: choose Equipment to PLC.')
        row.update(table=table, sheet=name, address=address, Direction=direction, direction=direction,
                   Line=slave.get('Line'), **{'Table': table, 'Equipment address': address,
                   'GVL_MODBUS variable': raw, 'Channel offset': 0})
    state.setdefault(collection, []).append(row)
    if collection == 'rtu_channels':
        book = state.get('workbooks', {}).get('MODBUS_RTU.xlsx')
        if book and not any(s.get('name') == name for s in book.get('sheets', [])):
            source = next((s for s in book.get('sheets', []) if s.get('name') not in {'README', 'LISTS', 'LINES', 'SLAVES'}), None)
            if source:
                book['sheets'].append({'name': name, 'headers': list(source.get('headers', [])), 'rows': []})
    return row
