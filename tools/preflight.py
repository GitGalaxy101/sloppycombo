"""Verify every sheet cell and cross-sheet reference before generation/build."""

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inspect(directory: Path):
    issues, sheets = [], {}
    for path in sorted(directory.glob('*.json')):
        try:
            sheet = json.loads(path.read_text())
            name = sheet['id']
            if name in sheets:
                issues.append(f'{path.name}: duplicate sheet {name}')
            sheets[name] = sheet
            if not sheet.get('rows') or not sheet.get('columns'):
                issues.append(f'{name}: empty rows or columns')
        except (ValueError, KeyError, OSError) as error:
            issues.append(f'{path.name}: {error}')
    if not sheets:
        issues.append('No sheets were found')
    checked = 0
    for name, sheet in sheets.items():
        seen = set()
        for index, row in enumerate(sheet['rows']):
            label = f'{name}[{row.get("id", index)}]'
            if row.get('id') in seen:
                issues.append(f'{label}: duplicate row id')
            seen.add(row.get('id'))
            for extra in set(row) - set(sheet['columns']):
                issues.append(f'{label}.{extra}: undeclared column')
            for column, spec in sheet['columns'].items():
                cell = f'{label}.{column}'
                value = row.get(column)
                if value is None or value == '' or isinstance(value, str) and value.strip().upper() in {'TODO', 'TBD', '?'}:
                    issues.append(f'{cell}: unfilled')
                    continue
                kind = spec['type']
                valid = (
                    kind == 'str' and isinstance(value, str) or
                    kind == 'bool' and type(value) is bool or
                    kind == 'int' and type(value) is int or
                    kind == 'float' and type(value) in (int, float) and math.isfinite(value) or
                    kind == 'color' and isinstance(value, list) and len(value) == 3 and all(type(v) is int and 0 <= v <= 255 for v in value)
                )
                if not valid:
                    issues.append(f'{cell}: expected {kind}')
                    continue
                if 'min' in spec and value < spec['min'] or 'max' in spec and value > spec['max']:
                    issues.append(f'{cell}: out of range')
                    continue
                if 'choices' in spec and value not in spec['choices']:
                    issues.append(f'{cell}: unverified enum {value!r}')
                    continue
                if 'ref' in spec:
                    target = sheets.get(spec['ref'])
                    if not target or value not in {r['id'] for r in target['rows']}:
                        issues.append(f'{cell}: unresolved {spec["ref"]}/{value}')
                        continue
                checked += 1
    return sheets, issues, checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sheets', type=Path, default=ROOT / 'sheets')
    args = parser.parse_args()
    sheets, issues, checked = inspect(args.sheets)
    for issue in issues:
        print(issue)
    print(f'Preflight: {len(sheets)} sheets, {sum(len(s["rows"]) for s in sheets.values())} rows, {checked} cells verified; {len(issues)} issues.')
    print('This verifies design values and references. It does not establish real-game compatibility.')
    return 1 if issues else 0


if __name__ == '__main__':
    raise SystemExit(main())
