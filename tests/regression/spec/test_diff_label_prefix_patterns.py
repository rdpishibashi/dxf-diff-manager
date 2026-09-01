"""
config.LabelFilterConfig.DIFF_LABEL_PREFIX_PATTERNS（差分抽出するラベルの先頭文字列）の
仕様テスト。model.label_diff.filter_change_rows_by_patterns() を直接検証する。

受入条件（2026-09、Step4「オプション設定」の config.py 移行に伴い新設）:
    - 正規表現のリストで、Old Label・New Label のいずれかが「先頭一致」すれば
      その change_rows の行を残す。
    - 空リスト（既定）の場合は絞り込みを行わず、全ラベルが対象になる。
    - 名称変更行（Old/New 両方が値を持つ）は、どちらか一方が一致すれば残る。
    - 追加のみ（Old=None）・削除のみ（New=None）の行でも、None に対して
      正規表現マッチを試みず正しく判定できる。
    - 不正な正規表現は re.error を送出する（呼び出し元でエラーメッセージに変換する）。

実行:
    cd DXF-diff-manager
    python -m tests.regression.spec.test_diff_label_prefix_patterns
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from model.label_diff import filter_change_rows_by_patterns


def _row(old, new):
    return {'Coordinate X': 0, 'Coordinate Y': 0, 'Old Label': old, 'New Label': new}


def test_empty_patterns_returns_all_rows_unchanged():
    rows = [_row('R10', None), _row(None, 'C5'), _row('OLD', 'NEW')]
    result = filter_change_rows_by_patterns(rows, [])
    assert result == rows


def test_pattern_matches_old_label_only_row():
    rows = [_row('R10', None), _row('XYZ', None)]
    result = filter_change_rows_by_patterns(rows, [r'R'])
    assert result == [_row('R10', None)]


def test_pattern_matches_new_label_only_row():
    rows = [_row(None, 'R10'), _row(None, 'XYZ')]
    result = filter_change_rows_by_patterns(rows, [r'R'])
    assert result == [_row(None, 'R10')]


def test_pattern_matches_either_side_of_rename_row():
    """名称変更行（Old/New両方あり）は、どちらか一方が先頭一致すれば残る。"""
    rows = [_row('R10', 'R11'), _row('R10', 'NOPREFIX'), _row('NOPREFIX', 'R11')]
    result = filter_change_rows_by_patterns(rows, [r'R'])
    assert result == rows  # 3行とも一方がRで始まるため残る


def test_pattern_excludes_non_matching_rows():
    rows = [_row('R10', None), _row(None, 'XYZ')]
    result = filter_change_rows_by_patterns(rows, [r'R'])
    assert result == [_row('R10', None)]


def test_multiple_patterns_are_ored():
    rows = [_row('R10', None), _row(None, 'C5'), _row(None, 'XYZ')]
    result = filter_change_rows_by_patterns(rows, [r'R', r'C'])
    assert result == [_row('R10', None), _row(None, 'C5')]


def test_none_labels_do_not_match_and_do_not_crash():
    """追加のみ・削除のみの行で、None側に対して正規表現マッチを試みない。"""
    rows = [_row('R10', None), _row(None, 'C5')]
    result = filter_change_rows_by_patterns(rows, [r'R', r'C'])
    assert len(result) == 2  # クラッシュせず、両方ともマッチする側で残る


def test_prefix_match_not_substring_match():
    """re.match は先頭一致であり、部分一致ではない。"""
    rows = [_row('XR10', None)]  # 'R10' で始まらない（先頭は'X'）
    result = filter_change_rows_by_patterns(rows, [r'R10'])
    assert result == []


def test_invalid_regex_raises_re_error():
    import pytest
    with pytest.raises(re.error):
        filter_change_rows_by_patterns([_row('R10', None)], [r'['])  # 閉じ括弧なし


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except Exception as e:
            failures.append(t.__name__)
            print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
