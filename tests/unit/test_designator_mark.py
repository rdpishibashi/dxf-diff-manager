"""
このテストが守るもの: diff_labels.xlsx の「機器符号候補」列が DXF-extract-labels と同じ
'DEF'／'CAN'／空欄になること（2026-11-06、ユーザー要望）。

- `diff_export._designator_mark()`: 機器符号一覧の索引に含まれれば DEF、候補パターンなら CAN、
  それ以外は空欄。索引なし（鍵なし等）なら DEF は出ない
- `_annotate_ref_designator_candidates()`: 行の値は Old/New の強い方（DEF > CAN > 空欄）、
  件数は DEF＋CAN の行数
- `create_diff_zip(master_index=…)` が diff_labels.xlsx の列と Summary の件数まで DEF を届ける
- 図番（`EE5319-300-01B` など）は機器符号候補にしない（2026-11-06、ユーザー指摘）。分類器は図番の形を
  候補と判定するが、DXF-extract-labels は図面情報枠の中を集めないので図番が出ない。DXF-diff-manager は
  枠の中も含めるので、`_designator_mark()` で外す（DXF-extract-labels との意図的な差）
- 図番以外では、規則が DXF-extract-labels の `ref_designator.designator_mark()` とずれないこと
  （`ref_designator.py` は持ち込んでいないので、隣のプロジェクトで実行して結果を比べる。
  隣が無いときだけ skip）
"""
import io
import json
import os
import subprocess
import sys
import tempfile

import ezdxf
import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from model.diff_export import (  # noqa: E402
    _annotate_ref_designator_candidates, _designator_mark, create_diff_zip,
)
from model.pairing import build_pairs_from_list  # noqa: E402

_EXTRACT_LABELS_DIR = os.path.join(_ROOT, '..', 'DXF-extract-labels')


class _StubIndex:
    """機器符号一覧の索引のスタブ（`contains()` だけを持つ）。"""

    def __init__(self, *codes):
        self._codes = set(codes)

    def contains(self, label):
        return label in self._codes


LABELS = ['CB001', 'C1', 'MCCB001', 'TB1', 'GND', 'R10', '電源', 'SYSTEM', 'X14A', 'Ｃ１', '']
IN_MASTER = ('CB001', 'TB1')


@pytest.mark.parametrize('label, expected', [
    ('CB001', 'DEF'), ('TB1', 'DEF'), ('C1', 'CAN'), ('MCCB001', 'CAN'),
    ('GND', None), ('R10', None), ('電源', None), (None, None),
])
def test_mark_with_master_index(label, expected):
    assert _designator_mark(label, _StubIndex(*IN_MASTER)) == expected


@pytest.mark.parametrize('label', [
    'EE5319-300-01B', 'EE7319-300-01A', 'EE7319-307A', 'DE3527-553-10E', 'ee7319-307a', ' EE7319-307A ',
])
def test_drawing_numbers_are_never_candidates(label):
    """図番の形のラベルは、索引の有無に関わらず空欄（索引に入っていても DEF にしない）。"""
    assert _designator_mark(label, None) is None
    assert _designator_mark(label, _StubIndex(label)) is None


def test_label_containing_drawing_number_is_not_excluded():
    """図番を「含む」ラベルまでは外さない（ラベル全体が図番のときだけ）。"""
    assert _designator_mark('CB001', None) == 'CAN'
    assert _designator_mark('EE7319-307A 参照', None) is None  # 候補パターンにも一致しない（従来どおり）


def test_mark_without_master_index_never_def():
    assert _designator_mark('CB001', None) == 'CAN'
    assert _designator_mark('GND', None) is None


def _row(old, new):
    return {'X': None, 'Y': None, 'Old Label': old, 'New Label': new}


def test_row_takes_stronger_mark_and_counts_def_and_can():
    rows = [
        _row('C1', 'CB001'),     # CAN と DEF → DEF
        _row('CB001', 'GND'),    # DEF と空欄 → DEF
        _row('GND', 'C1'),       # 空欄と CAN → CAN
        _row(None, 'R10'),       # 空欄
        _row('MCCB001', None),   # CAN
    ]
    count = _annotate_ref_designator_candidates(rows, _StubIndex(*IN_MASTER))
    assert [r['機器符号候補'] for r in rows] == ['DEF', 'DEF', 'CAN', None, 'CAN']
    assert count == 4


def test_drawing_number_row_is_blank_and_not_counted():
    rows = [_row('EE5319-300-01B', 'EE6094-300-01C'), _row(None, 'C1')]
    count = _annotate_ref_designator_candidates(rows, None)
    assert [r['機器符号候補'] for r in rows] == [None, 'CAN']
    assert count == 1


