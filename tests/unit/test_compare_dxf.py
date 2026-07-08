"""
utils.compare_dxf の EntityExpander に関するユニットテスト（UI 非依存）。

off/frozen（非表示）レイヤー上のエンティティが、ビジュアル差分の抽出対象から
除外されることを検証する。重なった旧タイトルブロック・改訂履歴メモ等が
off/frozen レイヤーに残っている DXF で、新旧同一の不可視テキストが UNCHANGED
として差分DXFに描画される不具合の回帰テスト（実データ
EE2505-611-79B_vs_79A で確認: ブロック JZB_0004 の MTEXT 'EE2505-611-57B' 等が
off+frozen レイヤー上にあった）。

実行:
    cd DXF-diff-manager
    python -m tests.unit.test_compare_dxf
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ezdxf

from utils.compare_dxf import (
    ToleranceConfig, CoordinateTransformer, EntityExpander,
    SignatureGenerator, DiffAnalyzer,
)


def _make_expander():
    tol = ToleranceConfig(0.01)
    transformer = CoordinateTransformer(tol, debug=False)
    return EntityExpander(transformer, debug=False)


def _diff_counts(doc_old, doc_new):
    """2ドキュメントを比較し (deleted, added, unchanged) の署名数を返す。"""
    tol = ToleranceConfig(0.01)
    tr = CoordinateTransformer(tol, debug=False)
    da = DiffAnalyzer(SignatureGenerator(tr, debug=False), debug=False)
    ea, _, _, _ = da.extract_entities_from_doc(doc_old, "A", EntityExpander(tr))
    eb, _, _, _ = da.extract_entities_from_doc(doc_new, "B", EntityExpander(tr))
    sa, sb = set(ea), set(eb)
    return len(sa - sb), len(sb - sa), len(sa & sb)


def _texts_from_expansion(doc):
    expander = _make_expander()
    entities = expander.expand_insert_entities(doc, "test")
    return {e.get('text_content') for e in entities if e.get('text_content')}


def _prepare_doc():
    doc = ezdxf.new()
    # 可視レイヤー
    doc.layers.add('VISIBLE')
    # off レイヤー
    off = doc.layers.add('HIDDEN_OFF')
    off.off()
    # frozen レイヤー
    frozen = doc.layers.add('HIDDEN_FROZEN')
    frozen.freeze()
    return doc


def test_direct_modelspace_entities_on_hidden_layers_excluded():
    """modelspace 直下の off/frozen レイヤー上テキストは除外される。"""
    doc = _prepare_doc()
    msp = doc.modelspace()
    msp.add_text('VISIBLE_TEXT', dxfattribs={'layer': 'VISIBLE'})
    msp.add_text('OFF_TEXT', dxfattribs={'layer': 'HIDDEN_OFF'})
    msp.add_text('FROZEN_TEXT', dxfattribs={'layer': 'HIDDEN_FROZEN'})

    texts = _texts_from_expansion(doc)
    assert 'VISIBLE_TEXT' in texts
    assert 'OFF_TEXT' not in texts
    assert 'FROZEN_TEXT' not in texts


def test_block_entities_on_hidden_layers_excluded():
    """ブロック定義内の off/frozen レイヤー上テキストは、可視 INSERT 経由でも除外される。

    実データの不具合（ブロック JZB_0004 の MTEXT が off+frozen レイヤーにあり
    UNCHANGED に描画された）に対応する中核ケース。
    """
    doc = _prepare_doc()
    blk = doc.blocks.new('BLK')
    blk.add_text('BLK_VISIBLE', dxfattribs={'layer': 'VISIBLE'})
    blk.add_text('BLK_OFF', dxfattribs={'layer': 'HIDDEN_OFF'})
    blk.add_text('BLK_FROZEN', dxfattribs={'layer': 'HIDDEN_FROZEN'})
    # 継承レイヤー '0'（INSERT のレイヤーを継承）→ INSERT が可視なら表示される
    blk.add_text('BLK_LAYER0', dxfattribs={'layer': '0'})

    msp = doc.modelspace()
    msp.add_blockref('BLK', (0, 0), dxfattribs={'layer': 'VISIBLE'})

    texts = _texts_from_expansion(doc)
    assert 'BLK_VISIBLE' in texts
    assert 'BLK_LAYER0' in texts
    assert 'BLK_OFF' not in texts
    assert 'BLK_FROZEN' not in texts


def test_insert_on_hidden_layer_excludes_all_contents():
    """INSERT 自身が off/frozen レイヤーにある場合、参照全体が除外される。"""
    doc = _prepare_doc()
    blk = doc.blocks.new('BLK2')
    blk.add_text('INSIDE1', dxfattribs={'layer': 'VISIBLE'})
    blk.add_text('INSIDE2', dxfattribs={'layer': '0'})

    msp = doc.modelspace()
    # INSERT を off レイヤーに配置 → 中身は全て非表示
    msp.add_blockref('BLK2', (0, 0), dxfattribs={'layer': 'HIDDEN_OFF'})

    texts = _texts_from_expansion(doc)
    assert 'INSIDE1' not in texts
    assert 'INSIDE2' not in texts


def test_visible_content_preserved():
    """通常の可視レイヤー・レイヤー'0' の図形は従来どおり抽出される（過剰除外がない）。"""
    doc = _prepare_doc()
    msp = doc.modelspace()
    msp.add_text('DEFAULT_LAYER0', dxfattribs={'layer': '0'})
    msp.add_text('ON_VISIBLE', dxfattribs={'layer': 'VISIBLE'})

    texts = _texts_from_expansion(doc)
    assert 'DEFAULT_LAYER0' in texts
    assert 'ON_VISIBLE' in texts


# --- MTEXT フォーマットコードの署名正規化 ---

def test_mtext_differing_format_codes_treated_as_unchanged():
    """同じ見た目・同じ位置の MTEXT が、\\W 幅係数・\\T 文字間隔コードだけ異なる場合に
    UNCHANGED と判定される（DELETED+ADDED の偽差分を出さない）。

    実データ EE6588-405C_vs_405B で、改訂時に再計算された \\W/\\T の僅差により
    同一ラベル約313件が DELETED+ADDED に誤判定された不具合の回帰テスト。
    diff_labels.xlsx は plain_mtext でこれらを除去済みのため変化なしと出るのに、
    差分DXFだけ大量の偽差分が出ていた。
    """
    old = ezdxf.new()
    old.modelspace().add_mtext('EE6588-405', dxfattribs={'insert': (10, 20)}).text = \
        r'\A1;\W0.892749;\T0.892749;EE6588-405'
    new = ezdxf.new()
    new.modelspace().add_mtext('EE6588-405', dxfattribs={'insert': (10, 20)}).text = \
        r'\A1;\W0.883176;\T0.883176;EE6588-405'

    deleted, added, unchanged = _diff_counts(old, new)
    assert deleted == 0, f"偽の DELETED が出た: {deleted}"
    assert added == 0, f"偽の ADDED が出た: {added}"
    assert unchanged == 1


def test_mtext_genuinely_different_text_is_detected():
    """フォーマットコード除去後の本文が異なる MTEXT は、正しく差分として検出される
    （過剰な同一視で本物の変更を見逃さない）。"""
    old = ezdxf.new()
    old.modelspace().add_mtext('x', dxfattribs={'insert': (10, 20)}).text = \
        r'\A1;\W0.9;\T0.9;EE6588-405B'
    new = ezdxf.new()
    new.modelspace().add_mtext('x', dxfattribs={'insert': (10, 20)}).text = \
        r'\A1;\W0.9;\T0.9;EE6588-405C'

    deleted, added, unchanged = _diff_counts(old, new)
    assert deleted == 1
    assert added == 1


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
