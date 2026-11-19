from datetime import datetime
from io import BytesIO

import openpyxl
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo

from backend.v2_excel_plain import plain_xlsx


def test_plain_workbook_keeps_values_formulas_and_number_formats_without_body_colours():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Value', 'Date', 'Flag', 'Formula'])
    ws.append([0, datetime(2026, 11, 19), False, '=A2+1'])
    ws['A2'].number_format = '0.000'
    ws['A2'].font = Font(color='FF0000', bold=True)
    ws['A2'].fill = PatternFill('solid', fgColor='FF0000')
    ws['B2'].number_format = 'yyyy-mm-dd'
    ws.conditional_formatting.add('A2', CellIsRule(operator='greaterThan', formula=['-1'], fill=PatternFill('solid', fgColor='00FF00')))
    table = Table(displayName='Data', ref='A1:D2')
    table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium9', showRowStripes=True)
    ws.add_table(table)
    stream = BytesIO()
    wb.save(stream)
    data = plain_xlsx(stream.getvalue())
    actual = openpyxl.load_workbook(BytesIO(data)).active
    assert list(actual.values) == list(ws.values)
    assert actual['A2'].number_format == '0.000'
    assert actual['B2'].number_format == 'yyyy-mm-dd'
    assert not actual.conditional_formatting
    assert actual.tables['Data'].tableStyleInfo.name == ''
    assert not actual.tables['Data'].tableStyleInfo.showRowStripes
    assert all(cell.fill.patternType == 'solid' for cell in actual[1])
    assert all(cell.fill.patternType is None and not cell.font.bold and cell.font.color.rgb == 'FF000000' for cell in actual[2])


def test_all_project_exports_have_copyable_variables_and_roundtrip_their_data():
    from backend.v2_engine import export_workbook, load_pack, workbook_to_data
    state = load_pack()
    for filename in state['workbooks']:
        data = export_workbook(state, filename)
        book = openpyxl.load_workbook(BytesIO(data))
        assert 'VARIABLES' in book.sheetnames
        for sheet in book:
            assert not sheet.conditional_formatting
            assert all(cell.fill.patternType is None for row in sheet.iter_rows(min_row=2) for cell in row)
        parsed = workbook_to_data(data, filename)
        # The reference sheet is ignored when importing, so it cannot create PLC rows.
        assert all(sheet['name'] != 'VARIABLES' for sheet in parsed['sheets'])


def test_modbus_templates_have_domain_specific_copyable_members_without_variable_lists():
    from backend.v2_engine import load_pack
    from backend.v2_type_excel import Draft, export_draft
    state = load_pack()
    draft = Draft(name='Sensor', scope='modbus', signals=[
        {'id': 'pressure', 'name': 'PRESSURE', 'transport': 'tcp', 'kind': 'analog', 'direction': 'input', 'iec_type': 'REAL'}], duts=[
        {'role': 'HMI', 'members': [{'name': 'OUTPUT_PRESSURE', 'iec_type': 'REAL'}]}])
    book = openpyxl.load_workbook(BytesIO(export_draft(state, draft)))
    rows = list(book['VARIABLES'].values)
    assert ('TCP_INPUT_PRESSURE.Real_value', 'COM', 'REAL', 'COM.TCP_INPUT_PRESSURE.Real_value') in rows
    assert ('OUTPUT_PRESSURE', 'HMI', 'REAL', 'HMI.OUTPUT_PRESSURE') in rows
    for name in ('MODBUS_TCP', 'MODBUS_RTU'):
        sheet = book[name]
        column = next(cell.column for cell in sheet[1] if cell.value == 'Member')
        assert all(not any(area.min_col <= column <= area.max_col for area in rule.sqref.ranges) for rule in sheet.data_validations.dataValidation)
        assert sheet.data_validations.dataValidation  # Protocol/type choices remain available.


def test_vendor_workbook_without_styles_keeps_native_data_and_gets_only_one_header():
    from zipfile import ZipFile
    from backend.v2_panelview_export import TEMPLATE
    original = openpyxl.load_workbook(TEMPLATE)
    data = plain_xlsx(TEMPLATE.read_bytes())
    actual = openpyxl.load_workbook(BytesIO(data))
    assert original.sheetnames == actual.sheetnames
    assert list(original.active.values) == list(actual.active.values)
    assert actual.active['A1'].fill.patternType == 'solid'
    assert actual.active['A2'].fill.patternType is None
    with ZipFile(BytesIO(data)) as archive:
        assert 'xl/styles.xml' in archive.namelist()
