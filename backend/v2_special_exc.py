"""Explicit per-instance EXC assignments from user-entered ST sources."""
from copy import deepcopy
import re
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

KEY = 'special_exc_assignments'
MANAGED_GVLS = {'GVL_COM', 'GVL_HMI', 'GVL_EXC', 'GVL_IO', 'GVL_PA', 'GVL_IOMAP', 'GVL_MODBUS'}


class Assignment(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=128)
    member: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=2000)
    comment: str = Field(default='', max_length=500)


def _instance(state, identifier):
    if identifier in (None, ''): return None
    return next((row for row in state.get('instances', []) if str(identifier) in {str(row.get('id')), str(row.get('name'))}), None)


def options(state, instance):
    from backend.v2_fb_flow import variable_options
    typ = next((typ for typ in state.get('types', []) if str(instance.get('type_id')) in {str(typ.get('id')), str(typ.get('name'))}), {})
    duts = {str(d.get('name')).upper() for typ in state.get('types', []) for d in typ.get('duts', [])}
    return [{'member': row['path'][4:], 'iec_type': row['iec_type']} for row in variable_options(typ, state)
            if row['path'].startswith('EXC.') and row['path'] != 'EXC.SPARE'
            and str(row['iec_type']).upper() not in duts
            and not str(row['iec_type']).upper().startswith(('ARRAY', 'FB_'))]


def _source(value):
    from backend.v2_plc_validation import _st_code
    value = value.strip()
    if not value or any(token in value for token in (';', ':=', '\n', '\r', '//', '(*', '*)')) or any(ord(char) < 32 for char in value):
        raise ValueError('Saisis une variable ou une expression ST sur une ligne, sans instruction, commentaire ni point-virgule.')
    code = _st_code(value)
    if "'" in code:
        raise ValueError('Chaîne ST non terminée dans la source.')
    if re.search(r'\b(?:IF|THEN|ELSE|END_IF|FOR|END_FOR|WHILE|END_WHILE|REPEAT|UNTIL|CASE|END_CASE|RETURN|VAR|END_VAR)\b', code, re.I):
        raise ValueError('La source doit être une expression ST, pas un bloc de programme.')
    stack = []
    for char in code:
        if char in '([': stack.append(char)
        elif char in ')]':
            if not stack or stack.pop() != {')': '(', ']': '['}[char]:
                raise ValueError('Parenthèses ou crochets non équilibrés dans la source ST.')
    if stack: raise ValueError('Parenthèses ou crochets non équilibrés dans la source ST.')
    return value


def _rows(state, instance, rows):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('Maximum 1 000 affectations par instance.')
    allowed = {row['member'] for row in options(state, instance)}
    result, seen, ids = [], set(), set()
    for number, raw in enumerate(rows, 1):
        try:
            row = Assignment.model_validate(raw).model_dump()
        except ValidationError as exc:
            raise ValueError(f'Ligne {number} : renseigne une cible EXC et une source ST valides (source : 2 000 caractères, commentaire : 500 maximum).') from exc
        row['member'] = row['member'].strip().removeprefix('EXC.')
        if row['member'] not in allowed:
            raise ValueError(f'Ligne {number} : variable EXC unitaire introuvable : {row["member"]}.')
        if row['member'].casefold() in seen or row['id'] in ids:
            raise ValueError(f'Ligne {number} : variable EXC ou identifiant en double.')
        seen.add(row['member'].casefold()); ids.add(row['id'])
        row.update(instance_id=str(instance.get('name') or instance['id']), source=_source(row['source']), comment=row['comment'].strip())
        result.append(row)
    return result


def replace(state, identifier, rows):
    instance = _instance(state, identifier)
    if instance is None: raise ValueError('Instance introuvable.')
    override = (state.get('st_overrides') or {}).get('GFC_EXC') or {}
    if override.get('body') is not None:
        raise ValueError('GFC_EXC utilise un corps ST manuel. Reviens au mode généré dans son éditeur ST pour appliquer ces affectations automatiquement.')
    rows = _rows(state, instance, rows)
    aliases = {str(instance.get('id')), str(instance.get('name'))}
    remaining = [row for row in state.get(KEY, []) if str(row.get('instance_id')) not in aliases]
    if {row['id'] for row in remaining} & {row['id'] for row in rows}:
        raise ValueError('Un identifiant d’affectation appartient déjà à une autre instance.')
    state[KEY] = remaining + rows
    return [f'{instance.get("name") or instance["id"]} : enregistrer {len(rows)} affectation(s) EXC particulière(s).']


