"""
このテストが守るもの: DXF-diff-manager の「機器符号候補」列が、DXF-extract-labels と同じ規則
（model/label_classifier.py、バイトコピー）で判定されること。

背景（2026-10-17）: 旧判定（ref_designator_judge.py）は 2026-10-10 以前の規則の凍結コピーで、
DATE・GND・R10・AWG24 等を候補にしていた。現行 primary に追随した（列の形式 'Y'/空欄は不変）。
旧テストで True だった GND・SYSTEM・R10 が None に変わるのは、この追随による意図的な変更。

実行:
    cd DXF-diff-manager
    python -m pytest tests/unit/test_label_classifier_adoption.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from model.diff_export import _annotate_ref_designator_candidates


def _mark(old_label=None, new_label=None):
    rows = [{'Old Label': old_label, 'New Label': new_label}]
    count = _annotate_ref_designator_candidates(rows)
    return rows[0]['機器符号候補'], count


@pytest.mark.parametrize('label', ['C1', 'C1(2.2K)', 'CB001', 'MCCB001', 'X14A', 'TB1'])
def test_designator_labels_are_marked_y(label):
    assert _mark(new_label=label) == ('Y', 1)


@pytest.mark.parametrize('label', [
    'R10', 'R10(2.2K)',          # OTHER_PATTERNS（A/C/L/Q/U/F 以外の英大文字1字＋数字）
    'GND', 'SYSTEM', 'DATE',     # 英字のみの語は候補にしない
    'N24-', 'L1+',               # 末尾が +/- の電源端子表記
    'H',                         # 英字1字
    'タイトル',
    'RK10', 'CN-IF2-1',          # FORCED_REGION_NAME_PATTERNS（領域名）
])
def test_non_designator_labels_are_not_marked(label):
    assert _mark(new_label=label) == (None, 0)


def test_fullwidth_label_is_normalized_before_judgment():
    """diff-manager の extract_labels.py は NFKC 正規化をしない。全角のまま渡っても
    判定側が正規化すること（正規化を怠ると全角ラベルがすり抜ける）。"""
    assert _mark(new_label='Ｃ１') == ('Y', 1)


def test_old_only_new_only_and_none_rows_are_counted_correctly():
    rows = [
        {'Old Label': 'C1', 'New Label': None},       # Old だけ候補
        {'Old Label': None, 'New Label': 'CB001'},    # New だけ候補
        {'Old Label': 'GND', 'New Label': 'DATE'},    # どちらも候補外
    ]
    assert _annotate_ref_designator_candidates(rows) == 2
    assert [r['機器符号候補'] for r in rows] == ['Y', 'Y', None]
