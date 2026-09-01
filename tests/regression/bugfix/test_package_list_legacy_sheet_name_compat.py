"""
図面管理台帳の "Drawing List" シートが "Package List" にリネームされた（2026-09）際、
旧名で出力された既存台帳を再アップロードしても読めることを保証する回帰テスト。

不具合の識別子: なし（改名に伴う後方互換の予防的対応。2026-08 の "Diff List" →
"Master" 改名で、シート名ハードコード依存のコンシューマーが黙って除外されかけた
既知の失敗パターン〈project_master_sheet_rename_2026_08_30〉と同じ危険を避けるため、
今回は改名と同時に後方互換読み込みを実装した）。

以前どう壊れていたか（もし後方互換を入れなかった場合）: load_drawing_list() が
新シート名 "Package List" だけを探すため、旧名 "Drawing List" しか持たない
既存台帳を再アップロードすると、実際にはデータがあるのに空の DataFrame が返り、
既存の Package List 記録がすべて失われる（新規行のみ扱いになり、既存 Child
Drawing Number の重複防止・保持が効かなくなる）。

修正後に保証したいこと: 新名（Package List）・旧名（Drawing List）のどちらの
シート名を持つ台帳でも同じ内容が読み込める。

実行:
    cd DXF-diff-manager
    python -m tests.regression.bugfix.test_package_list_legacy_sheet_name_compat
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import pandas as pd

from model.master_ledger import load_drawing_list, DRAWING_LIST_SHEET_NAME


def _write_ledger(path, list_sheet_name):
    with pd.ExcelWriter(path, engine='xlsxwriter') as writer:
        pd.DataFrame({'Child': ['C1'], 'Parent': ['P1']}).to_excel(
            writer, sheet_name='Master', index=False)
        pd.DataFrame({
            'Sashiban': ['AA11-1111-1'], 'Module': ['ZM00'], 'Side': ['405'],
            'Child Drawing Number': ['C1'], 'Parent Drawing Number': ['P1'],
            'Title': ['T'], 'Subtitle': ['S'], 'Recorded Date': ['2026-07-01'],
        }).to_excel(writer, sheet_name=list_sheet_name, index=False)


def test_reads_current_package_list_sheet_name(tmp_path):
    assert DRAWING_LIST_SHEET_NAME == "Package List"
    path = tmp_path / "current_format.xlsx"
    _write_ledger(str(path), "Package List")

    df, error = load_drawing_list(str(path))
    assert error is None
    assert list(df['Child Drawing Number']) == ['C1']


def test_reads_legacy_drawing_list_sheet_name(tmp_path):
    """旧名 "Drawing List" で出力された既存台帳の再アップロードでも読める。"""
    path = tmp_path / "legacy_format.xlsx"
    _write_ledger(str(path), "Drawing List")

    df, error = load_drawing_list(str(path))
    assert error is None
    assert list(df['Child Drawing Number']) == ['C1']
    assert list(df['Sashiban']) == ['AA11-1111-1']


def test_current_name_takes_priority_when_both_present(tmp_path):
    """新旧両方のシートが存在する場合（通常は起こらないが）、新名を優先する。"""
    path = tmp_path / "both_sheets.xlsx"
    with pd.ExcelWriter(str(path), engine='xlsxwriter') as writer:
        pd.DataFrame({'Child': ['C1'], 'Parent': ['P1']}).to_excel(
            writer, sheet_name='Master', index=False)
        pd.DataFrame({
            'Sashiban': ['OLD'], 'Module': ['OLD'], 'Side': ['OLD'],
            'Child Drawing Number': ['OLD-C1'], 'Parent Drawing Number': ['OLD-P1'],
            'Title': [None], 'Subtitle': [None], 'Recorded Date': [None],
        }).to_excel(writer, sheet_name='Drawing List', index=False)
        pd.DataFrame({
            'Sashiban': ['NEW'], 'Module': ['NEW'], 'Side': ['NEW'],
            'Child Drawing Number': ['NEW-C1'], 'Parent Drawing Number': ['NEW-P1'],
            'Title': [None], 'Subtitle': [None], 'Recorded Date': [None],
        }).to_excel(writer, sheet_name='Package List', index=False)

    df, error = load_drawing_list(str(path))
    assert error is None
    assert list(df['Child Drawing Number']) == ['NEW-C1']


def _run_all():
    import tempfile
    from pathlib import Path
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    for t in tests:
        try:
            with tempfile.TemporaryDirectory() as d:
                t(Path(d))
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append(t.__name__)
            print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
