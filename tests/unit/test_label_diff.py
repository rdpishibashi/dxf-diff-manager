"""
model.label_diff（UI 非依存）のユニットテスト。

streamlit に依存しないため app.py をインポートせず、コアを直接検証する
（tests/unit/test_pairing.py と同じ方針）。

実行:
    cd DXF-diff-manager
    python -m tests.unit.test_label_diff
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from model.label_diff import (
    group_labels_by_coordinate,
    round_labels_with_coordinates,
    find_label_change_pairs,
    find_label_change_pairs_ignoring_coordinates,
    reclassify_moved_labels,
    build_diff_labels_workbook,
)


def _change_rows_for(new_labels, old_labels, tolerance=0.01):
    """(label, x, y) のリスト2つから change_rows/unchanged_entries を計算するヘルパー。"""
    rounded_new = round_labels_with_coordinates(new_labels, tolerance)
    rounded_old = round_labels_with_coordinates(old_labels, tolerance)
    grouped_new = group_labels_by_coordinate(rounded_new)
    grouped_old = group_labels_by_coordinate(rounded_old)
    return find_label_change_pairs(grouped_new, grouped_old)


# --- reclassify_moved_labels: 基本ケース ---

def test_moved_block_reclassified_as_unchanged():
    """回路ブロックがまるごと別座標に移動した場合、削除+追加ではなく変更なしになる。"""
    old_labels = [('R10', 0, 0), ('C1', 0, 0)]
    new_labels = [('R10', 100, 100), ('C1', 100, 100)]
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    # reclassify前は削除2件+追加2件のはず
    assert len(change_rows) == 4

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    assert remaining == []
    moved = [e for e in unchanged if e not in unchanged_entries]
    assert len(moved) == 2
    assert {e['label'] for e in moved} == {'R10', 'C1'}
    for e in moved:
        assert e['coordinate'] == (100, 100)  # 新座標を採用


def test_unmatched_count_partially_remains_as_change():
    """削除件数と追加件数が一致しない分は変更候補として残る。"""
    old_labels = [('R10', 0, 0), ('R10', 1, 1), ('R10', 2, 2)]  # 3件削除
    new_labels = [('R10', 100, 100)]  # 1件追加
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    # 1件だけ移動とみなされ、残り2件は削除のまま
    moved = [e for e in unchanged if e not in unchanged_entries]
    assert len(moved) == 1
    assert len(remaining) == 2
    assert all(r['New Label'] is None for r in remaining)  # 残りは全て削除


def test_star_labels_never_reclassified():
    """「☆」を含むラベルは件数が一致しても常に変更候補として残る。"""
    old_labels = [('☆注記1', 0, 0)]
    new_labels = [('☆注記1', 100, 100)]
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    assert len(remaining) == 2  # 削除1件・追加1件のまま
    moved = [e for e in unchanged if e not in unchanged_entries]
    assert moved == []


def test_rename_at_same_coordinate_not_affected():
    """同一座標での名称変更（Old/New両方が存在する行）は再分類の対象外。"""
    old_labels = [('R10', 0, 0)]
    new_labels = [('R11', 0, 0)]
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    assert len(change_rows) == 1
    assert change_rows[0]['Old Label'] == 'R10'
    assert change_rows[0]['New Label'] == 'R11'

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    # Old/New どちらも None でないため対象外、そのまま残る
    assert remaining == change_rows
    assert unchanged == unchanged_entries


def test_unrelated_deletion_and_addition_different_labels_not_matched():
    """異なるラベル文字列同士は誤って対応付けられない。"""
    old_labels = [('R10', 0, 0)]
    new_labels = [('C1', 100, 100)]
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    assert len(remaining) == 2  # R10削除・C1追加はそれぞれ独立して残る
    moved = [e for e in unchanged if e not in unchanged_entries]
    assert moved == []


def test_ignore_moved_labels_disabled_by_default_via_compute_label_differences(tmp_path):
    """compute_label_differences() は ignore_moved_labels=False がデフォルト
    （既存呼び出し元の挙動を変えない）ことを、シグネチャのデフォルト値で確認する。"""
    import inspect
    from model.label_diff import compute_label_differences
    sig = inspect.signature(compute_label_differences)
    assert sig.parameters['ignore_moved_labels'].default is False


def test_label_only_disabled_by_default_via_compute_label_differences():
    """compute_label_differences() は label_only=False がデフォルト
    （既存呼び出し元・DXF-visual-diffの挙動を変えない）。"""
    import inspect
    from model.label_diff import compute_label_differences
    sig = inspect.signature(compute_label_differences)
    assert sig.parameters['label_only'].default is False


# --- find_label_change_pairs_ignoring_coordinates: ラベルのみ比較モード ---

def test_label_only_moved_block_becomes_unchanged_without_ignore_moved_labels():
    """座標を無視するため、reclassify_moved_labels を使わなくても
    「移動しただけ」のラベルは自動的に変更なし扱いになる。"""
    old_labels = [('R10', 0, 0), ('C1', 0, 0)]
    new_labels = [('R10', 100, 100), ('C1', 100, 100)]
    change_rows, unchanged_entries = find_label_change_pairs_ignoring_coordinates(new_labels, old_labels)

    assert change_rows == []
    assert {e['label'] for e in unchanged_entries} == {'R10', 'C1'}
    assert all(e['coordinate'] is None for e in unchanged_entries)


def test_label_only_rename_at_same_coordinate_becomes_delete_plus_add():
    """座標を見ないため、同一座標での「名称変更」は検出できず、
    削除1件・追加1件として出力される（名称変更ペアは発生しない）。"""
    old_labels = [('R10', 0, 0)]
    new_labels = [('R11', 0, 0)]
    change_rows, unchanged_entries = find_label_change_pairs_ignoring_coordinates(new_labels, old_labels)

    assert len(change_rows) == 2
    assert {(r['Old Label'], r['New Label']) for r in change_rows} == {
        ('R10', None), (None, 'R11'),
    }
    assert unchanged_entries == []


def test_label_only_duplicate_labels_partial_match():
    """同一ラベルが旧2個・新3個ある場合、2個は変更なし、1個だけ追加として残る。"""
    old_labels = [('R10', 0, 0), ('R10', 1, 1)]
    new_labels = [('R10', 100, 100), ('R10', 200, 200), ('R10', 300, 300)]
    change_rows, unchanged_entries = find_label_change_pairs_ignoring_coordinates(new_labels, old_labels)

    assert len(change_rows) == 1
    assert change_rows[0] == {'X': None, 'Y': None, 'Old Label': None, 'New Label': 'R10'}
    assert len(unchanged_entries) == 1
    assert unchanged_entries[0]['count'] == 2


def test_label_only_star_label_absorbed_as_moved_unlike_coordinate_mode():
    """座標モードの reclassify_moved_labels は「☆」ラベルを常に変更候補として
    残すが、ラベルのみ比較モードにはこの例外が無い（座標そのものを見ないため、
    「移動」という概念自体が発生しない）。これは仕様として受容する差異。"""
    old_labels = [('☆注記1', 0, 0)]
    new_labels = [('☆注記1', 100, 100)]
    change_rows, unchanged_entries = find_label_change_pairs_ignoring_coordinates(new_labels, old_labels)

    assert change_rows == []
    assert len(unchanged_entries) == 1
    assert unchanged_entries[0]['label'] == '☆注記1'


def test_label_only_x_y_always_none():
    """ラベルのみ比較モードの change_rows は X/Y を常に None にする
    （呼び出し元が Excel 出力時に空欄として扱う。D2）。"""
    old_labels = [('R10', 0, 0)]
    new_labels = []
    change_rows, _ = find_label_change_pairs_ignoring_coordinates(new_labels, old_labels)
    assert change_rows == [{'X': None, 'Y': None, 'Old Label': 'R10', 'New Label': None}]


# --- build_diff_labels_workbook: 機器符号候補列（include_ref_designator_column） ---

def test_build_diff_labels_workbook_without_ref_designator_column_unchanged():
    """include_ref_designator_column を省略した場合、機器符号候補列は出力されない
    （DXF-visual-diff 側の既存呼び出しの挙動を変えないことを保証する）。"""
    import io
    import pandas as pd

    sheets = [{
        'sheet_name': 'EE1234-567A',
        'rows': [{'X': 0, 'Y': 0, 'Old Label': None, 'New Label': 'R10'}],
        'old_label_name': 'Old: EE1234-567',
        'new_label_name': 'New: EE1234-567A',
    }]
    data = build_diff_labels_workbook(sheets)
    xl = pd.ExcelFile(io.BytesIO(data))
    df = pd.read_excel(xl, sheet_name='EE1234-567A')
    assert '機器符号候補' not in df.columns
    assert list(df.columns) == ['X', 'Y', 'Old: EE1234-567', 'New: EE1234-567A']


def test_build_diff_labels_workbook_with_ref_designator_column():
    """include_ref_designator_column=True の場合、ペアシート先頭に「機器符号候補」列、
    Summaryシートに「機器符号候補数」列が出力される。値は呼び出し元が
    rows/summary_data に埋め込んだものをそのまま使う（判定ロジック自体は
    label_diff.py に持たせない設計）。"""
    import io
    import pandas as pd

    sheets = [{
        'sheet_name': 'EE1234-567A',
        'rows': [
            {'機器符号候補': 'Y', 'X': 0, 'Y': 0, 'Old Label': None, 'New Label': 'R10'},
            {'機器符号候補': None, 'X': 1, 'Y': 1, 'Old Label': None, 'New Label': 'タイトル'},
        ],
        'old_label_name': 'Old: EE1234-567',
        'new_label_name': 'New: EE1234-567A',
    }]
    summary_data = [{
        '図番': 'EE1234-567A', '流用元図番': 'EE1234-567', '追加ラベル数': 2,
        '削除ラベル数': 0, '変更ラベル数': 0, '機器符号候補数': 1,
        'タイトル': 'T', 'サブタイトル': 'S',
    }]
    data = build_diff_labels_workbook(sheets, summary_data=summary_data,
                                       include_ref_designator_column=True)
    xl = pd.ExcelFile(io.BytesIO(data))

    df = pd.read_excel(xl, sheet_name='EE1234-567A')
    assert list(df.columns) == ['機器符号候補', 'X', 'Y', 'Old: EE1234-567', 'New: EE1234-567A']
    assert df.loc[df['New: EE1234-567A'] == 'R10', '機器符号候補'].iloc[0] == 'Y'
    assert pd.isna(df.loc[df['New: EE1234-567A'] == 'タイトル', '機器符号候補'].iloc[0])

    summary_df = pd.read_excel(xl, sheet_name='Summary')
    assert '機器符号候補数' in summary_df.columns
    assert summary_df.iloc[0]['機器符号候補数'] == 1
    # タイトル・サブタイトルの位置が機器符号候補数の後ろにずれていないか確認
    assert summary_df.iloc[0]['タイトル'] == 'T'
    assert summary_df.iloc[0]['サブタイトル'] == 'S'


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
