"""
model.ref_designator_judge（機器符号候補判定）のユニットテスト。

DXF-extract-labels の model/ref_designator.py::is_ref_designator_label() を
最小移植したもの（model/ref_designator_judge.py 参照）。判定パターン自体は
model/ref_designator_patterns.py（byte-identical コピー）に委譲する。

守る対象:
    - 基本的な機器符号パターン（R10・C1(2.2K) 等）が候補として判定される
    - 単一英字・末尾+/- の例外規則（primaryと同一）
    - DXF-diff-manager の extract_labels.py は NFKC 正規化をしないため、
      全角文字列を渡しても本モジュール内部で正規化してから判定すること
      （2026-09-16、region_detector.py の normalize_width 抜け漏れ事例と
      同じ失敗モードの予防）

実行:
    cd DXF-diff-manager
    python -m tests.unit.test_ref_designator_judge
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from model.ref_designator_judge import is_ref_designator_label


def test_basic_letter_digit_pattern_is_candidate():
    assert is_ref_designator_label('R10') is True
    assert is_ref_designator_label('C1') is True


def test_label_with_bracket_judged_by_part_before_bracket():
    assert is_ref_designator_label('R10(2.2K)') is True


def test_single_uppercase_letter_is_excluded():
    """英大文字1字だけは機器符号ではない（図形枠外の位置記号等）。"""
    assert is_ref_designator_label('H') is False
    assert is_ref_designator_label('V') is False


def test_trailing_sign_is_excluded():
    """末尾が+/-で終わる文字列は電源端子表記であって単体の機器符号ではない。"""
    assert is_ref_designator_label('N24-') is False
    assert is_ref_designator_label('L1+') is False


def test_common_nouns_are_not_excluded():
    """GND・SYSTEM等の普通名詞は、3パターンのいずれかに一致すれば候補として扱う
    （2026-09-14ユーザー確認、EXCLUSION_EXACT_CATEGORIESは復元しない方針）。"""
    assert is_ref_designator_label('GND') is True
    assert is_ref_designator_label('SYSTEM') is True


def test_non_matching_label_is_not_candidate():
    assert is_ref_designator_label('タイトル') is False
    assert is_ref_designator_label('LABEL_A') is False  # アンダースコアを含むため不一致


def test_fullwidth_label_is_normalized_before_judgment():
    """DXF-diff-manager の extract_labels.py はNFKC正規化をしないため、
    全角文字列がそのまま渡ってきても本モジュール内部で正規化してから判定する
    （正規化を怠ると全角ラベルがすり抜ける——region_detector.pyでの
    normalize_width欠落と同じ失敗モード）。"""
    # 全角の 'ＲＫ１０' は NFKC 正規化すると 'RK10' になり、letters_digits パターンに一致する
    assert is_ref_designator_label('ＲＫ１０') is True


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append(t.__name__); print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