def assignments(state):
    """Resolve validated destinations, preserving instance and row order."""
    result = []
    for row in state.get(KEY, []):
        instance = _instance(state, row.get('instance_id'))
        if instance is None: raise ValueError('Une affectation EXC particulière vise une instance supprimée.')
        parsed = _rows(state, instance, [row])[0]
        parsed['destination'] = f'GVL_EXC.EXC_{parsed["instance_id"]}.{parsed["member"]}'
        result.append(parsed)
    destinations = [row['destination'].casefold() for row in result]
    if len(set(destinations)) != len(destinations): raise ValueError('Une variable EXC possède plusieurs affectations particulières.')
    return result


def render(state):
    rows = assignments(state)
    if not rows: return ''
    lines = ['// Affectation de variables particulières']
    grouped = dict.fromkeys(row['instance_id'] for row in rows)
    for name in grouped:
        lines.extend(['', '// ' + name])
        for row in rows:
            if row['instance_id'] != name: continue
            if row.get('comment'):
                lines.append('// ' + ' '.join(row['comment'].splitlines()))
            lines.append(f'{row["destination"]} := {row["source"]};')
    return '\n'.join(lines)


def external_sources(state, known_names):
    """Only explicitly authored source references can be external to this project."""
    from backend.v2_plc_validation import _st_code
    result = []
    known = {str(name).casefold() for name in known_names} | {name.casefold() for name in MANAGED_GVLS}
    for row in assignments(state):
        roots = {match[0] for match in re.finditer(r'\bGVL_[A-Za-z0-9_]+(?=\s*\.)', _st_code(row['source'])) if match[0].casefold() not in known}
        if roots: result.append({**row, 'external_roots': roots})
    return result


def mask_external_sources(code, rows):
    """Exclude external RHS roots from static checks, leaving destinations intact."""
    from backend.v2_plc_validation import _st_code
    for row in rows:
        source = row['source']
        # Replace identifiers at their lexical offsets; quoted text is untouched.
        masked = source
        for match in reversed(list(re.finditer(r'\bGVL_[A-Za-z0-9_]+(?=\s*\.)', _st_code(source)))):
            if match[0] in row['external_roots']:
                masked = masked[:match.start()] + 'ExternalSource' + masked[match.end():]
        pattern = r'(?m)^([ \t]*' + re.escape(row['destination']) + r'\s*:=\s*)' + re.escape(source) + r'(\s*;[ \t]*$)'
        code = re.sub(pattern, lambda match: match[1] + masked + match[2], code)
    return code


def checked_body(pou, state, known_names):
    raw = pou.xpath("string(./*[local-name()='body']/*[local-name()='ST']/*[local-name()='xhtml'])")
    if pou.get('name') == 'GFC_EXC' and state.get(KEY):
        raw = mask_external_sources(raw, external_sources(state, known_names))
    return raw


def sync(state, roots, instances_by_alias, edits):
    from backend.v2_member_sync import rewrite_st
    changes, kept = [], []
    for old in state.get(KEY, []):
        row, removed = deepcopy(old), set()
        instance = _instance(state, row.get('instance_id'))
        if str(row.get('instance_id')) in instances_by_alias and 'EXC' in edits:
            path = rewrite_st('ROOT.' + row['member'], {'root': edits['EXC']}, removed)
            row['member'] = path.removeprefix('ROOT.')
        row['source'] = rewrite_st(row['source'], roots, removed)
        owned = str(row.get('instance_id')) in instances_by_alias
        if removed or instance is None or owned and row['member'] not in {option['member'] for option in options(state, instance)}:
            changes.append(f'Affectation EXC particulière retirée : {old.get("instance_id")}.{old["member"]}')
        else:
            kept.append(row)
            if row != old: changes.append(f'Affectation EXC particulière mise à jour : {row["instance_id"]}.{row["member"]}')
    if KEY in state: state[KEY] = kept
    return changes
