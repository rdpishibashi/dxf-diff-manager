"""
model.common_utils（UI 非依存）のユニットテスト。

streamlit に依存しないため app.py をインポートせず、コアを直接検証する
（tests/unit/test_pairing.py と同じ方針）。

実行:
    cd DXF-diff-manager
    python -m tests.unit.test_common_utils
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from model.common_utils import is_drawing_number_filename


# --- is_drawing_number_filename ---

def test_accepts_long_format():
    """長フォーマット（aannnn-nnn-nna）を受け付ける。"""
    assert is_drawing_number_filename('EE1234-567-89A.dxf')


def test_accepts_short_format():
    """短フォーマット（aannnn-nnna）を受け付ける。"""
    assert is_drawing_number_filename('EE1234-567A.dxf')


def test_rejects_lowercase():
    """英字は大文字限定（小文字は不一致）。"""
    assert not is_drawing_number_filename('ee1234-567-89a.dxf')
    assert not is_drawing_number_filename('ee1234-567a.dxf')


def test_rejects_non_matching_filename():
    assert not is_drawing_number_filename('random_file.dxf')
    assert not is_drawing_number_filename('図面サンプル.dxf')


def test_extension_case_and_choice_do_not_affect_match():
    """拡張子の大小文字・種類は判定（ステム部分の照合）に影響しない。"""
    assert is_drawing_number_filename('EE1234-567-89A.DXF')
    assert is_drawing_number_filename('EE1234-567-89A.dwg')  # ステムだけ見るため拡張子自体は問わない


def test_rejects_extra_characters_around_valid_pattern():
    """図番フォーマットの前後に余分な文字が付くと不一致（完全一致=fullmatch）。"""
    assert not is_drawing_number_filename('copy_of_EE1234-567-89A.dxf')
    assert not is_drawing_number_filename('EE1234-567-89A_old.dxf')
    assert not is_drawing_number_filename('EE1234-567-89A(1).dxf')


def test_rejects_wrong_digit_counts():
    assert not is_drawing_number_filename('EE123-567-89A.dxf')    # 数字4桁の部分が3桁
    assert not is_drawing_number_filename('EE1234-56-89A.dxf')    # 数字3桁の部分が2桁
    assert not is_drawing_number_filename('EE1234-567-8A.dxf')    # 末尾数字2桁の部分が1桁


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
