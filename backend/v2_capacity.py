"""Read Modbus address capacity from the current CODESYS reference."""
from backend.v2_plc_validation import _snapshot_nodes


def tcp_capacity(state):
    from backend.v2_plc import _source, _source_objects
    node=_snapshot_nodes(state).get('GVL_MODBUS')
    if node is None:
        node=_source_objects(_source()).get('GVL_MODBUS')
    result={}
    names={'Coils_TCP_HMI':('coils',1,8), 'Discret_input_TCP_HMI':('discrete_inputs',10001,8),
           'Input_register_TCP_HMI':('input_registers',1,1), 'Holding_register_TCP_HMI':('holding_registers',1,1)}
    if node is not None:
        for var in node:
            if var.get('name') not in names: continue
            table,base,width=names[var.get('name')]
            dims=var.xpath('./*[local-name()="type"]/*[local-name()="array"]/*[local-name()="dimension"]')
            if len(dims)!=1: continue
            try: lo=int(dims[0].get('lower'));hi=int(dims[0].get('upper'))
            except (ValueError,TypeError): continue
            result[table]=[base+lo*width,base+(hi+1)*width-1]
    return result
