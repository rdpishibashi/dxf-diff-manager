"""
このテストが守るもの: diff_labels.xlsx プレビューの「機器符号候補」絞り込み
（`model/candidate_filter.py`）が、DXF-extract-labels の `app.py` の同名関数と同じ振る舞いで、
選択肢の順（DEF → CAN → その他 → （空欄））、欠損値・空文字の（空欄）扱い、
絞り込み後も行の index を保つことを満たす（2026-11-06 新設）。

あわせて、`app.render_candidate_pills()` が列の無いシート（Summary）・行の無いシートで
選択欄を出さず None を返し、列のあるシートでは選択欄を出すこと（Streamlit の `AppTest`）。
"""
import os
import sys

import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from model.candidate_filter import (  # noqa: E402
    BLANK_MARK, candidate_filter_options, filter_by_candidate,
)


def _df():
    return pd.DataFrame({
        '機器符号候補': ['DEF', 'CAN', None, '', float('nan'), 'DEF'],
        'ラベル': ['a', 'b', 'c', 'd', 'e', 'f'],
    })


def test_options_order_is_def_can_other_blank():
    series = pd.Series(['XYZ', None, 'CAN', 'DEF', 'ABC', ''])
    assert candidate_filter_options(series) == ['DEF', 'CAN', 'ABC', 'XYZ', BLANK_MARK]


def test_options_only_values_present():
    assert candidate_filter_options(pd.Series(['CAN', 'CAN'])) == ['CAN']
    assert candidate_filter_options(pd.Series([None, ''])) == [BLANK_MARK]


def test_filter_keeps_selected_rows_and_index():
    out = filter_by_candidate(_df(), ['DEF', 'CAN'])
    assert list(out['ラベル']) == ['a', 'b', 'f']
    assert list(out.index) == [0, 1, 5]


def test_filter_blank_matches_none_empty_and_nan():
    assert list(filter_by_candidate(_df(), [BLANK_MARK])['ラベル']) == ['c', 'd', 'e']


def test_filter_all_selected_returns_all_and_none_selected_returns_empty():
    assert len(filter_by_candidate(_df(), ['DEF', 'CAN', BLANK_MARK])) == 6
    assert filter_by_candidate(_df(), []).empty


_PILLS_SCRIPT = """
import sys
import pandas as pd
import streamlit as st
sys.path.insert(0, {root!r})
import app as diff_app

with_col = pd.DataFrame({{'機器符号候補': ['DEF', None], 'ラベル': ['a', 'b']}})
st.session_state['with_col'] = diff_app.render_candidate_pills(with_col, 'k1')
st.session_state['no_col'] = diff_app.render_candidate_pills(pd.DataFrame({{'図番': ['x']}}), 'k2')
st.session_state['empty'] = diff_app.render_candidate_pills(
    pd.DataFrame(columns=['機器符号候補', 'ラベル']), 'k3')
"""


def test_render_candidate_pills_in_app():
    """列があるシートでは選択欄（pills）を出し、列の無いシート・空のシートでは出さずに None。"""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string(_PILLS_SCRIPT.format(root=_ROOT), default_timeout=60).run()

    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state['with_col'] == ['DEF', BLANK_MARK]  # 初期状態は全選択
    assert at.session_state['no_col'] is None
    assert at.session_state['empty'] is None
    assert len(at.button_group) == 1  # 選択欄は列のあるシートの1つだけ


def test_preview_reads_sheet_without_exceldfile_parse_deprecation(recwarn):
    """プレビューで使う読み込み（`pd.read_excel(ExcelFile, sheet_name=...)`）が、pandas 3 の
    `ExcelFile.parse is deprecated`（Pandas4Warning）を出さない（2026-11-06、画面の警告で発覚）。
    `app.py` が `.parse(` を使っていないことも見る。"""
    import io
    from pathlib import Path

    from model.label_diff import build_diff_labels_workbook

    data = build_diff_labels_workbook(
        [{'sheet_name': 'S1', 'rows': [{'機器符号候補': 'CAN', 'X': 0, 'Y': 0, 'Old Label': None, 'New Label': 'C1'}],
          'old_label_name': 'Old', 'new_label_name': 'New'}],
        include_ref_designator_column=True)
    xl = pd.ExcelFile(io.BytesIO(data))
    df = pd.read_excel(xl, sheet_name='S1')

    assert list(df['機器符号候補']) == ['CAN']
    assert not [w for w in recwarn if 'parse' in str(w.message)]
    assert '.parse(' not in (Path(_ROOT) / 'app.py').read_text(encoding='utf-8')
