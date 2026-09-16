"""
extract_labels() が、専用レイヤーごとオフ/フリーズされた旧タイトルブロックの
内容（誤った図番・重複した図枠メタ情報）を抽出しないことを保証する回帰テスト。

不具合の識別子: 2026-09-16 ユーザー報告
    「EE3273-039-90B（完全新規図面、1ページ）の diff_labels.xlsx に、
    APPRV/CHECK等の図面情報ラベルが複数、かつ図面上には存在しない図番
    （EE3273-039-90A等）が抽出されている」（実データで確認）。

以前どう壊れていたか:
    common_utils.is_invisible() はエンティティ自身の`invisible`属性
    （グループコード60）のみをチェックしており、エンティティが置かれた
    **レイヤー自体がオフ/フリーズされている**ケースを見逃していた。
    実データ EE3273-039-90B.dxf を調査したところ、旧タイトルブロック一式
    （図番「EE3273-039-90A」・APPRV/CHECK/DATE等のラベルを含むINSERT）が
    `off=True, frozen=True` の専用レイヤーに置かれており、エンティティ
    自身のinvisible属性は立っていなかった（＝従来のis_invisible()では
    検出できなかった）ため、この旧タイトルブロックの内容がそのまま
    extract_labels() の出力に混入していた。

修正後に保証したいこと:
    - オフ/フリーズされたレイヤーに置かれた直接配置エンティティ・INSERT・
      INSERT展開後の仮想エンティティは、いずれも収集対象から除外される。
    - 通常レイヤー（現行タイトルブロック）の内容は引き続き正しく抽出される。

実行:
    cd DXF-diff-manager
    python -m pytest tests/regression/bugfix/test_off_frozen_layer_titleblock_excluded.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf

from model.extract_labels import extract_labels


def _build_dxf_with_hidden_old_titleblock(path):
    """実データ EE3273-039-90B.dxf の構造を模した合成DXFを作成する。

    - 現行タイトルブロック（通常レイヤー）: 図番 'EE3273-039-90B'・'APPRV'・'CHECK'
    - 旧タイトルブロック（off+frozenレイヤーのブロック内）: 図番 'EE3273-039-90A'・
      'APPRV'・'CHECK'（実データと同じく重複するラベル文字列を持つ）
    - 回路ラベル 'R10'（通常レイヤー、枠外）
    """
    doc = ezdxf.new()
    msp = doc.modelspace()

    # 現行タイトルブロック（通常表示）
    msp.add_text('EE3273-039-90B', dxfattribs={'insert': (100, 0), 'layer': '0'})
    msp.add_text('APPRV', dxfattribs={'insert': (100, 10), 'layer': '0'})
    msp.add_text('CHECK', dxfattribs={'insert': (100, 20), 'layer': '0'})

    # 旧タイトルブロック（専用レイヤーごとオフ+フリーズ）
    hidden_layer_name = 'OLD_TITLEBLOCK_LAYER'
    doc.layers.add(hidden_layer_name)
    hidden_layer = doc.layers.get(hidden_layer_name)
    hidden_layer.off()
    hidden_layer.freeze()

    old_block = doc.blocks.new('OLD_TITLEBLOCK')
    old_block.add_text('EE3273-039-90A', dxfattribs={'insert': (0, 0), 'layer': hidden_layer_name})
    old_block.add_text('APPRV', dxfattribs={'insert': (0, 10), 'layer': hidden_layer_name})
    old_block.add_text('CHECK', dxfattribs={'insert': (0, 20), 'layer': hidden_layer_name})
    msp.add_blockref('OLD_TITLEBLOCK', insert=(0, 0))

    # 回路ラベル（枠外・通常レイヤー、比較のため）
    msp.add_text('R10', dxfattribs={'insert': (50, 50), 'layer': '0'})

    doc.saveas(path)


def test_off_frozen_layer_titleblock_content_excluded_from_extract_labels():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'EE3273-039-90B.dxf')
        _build_dxf_with_hidden_old_titleblock(path)

        labels, info = extract_labels(
            path, filter_non_parts=False, sort_order="none", include_coordinates=True,
        )
        label_texts = [l[0] for l in labels]

        # 旧タイトルブロック（off+frozenレイヤー）由来の図番・ラベルは含まれない
        assert 'EE3273-039-90A' not in label_texts, \
            f"off+frozenレイヤーの旧図番が抽出されている: {label_texts}"

        # 現行タイトルブロック・回路ラベルは正しく抽出される
        assert 'EE3273-039-90B' in label_texts
        assert 'R10' in label_texts

        # APPRV/CHECKは現行タイトルブロック分の1回ずつのみ（旧分が混入していない）
        appvr_count = label_texts.count('APPRV')
        check_count = label_texts.count('CHECK')
        assert appvr_count == 1, f"APPRVが複数回抽出されている（旧タイトルブロック混入の疑い）: {appvr_count}"
        assert check_count == 1, f"CHECKが複数回抽出されている（旧タイトルブロック混入の疑い）: {check_count}"


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
