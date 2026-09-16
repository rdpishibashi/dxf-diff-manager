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

import ezdxf

from model.common_utils import is_drawing_number_filename, is_invisible


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


# --- is_invisible ---

def test_visible_entity_on_normal_layer_is_not_invisible():
    """通常レイヤー上の、invisible属性が立っていないエンティティは非表示ではない。"""
    doc = ezdxf.new()
    msp = doc.modelspace()
    e = msp.add_text('X', dxfattribs={'layer': '0'})
    assert is_invisible(e) is False


def test_entity_with_invisible_attribute_is_invisible():
    """エンティティ自身のinvisible属性（グループコード60）が立っていれば非表示。"""
    doc = ezdxf.new()
    msp = doc.modelspace()
    e = msp.add_text('X', dxfattribs={'invisible': 1})
    assert is_invisible(e) is True


def test_entity_on_off_layer_is_invisible():
    """エンティティ自身のinvisible属性が立っていなくても、所属レイヤーがオフなら
    非表示（2026-09-16、レイヤー単位の非表示状態チェックを追加）。"""
    doc = ezdxf.new()
    doc.layers.add('HIDDEN_LAYER')
    doc.layers.get('HIDDEN_LAYER').off()
    msp = doc.modelspace()
    e = msp.add_text('X', dxfattribs={'layer': 'HIDDEN_LAYER'})
    assert is_invisible(e) is True


def test_entity_on_frozen_layer_is_invisible():
    """所属レイヤーがフリーズされている場合も非表示として扱う。"""
    doc = ezdxf.new()
    doc.layers.add('FROZEN_LAYER')
    doc.layers.get('FROZEN_LAYER').freeze()
    msp = doc.modelspace()
    e = msp.add_text('X', dxfattribs={'layer': 'FROZEN_LAYER'})
    assert is_invisible(e) is True


def test_virtual_entity_from_insert_on_off_layer_is_invisible():
    """INSERTをvirtual_entities()で展開した仮想エンティティも、展開後の
    レイヤー参照でオフ/フリーズ状態を正しく判定できる（実データ
    EE3273-039-90B.dxfで確認した「旧タイトルブロック一式が専用レイヤーごと
    オフ/フリーズされている」ケースの再現）。"""
    doc = ezdxf.new()
    doc.layers.add('OLD_TITLEBLOCK')
    doc.layers.get('OLD_TITLEBLOCK').off()
    doc.layers.get('OLD_TITLEBLOCK').freeze()

    block = doc.blocks.new('OLD_TB_BLOCK')
    block.add_text('EE0000-000-00A', dxfattribs={'layer': 'OLD_TITLEBLOCK'})

    msp = doc.modelspace()
    insert = msp.add_blockref('OLD_TB_BLOCK', insert=(0, 0))
    virtuals = list(insert.virtual_entities())
    assert len(virtuals) == 1
    assert is_invisible(virtuals[0]) is True


def test_missing_layer_or_doc_falls_back_to_not_invisible():
    """レイヤーテーブルに存在しない・.docが取得できない等の異常系は、
    誤って全除外にならないよう「非表示ではない」側にフォールバックする。"""
    doc = ezdxf.new()
    msp = doc.modelspace()
    e = msp.add_text('X', dxfattribs={'layer': '0'})
    # レイヤー名を、レイヤーテーブルに存在しない名前に強制的に書き換える
    e.dxf.layer = 'NO_SUCH_LAYER_IN_TABLE'
    assert is_invisible(e) is False


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
