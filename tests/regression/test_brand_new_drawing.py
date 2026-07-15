"""
完全新規図面（流用元の参照がない図面）に関する回帰テスト。

背景:
    1. 方式C（pair_list）で、流用先図番のみが記載され流用元図番が空白の行
       （例: sample-dxf/pairC の DE3527-556-01B）が、誤って「片側のみのペア」
       （one_sided）に分類され、「完全新規図面（流用元図番なし）」セクションに
       表示されない不具合が報告された。流用先が空白の行（one_sided）と、
       流用元が空白だが流用先はある行（no_source_defined）を区別するよう修正した。
    2. 図面管理台帳に完全新規図面が一切登録されない仕様だったが、登録対象に含める
       よう変更。Parent欄は "none"、Deleted/Diff/Unchanged Entitiesは "n/a"、
       Added Entities = Total Entities（その図面単独の総エンティティ数）とする。

実行:
    cd DXF-diff-manager
    python -m tests.regression.test_brand_new_drawing
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

from model.pairing import (
    build_pairs_from_list,
    compute_unchanged_drawings,
    get_brand_new_drawing_pairs,
    STATUS_NO_SOURCE_DEFINED,
    STATUS_ONE_SIDED,
)
from model.master_ledger import create_empty_master_df, update_parent_child_master


def test_target_only_row_is_no_source_defined_not_one_sided():
    """流用先のみ記載（流用元空白）の行は one_sided ではなく no_source_defined。"""
    df = pd.DataFrame({
        '流用元図番': ['', 'A'],
        '流用先図番': ['DE3527-556-01B', ''],
    })
    pairs = build_pairs_from_list(df, {})
    assert pairs[0]['status'] == STATUS_NO_SOURCE_DEFINED  # 流用先のみ→完全新規図面
    assert pairs[1]['status'] == STATUS_ONE_SIDED          # 流用元のみ→片側のみのペア


def test_get_brand_new_drawing_pairs_pair_list_excludes_identical():
    """方式C: identical（変更していない図面）に分類された図番は完全新規図面に含めない。"""
    files = {'NEW1': {'temp_path': '/tmp/NEW1.dxf'}, 'X': {'temp_path': '/tmp/X.dxf'}}
    df = pd.DataFrame({
        '流用元図番': ['', 'X', ''],
        '流用先図番': ['NEW1', 'X', 'NEW1'],
    })
    pairs = build_pairs_from_list(df, files)
    result = get_brand_new_drawing_pairs(pairs, 'pair_list')
    assert {p['main_drawing'] for p in result} == {'NEW1'}


def test_get_brand_new_drawing_pairs_excludes_unuploaded_target():
    """方式C: 流用先のファイルが未アップロードの図番は完全新規図面に含めない
    （例: DE3527-556-01B。流用元図番が空白でも、肝心のファイル自体が無ければ
    「完全新規図面」として台帳登録・Step3表示の対象にはしない）。
    """
    df = pd.DataFrame({'流用元図番': [''], '流用先図番': ['DE3527-556-01B']})
    pairs = build_pairs_from_list(df, {})  # ファイル未アップロード
    assert pairs[0]['status'] == STATUS_NO_SOURCE_DEFINED
    result = get_brand_new_drawing_pairs(pairs, 'pair_list')
    assert result == []


def test_title_subtitle_populated_for_brand_new_drawing():
    """方式C: 完全新規図面のペアにも、流用先ファイルから抽出済みのTitle/Subtitleが入る。"""
    files = {'NEW1': {'temp_path': '/tmp/NEW1.dxf', 'title': 'T1', 'subtitle': 'S1'}}
    df = pd.DataFrame({'流用元図番': [''], '流用先図番': ['NEW1']})
    pairs = build_pairs_from_list(df, files)
    assert pairs[0]['title'] == 'T1'
    assert pairs[0]['subtitle'] == 'S1'


def test_compute_unchanged_drawings_excludes_unuploaded_identical():
    """方式C: identical でもファイル未アップロードの図番は「変更していない図面」から除外する。

    これにより 差分抽出が可能なペア(unique)+完全新規図面+変更していない図面 が
    流用先図面総数(a。ファイル実在のみで算出)と一致する（2026-06修正。以前は
    ファイル有無を問わず識別子の一致だけで「変更していない図面」に含めていたため、
    a との合計が一致しない不整合が実データ(sample-dxf/pairC)で見つかった）。
    """
    files = {'WITH_FILE': {'temp_path': '/tmp/WITH_FILE.dxf'}}  # NO_FILE は未アップロード
    df = pd.DataFrame({
        '流用元図番': ['WITH_FILE', 'NO_FILE'],
        '流用先図番': ['WITH_FILE', 'NO_FILE'],
    })
    pairs = build_pairs_from_list(df, files)
    result = compute_unchanged_drawings(pairs, 'pair_list')
    assert result == {'WITH_FILE'}


def test_show_missing_drawings_includes_identical_rows():
    """identical（流用元==流用先）の行も、ファイル未アップロードならば
    統合済みの「未アップロードの図番」セクションの対象に含める（2026-06修正。
    以前は比較対象外として無条件にスキップしていたため、ファイルが無い
    identical 宣言がどこにも警告表示されなかった）。
    """
    import streamlit as st
    import app  # UI層（_show_missing_drawings）の検証のためここだけ app をインポート
    df = pd.DataFrame({'流用元図番': ['NO_FILE'], '流用先図番': ['NO_FILE']})

    captured_dataframes = []
    orig_dataframe = st.dataframe
    st.dataframe = lambda data, *a, **k: captured_dataframes.append(data)
    try:
        app._show_missing_drawings(df, {})
    finally:
        st.dataframe = orig_dataframe

    assert len(captured_dataframes) == 1
    df_shown = captured_dataframes[0]
    assert 'NO_FILE' in df_shown['流用元図番（未アップロード）'].values
    assert 'NO_FILE' in df_shown['流用先図番（未アップロード）'].values


def test_update_master_brand_new_drawing_parent_is_none():
    """完全新規図面を台帳に登録すると Parent='none'、エンティティ列は規定の形式になる。"""
    master_df = create_empty_master_df()
    pair = {
        'main_drawing': 'NEW1',
        'source_drawing': None,
        'relation': '完全新規図面',
        'title': 'T', 'subtitle': 'S',
        'entity_counts': {'added_entities': 42, 'total_entities': 42},
    }
    updated, added_count = update_parent_child_master(master_df, [pair])
    assert added_count == 1
    row = updated[updated['Child'] == 'NEW1'].iloc[0]
    assert row['Parent'] == 'none'
    assert row['Deleted Entities'] == 'n/a'
    assert row['Diff Entities'] == 'n/a'
    assert row['Unchanged Entities'] == 'n/a'
    assert row['Added Entities'] == 42
    assert row['Total Entities'] == 42
    assert row['Added Entities'] == row['Total Entities']


def test_update_master_brand_new_drawing_without_entity_counts_yet():
    """エンティティ数算出前（pair-list作成直後）の先行登録では n/a 列のみ確定する。"""
    master_df = create_empty_master_df()
    pair = {
        'main_drawing': 'NEW2',
        'source_drawing': None,
        'relation': '完全新規図面',
        'title': None, 'subtitle': None,
        'entity_counts': None,
    }
    updated, added_count = update_parent_child_master(master_df, [pair])
    assert added_count == 1
    row = updated[updated['Child'] == 'NEW2'].iloc[0]
    assert row['Parent'] == 'none'
    assert row['Deleted Entities'] == 'n/a'
    assert pd.isna(row['Added Entities'])


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    for t in tests:
        try:
            t(); print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append(t.__name__); print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
