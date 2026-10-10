import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from preflight import inspect
from generate_design import render


def test_every_declared_sheet_cell_and_reference_is_checked():
    sheets,issues,checked=inspect(ROOT/'sheets')
    assert not issues
    assert checked==sum(len(s['columns'])*len(s['rows']) for s in sheets.values())
    assert (ROOT/'src/deltadash/generated_design.py').read_text()==render(ROOT/'sheets')


def test_unfilled_unknown_and_unresolved_cells_fail_preflight(tmp_path):
    data={'id':'test','class':'Test','columns':{'id':{'type':'str'},'next':{'type':'str','ref':'test'},'speed':{'type':'float','min':1}},'rows':[{'id':'only','next':'missing','speed':None,'unmapped':9}]}
    (tmp_path/'test.json').write_text(json.dumps(data))
    _,issues,_=inspect(tmp_path)
    assert any('unfilled' in s for s in issues)
    assert any('unresolved' in s for s in issues)
    assert any('undeclared' in s for s in issues)