def test_create_diff_zip_writes_def_to_diff_labels_for_brand_new_drawing():
    """master_index を渡すと、完全新規図面のシートに DEF が出て、Summary の件数は DEF＋CAN。"""
    with tempfile.TemporaryDirectory() as d:
        doc = ezdxf.new()
        for i, text in enumerate(['CB001', 'C1', 'GND']):
            doc.modelspace().add_text(text, dxfattribs={'insert': (0, 10 * i)})
        path = os.path.join(d, 'BRANDNEW-DEF.dxf')
        doc.saveas(path)
        all_files_dict = {'BRANDNEW-DEF': {'temp_path': path, 'title': None, 'subtitle': None,
                                           'filename': 'BRANDNEW-DEF.dxf'}}
        pair_df = pd.DataFrame({'流用元図番': [''], '流用先図番': ['BRANDNEW-DEF']})
        pairs = build_pairs_from_list(pair_df, all_files_dict)

        _, results, diff_labels_excel, _, _ = create_diff_zip(
            pairs, step1_mode='pair_list', master_index=_StubIndex('CB001'))

    assert results[0]['success']
    sheet_df = pd.read_excel(io.BytesIO(diff_labels_excel), sheet_name='BRANDNEW-DEF')
    marks = dict(zip(sheet_df['New: BRANDNEW-DEF'], sheet_df['機器符号候補']))
    assert marks['CB001'] == 'DEF'
    assert marks['C1'] == 'CAN'
    assert pd.isna(marks['GND'])
    summary_df = pd.read_excel(io.BytesIO(diff_labels_excel), sheet_name='Summary')
    assert summary_df.loc[0, '機器符号候補数'] == 2


def test_create_diff_zip_writes_def_to_diff_labels_for_normal_pair():
    """通常ペア（RevUp）でも master_index の DEF が出る。索引なしなら CAN のまま。"""
    with tempfile.TemporaryDirectory() as d:
        old_doc, new_doc = ezdxf.new(), ezdxf.new()
        new_doc.modelspace().add_text('CB001', dxfattribs={'insert': (0, 0)})
        old_path = os.path.join(d, 'OLD-DEF.dxf')
        new_path = os.path.join(d, 'NEW-DEF.dxf')
        old_doc.saveas(old_path)
        new_doc.saveas(new_path)
        pair = {
            'main_drawing': 'NEW-DEF', 'source_drawing': 'OLD-DEF',
            'main_file_info': {'temp_path': new_path, 'title': None, 'subtitle': None},
            'source_file_info': {'temp_path': old_path},
            'status': 'complete', 'relation': 'RevUp', 'title': None, 'subtitle': None,
        }
        marks = {}
        for name, index in (('with', _StubIndex('CB001')), ('without', None)):
            _, results, excel, _, _ = create_diff_zip([pair], master_index=index)
            assert results[0]['success']
            df = pd.read_excel(io.BytesIO(excel), sheet_name='NEW-DEF')
            marks[name] = df.loc[df['New: NEW-DEF'] == 'CB001', '機器符号候補'].iloc[0]

    assert marks == {'with': 'DEF', 'without': 'CAN'}


_PRIMARY_SCRIPT = """
import json, sys
sys.path.insert(0, '.')
from model.ref_designator import designator_mark

class Stub:
    def __init__(self, codes): self.codes = set(codes)
    def contains(self, label): return label in self.codes

labels, in_master = json.loads(sys.stdin.read())
print(json.dumps({
    'with': [designator_mark(l, Stub(in_master)) for l in labels],
    'without': [designator_mark(l, None) for l in labels],
}))
"""


def test_mark_rule_matches_extract_labels_designator_mark():
    """DXF-extract-labels の `designator_mark()` と同じ結果になる（隣が無いときだけ skip）。"""
    if not os.path.isdir(_EXTRACT_LABELS_DIR):
        pytest.skip(f'DXF-extract-labels が隣にありません（{_EXTRACT_LABELS_DIR}）')
    proc = subprocess.run(
        [sys.executable, '-c', _PRIMARY_SCRIPT], cwd=_EXTRACT_LABELS_DIR,
        input=json.dumps([LABELS, list(IN_MASTER)]), capture_output=True, text=True,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, check=True)
    primary = json.loads(proc.stdout)
    assert [_designator_mark(l, _StubIndex(*IN_MASTER)) for l in LABELS] == primary['with']
    assert [_designator_mark(l, None) for l in LABELS] == primary['without']
