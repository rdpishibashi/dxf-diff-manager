"""
model.master_ledger（UI 非依存）のユニットテスト。

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

from model.master_ledger import (
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


def test_update_parent_child_master_no_futurewarning_setting_na_on_numeric_column():
    """完全新規図面の先行登録（entity_counts 未確定 → 数値カラムが NaN のみで
    float64 として残る）の後、create_diff_zip() 側の2回目の update 呼び出しで
    実際のエンティティ数を書き込む際、同じ行の 'Deleted/Diff/Unchanged Entities'
    に "n/a" 文字列を代入する（既存レコード更新パス、model/master_ledger.py の
    元の行141）。台帳を Excel 経由で読み込んだ場合など、この時点でカラムが
    まだ float64 のままだと pandas FutureWarning（将来 TypeError 化予定）が出て
    いた。警告を例外に昇格させ、出ないことを確認する回帰テスト。"""
    import warnings

    master_df = pd.DataFrame({
        'Child': ['B1'], 'Parent': ['none'], 'Relation': ['完全新規図面'],
        'Title': [None], 'Subtitle': [None], 'Recorded Date': [None], 'Note': [None],
        'Deleted Entities': [float('nan')], 'Added Entities': [float('nan')],
        'Diff Entities': [float('nan')], 'Unchanged Entities': [float('nan')],
        'Total Entities': [float('nan')],
    })
    assert master_df['Deleted Entities'].dtype != object  # 前提: NaNのみで float64

    brand_new_pair = {
        'main_drawing': 'B1', 'source_drawing': None, 'relation': '完全新規図面',
        'title': None, 'subtitle': None,
        'entity_counts': {'added_entities': 6, 'total_entities': 6},
    }
    with warnings.catch_warnings():
        warnings.simplefilter('error', FutureWarning)
        updated, added_count = update_parent_child_master(master_df, [brand_new_pair])
    assert added_count == 0  # 既存レコードの更新
    row = updated[updated['Child'] == 'B1'].iloc[0]
    assert row['Deleted Entities'] == 'n/a'
    assert row['Added Entities'] == 6


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


def test_load_parent_child_master_finds_data_sheet_when_first_sheet_has_no_child_column(tmp_path):
    """save_master_to_bytes() が出力する台帳（Summaryシートが先頭）を再アップロード
    しても、Child/Parent 列を持つシート（Diff List）を自動で見つけて読み込める。

    実データ（ME24-9001-0_ZM00_405.xlsx）で「必須カラム 'Child' が見つかりません」
    と誤って失敗していた不具合の回帰テスト: 先頭シート（Summary）を無条件に読んで
    いたため、Child/Parent 列を持つ実データシート（Diff List）が無視されていた。
    """
    path = tmp_path / "master.xlsx"
    with pd.ExcelWriter(path, engine='xlsxwriter') as writer:
        # Summary シート（Child/Parent 列を持たない、統計情報のみ）を先頭に作成
        pd.DataFrame({'エンティティ統計': ['削除図形 総数'], 'Unnamed: 1': [10]}).to_excel(
            writer, sheet_name='Summary', index=False)
        # Diff List シート（実データ）を2番目に作成
        pd.DataFrame({'Child': ['B1'], 'Parent': ['A1']}).to_excel(
            writer, sheet_name='Diff List', index=False)

    df, error = load_parent_child_master(str(path))
    assert error is None
    assert df is not None
    assert list(df['Child']) == ['B1']
    assert list(df['Parent']) == ['A1']


def test_save_master_to_bytes_round_trip_reloads_correctly(tmp_path):
    """save_master_to_bytes() の出力をそのまま load_parent_child_master() で
    再読み込みできる（エクスポート→再アップロードの往復を保証する）。"""
    master_df = create_empty_master_df()
    master_df.loc[0] = {
        'Child': 'B1', 'Parent': 'A1', 'Relation': '流用',
        'Title': None, 'Subtitle': None, 'Recorded Date': None, 'Note': None,
        'Deleted Entities': 1, 'Added Entities': 2, 'Diff Entities': 3,
        'Unchanged Entities': 4, 'Total Entities': 5,
    }
    data = save_master_to_bytes(master_df, pairs=[], mode='auto', total_drawings_count=1)

    path = tmp_path / "roundtrip.xlsx"
    path.write_bytes(data)

    df, error = load_parent_child_master(str(path))
    assert error is None
    assert df is not None
    assert list(df['Child']) == ['B1']
    assert list(df['Parent']) == ['A1']


def test_uploaded_master_merges_correctly_across_all_pairing_modes(tmp_path):
    """Step0でアップロードした台帳（Summary+Diff List形式）が、Step1のどの
    ペアリング方式（Type A/B の RevUp・流用、Type C のペアリスト）で得られた
    ペアとも正しく合流する（新規追加は重複なく、既存行は上書き更新される）。

    dev-workflow スキルの選択肢組み合わせ表で「台帳アップロード × Step1モード」を
    影響あり→要確認と判定した組み合わせの回帰テスト。update_parent_child_master()
    は pairs のスキーマ（pairing.py で全モード共通と規定）のみに依存し mode 分岐を
    持たないため構造的には安全なはずだが、実際にアップロード経由で読み込んだ
    DataFrame に対して確認する。
    """
    # Step0でアップロードされる形式（Summaryシートが先頭）の台帳を用意
    path = tmp_path / "uploaded_master.xlsx"
    with pd.ExcelWriter(path, engine='xlsxwriter') as writer:
        pd.DataFrame({'エンティティ統計': ['削除図形 総数'], 'Unnamed: 1': [0]}).to_excel(
            writer, sheet_name='Summary', index=False)
        pd.DataFrame({'Child': ['EXIST-CHILD'], 'Parent': ['EXIST-PARENT'], 'Relation': ['流用']}).to_excel(
            writer, sheet_name='Diff List', index=False)

    master_df, error = load_parent_child_master(str(path))
    assert error is None

    # Type A/B の RevUp・流用、Type C のペアリスト、それぞれ新規追加のケース
    for relation in ('RevUp', '流用', 'ペアリスト'):
        pair = {
            'main_drawing': f'NEW-{relation}', 'source_drawing': f'OLD-{relation}',
            'relation': relation, 'title': 'T', 'subtitle': 'S',
        }
        master_df, added_count = update_parent_child_master(master_df, [pair])
        assert added_count == 1, f"{relation} ペアが新規追加されなかった"

    assert len(master_df) == 4  # 既存1件 + 新規3件

    # 既存行の更新（Type C のペアリストで同じ Child/Parent が再検出されたケース）
    update_pair = {
        'main_drawing': 'EXIST-CHILD', 'source_drawing': 'EXIST-PARENT',
        'relation': 'ペアリスト', 'title': 'T2', 'subtitle': 'S2',
    }
    master_df, added_count = update_parent_child_master(master_df, [update_pair])
    assert added_count == 0, "既存行が更新ではなく新規追加されてしまった（重複）"
    assert len(master_df) == 4  # 行数は増えない

    match = master_df[(master_df['Child'] == 'EXIST-CHILD') & (master_df['Parent'] == 'EXIST-PARENT')]
    assert len(match) == 1
    # Relation は「新しい値+-changed」で記録される（既存の
    # test_update_parent_child_master_existing_record_relation_changed_suffix と同じ仕様）
    assert match.iloc[0]['Relation'] == 'ペアリスト-changed'


def test_load_parent_child_master_no_matching_sheet_returns_error(tmp_path):
    """どのシートにも Child/Parent 列が無い場合は、従来どおりエラーを返す
    （先頭シートを対象にエラーメッセージを出す後方互換の挙動）。"""
    path = tmp_path / "master.xlsx"
    with pd.ExcelWriter(path, engine='xlsxwriter') as writer:
        pd.DataFrame({'foo': [1]}).to_excel(writer, sheet_name='Sheet1', index=False)
        pd.DataFrame({'bar': [2]}).to_excel(writer, sheet_name='Sheet2', index=False)

    df, error = load_parent_child_master(str(path))
    assert df is None
    assert 'Child' in error


# --- save_master_to_bytes ---

def test_save_master_to_bytes_returns_nonempty_excel():
    master_df = create_empty_master_df()
    data = save_master_to_bytes(master_df, pairs=[], mode='auto', total_drawings_count=0)
    assert isinstance(data, bytes) and len(data) > 0


def test_save_master_to_bytes_sorts_diff_list_by_child():
    """Diff List シートは Child 列の昇順（ABC順）でソートされる。"""
    master_df = create_empty_master_df()
    for i, child in enumerate(['EE3273-608-32B', 'EE3273-608-24B', 'DE5313-008-02A']):
        master_df.loc[i] = {
            'Child': child, 'Parent': 'none', 'Relation': '完全新規図面',
            'Title': None, 'Subtitle': None, 'Recorded Date': None, 'Note': None,
            'Deleted Entities': 'n/a', 'Added Entities': 1, 'Diff Entities': 'n/a',
            'Unchanged Entities': 'n/a', 'Total Entities': 1,
        }

    data = save_master_to_bytes(master_df, pairs=[], mode='pair_list', total_drawings_count=3)
    diff_list_df = pd.read_excel(pd.io.common.BytesIO(data), sheet_name='Diff List')
    assert list(diff_list_df['Child']) == ['DE5313-008-02A', 'EE3273-608-24B', 'EE3273-608-32B']
    # 元の master_df は変更されない（呼び出し元の順序に副作用を与えない）
    assert list(master_df['Child']) == ['EE3273-608-32B', 'EE3273-608-24B', 'DE5313-008-02A']


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
