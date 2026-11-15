"""
空欄だけの列を持つ既存台帳を再アップロードし、Type B で差分抽出すると
「差分抽出開始」が TypeError で失敗した不具合の回帰テスト（2026-11-15）。

実データ: 台帳 ME26-9603-0_ZM00_307.xlsx（Subtitle 列が全部空、Title 列は1行だけ値あり）。
1. Step 3 のペア作成時（タイトル未抽出）に既存行 EE7319-307A の Title へ None を書き、
   Title 列が全部空になって pandas が float64 にした。
2. 差分抽出時にタイトル 'ＭＰＤ ＲＡＣＫ１' を書くと、pandas 3 は
   「Invalid value ... for dtype 'float64'」の TypeError を出した。
   画面は例外直後の st.rerun() でエラーが一瞬で消えていた。

台帳は Excel を経由して作る（pandas の型推測を実データと同じ経路で通すため）。
"""
import io
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import pandas as pd

from model.master_ledger import (
    load_drawing_list,
    load_parent_child_master,
    update_drawing_list,
    update_parent_child_master,
    DRAWING_LIST_SHEET_NAME,
    MASTER_SHEET_NAME,
)


def _ledger_bytes():
    master = pd.DataFrame({
        'Child': ['EE7319-300-01A', 'EE7319-307A'],
        'Parent': ['none', 'EE5610-307D'],
        'Relation': ['完全新規図面', '流用'],
        'Title': [None, 'ＬＡＢＥＬ'],
        'Subtitle': [None, None],
        'Recorded Date': [datetime(2026, 11, 5), datetime(2026, 11, 5)],
        'Note': [None, None],
        'Deleted Entities': [None, 71],
        'Added Entities': [35455, 474],
        'Diff Entities': [None, 545],
        'Unchanged Entities': [None, 525],
        'Total Entities': [35455, 1070],
        'Unchanged Offset Entities': [None, 0],
    })
    package = pd.DataFrame({
        'Sashiban': ['ME26-9603-0'], 'Module': ['ZM00'], 'Side': ['307'],
        'Child Drawing Number': ['EE7319-307A'], 'Parent Drawing Number': ['EE5610-307D'],
        'Title': ['ＬＡＢＥＬ'], 'Subtitle': [None], 'Unchanged Offset Entities': [0],
        'Recorded Date': [datetime(2026, 11, 5)],
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        master.to_excel(writer, sheet_name=MASTER_SHEET_NAME, index=False)
        package.to_excel(writer, sheet_name=DRAWING_LIST_SHEET_NAME, index=False)
    return buf.getvalue()


def test_title_and_subtitle_written_after_pairing_step_cleared_title():
    master_df, err = load_parent_child_master(io.BytesIO(_ledger_bytes()))
    assert err is None

    pairs_at_pairing = [
        {'source_drawing': 'EE6094-300-01C', 'main_drawing': 'EE7319-300-01A',
         'relation': '流用', 'title': None, 'subtitle': None},
        {'source_drawing': 'EE5610-307D', 'main_drawing': 'EE7319-307A',
         'relation': '流用', 'title': None, 'subtitle': None},
    ]
    master_df, _ = update_parent_child_master(master_df, pairs_at_pairing)

    pairs_at_diff = [
        {'source_drawing': 'EE5610-307D', 'main_drawing': 'EE7319-307A',
         'relation': '流用', 'title': 'ＭＰＤ ＲＡＣＫ１', 'subtitle': 'サブ',
         'entity_counts': {'deleted_entities': 1, 'added_entities': 2,
                           'diff_entities': 3, 'unchanged_entities': 4,
                           'total_entities': 6}},
    ]
    updated, _ = update_parent_child_master(master_df, pairs_at_diff)

    row = updated[updated['Child'] == 'EE7319-307A'].iloc[0]
    assert row['Title'] == 'ＭＰＤ ＲＡＣＫ１'
    assert row['Subtitle'] == 'サブ'
    assert row['Deleted Entities'] == 1


def test_package_list_accepts_subtitle_when_column_is_all_empty():
    drawing_list_df, err = load_drawing_list(io.BytesIO(_ledger_bytes()))
    assert err is None

    updated, added = update_drawing_list(
        drawing_list_df,
        [{'main_drawing': 'EE7319-300-01A', 'source_drawing': 'EE6094-300-01C',
          'title': 'ＭＰＤ ＲＡＣＫ１', 'subtitle': 'サブ'}],
        'ME26-9603-0', 'ZM00', '307',
    )
    assert added == 1
    assert updated.iloc[-1]['Subtitle'] == 'サブ'
