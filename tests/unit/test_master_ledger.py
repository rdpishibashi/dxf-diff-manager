"""
utils.master_ledger（UI 非依存）のユニットテスト。

streamlit に依存しないため app.py をインポートせず、コアを直接検証する
（tests/unit/test_pairing.py と同じ方針）。

実行:
    cd DXF-diff-manager
    python -m tests.unit.test_master_ledger
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

from utils.master_ledger import (
    load_parent_child_master,
    update_parent_child_master,
    create_empty_master_df,
    save_master_to_bytes,
    make_dataframe_arrow_compatible,
)


# --- create_empty_master_df ---

def test_create_empty_master_df_has_required_columns():
    df = create_empty_master_df()
    assert list(df.columns) == [
        'Child', 'Parent', 'Relation', 'Title', 'Subtitle', 'Recorded Date', 'Note',
        'Deleted Entities', 'Added Entities', 'Diff Entities', 'Unchanged Entities', 'Total Entities',
    ]
    assert len(df) == 0


# --- update_parent_child_master ---

def test_update_parent_child_master_adds_new_record():
    master_df = create_empty_master_df()
    pair = {
        'main_drawing': 'B1', 'source_drawing': 'A1', 'relation': '流用',
        'title': 'T', 'subtitle': 'S',
        'entity_counts': {
            'deleted_entities': 1, 'added_entities': 2,
            'diff_entities': 3, 'unchanged_entities': 4, 'total_entities': 5,
        },
    }
    updated, added_count = update_parent_child_master(master_df, [pair])
    assert added_count == 1
    row = updated[updated['Child'] == 'B1'].iloc[0]
    assert row['Parent'] == 'A1'
    assert row['Deleted Entities'] == 1
    assert row['Total Entities'] == 5


def test_update_parent_child_master_skips_pair_without_child():
    master_df = create_empty_master_df()
    pair = {'main_drawing': None, 'source_drawing': 'A1'}
    updated, added_count = update_parent_child_master(master_df, [pair])
    assert added_count == 0
    assert len(updated) == 0


def test_update_parent_child_master_existing_record_relation_changed_suffix():
    master_df = create_empty_master_df()
    first = {'main_drawing': 'B1', 'source_drawing': 'A1', 'relation': '流用'}
    updated, _ = update_parent_child_master(master_df, [first])
    second = {'main_drawing': 'B1', 'source_drawing': 'A1', 'relation': 'RevUp'}
    updated, added_count = update_parent_child_master(updated, [second])
    assert added_count == 0  # 既存レコードの更新（新規追加ではない）
    row = updated[updated['Child'] == 'B1'].iloc[0]
    assert row['Relation'] == 'RevUp-changed'


# --- load_parent_child_master ---

def test_load_parent_child_master_missing_required_column(tmp_path):
    path = tmp_path / "master.xlsx"
    pd.DataFrame({'Child': ['B1']}).to_excel(path, index=False)  # Parent列なし
    df, error = load_parent_child_master(str(path))
    assert df is None
    assert 'Parent' in error


def test_load_parent_child_master_success(tmp_path):
    path = tmp_path / "master.xlsx"
    pd.DataFrame({'Child': ['B1'], 'Parent': ['A1']}).to_excel(path, index=False)
    df, error = load_parent_child_master(str(path))
    assert error is None
    assert len(df) == 1


# --- save_master_to_bytes ---

def test_save_master_to_bytes_returns_nonempty_excel():
    master_df = create_empty_master_df()
    data = save_master_to_bytes(master_df, pairs=[], mode='auto', total_drawings_count=0)
    assert isinstance(data, bytes) and len(data) > 0


def test_save_master_to_bytes_handles_na_entity_strings():
    """完全新規図面の 'n/a' 文字列が混在してもサマリー合計でエラーにならない。"""
    master_df = create_empty_master_df()
    master_df.loc[0] = {
        'Child': 'B1', 'Parent': 'none', 'Relation': '完全新規図面',
        'Title': None, 'Subtitle': None, 'Recorded Date': None, 'Note': None,
        'Deleted Entities': 'n/a', 'Added Entities': 10, 'Diff Entities': 'n/a',
        'Unchanged Entities': 'n/a', 'Total Entities': 10,
    }
    data = save_master_to_bytes(master_df, pairs=[], mode='pair_list', total_drawings_count=1)
    assert isinstance(data, bytes) and len(data) > 0


# --- make_dataframe_arrow_compatible ---

def test_make_dataframe_arrow_compatible_mixed_entity_columns():
    """'n/a' と整数が混在するエントリ数カラムが pyarrow でシリアライズ可能になる。

    完全新規図面の行（'n/a'）と通常ペアの行（整数）が混在した台帳をそのまま
    st.dataframe に渡すと pyarrow が変換に失敗して警告を出す。表示用コピーで
    混在カラムを文字列統一することで Arrow 互換になることを検証する。
    """
    import datetime
    pa = __import__('pyarrow')

    master_df = create_empty_master_df()
    master_df.loc[0] = {
        'Child': 'B1', 'Parent': 'A1', 'Relation': '流用',
        'Title': 'T', 'Subtitle': 'S',
        'Recorded Date': datetime.datetime(2026, 7, 8), 'Note': None,
        'Deleted Entities': 10, 'Added Entities': 20, 'Diff Entities': 30,
        'Unchanged Entities': 40, 'Total Entities': 100,
    }
    master_df.loc[1] = {
        'Child': 'B2', 'Parent': 'none', 'Relation': '完全新規図面',
        'Title': 'T2', 'Subtitle': None,
        'Recorded Date': datetime.datetime(2026, 7, 8), 'Note': None,
        'Deleted Entities': 'n/a', 'Added Entities': 5, 'Diff Entities': 'n/a',
        'Unchanged Entities': 'n/a', 'Total Entities': 5,
    }

    # 修正前は失敗することを確認（回帰の前提）
    try:
        pa.Table.from_pandas(master_df)
        raise AssertionError("前提が崩れている: 元の混在DataFrameがArrow変換に成功してしまった")
    except pa.lib.ArrowInvalid:
        pass

    display_df = make_dataframe_arrow_compatible(master_df)

    # 修正後は成功する
    pa.Table.from_pandas(display_df)  # 例外が出ないこと

    # 元のDataFrameは変更されない
    assert master_df.loc[0, 'Deleted Entities'] == 10

    # 混在カラムは文字列統一される
    assert display_df.loc[0, 'Deleted Entities'] == '10'
    assert display_df.loc[1, 'Deleted Entities'] == 'n/a'

    # 純粋な整数カラム（全行が数値）は数値のまま維持される
    assert display_df.loc[0, 'Total Entities'] == 100


def test_make_dataframe_arrow_compatible_leaves_clean_columns_untouched():
    """数値のみ・文字列のみの純粋なカラムは変換されない。"""
    df = pd.DataFrame({
        'counts': [1, 2, 3],           # 数値のみ
        'labels': ['a', 'b', 'c'],     # 文字列のみ
    })
    display_df = make_dataframe_arrow_compatible(df)
    assert display_df['counts'].tolist() == [1, 2, 3]  # 数値のまま
    assert display_df['labels'].tolist() == ['a', 'b', 'c']


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    import tempfile
    for t in tests:
        try:
            if 'tmp_path' in t.__code__.co_varnames[:t.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as d:
                    from pathlib import Path
                    t(Path(d))
            else:
                t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append(t.__name__); print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
