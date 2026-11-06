"""
このテストが守るもの: ブロックの中に入れ子になった INSERT の属性（ATTRIB）に、親ブロックの変換
（配置位置・倍率）が掛かること（2026-11-06 修正、ユーザー判断で本格運用前に対処）。

- 以前の壊れ方: `_append_insert_attribs()` が恒等行列で絶対座標化していたため、入れ子の INSERT の属性が
  親ブロックの座標（原点付近）のままになり、差分DXFでは親だけを動かしても属性が UNCHANGED になった
- 守ること: 2段・3段の入れ子で位置が合成行列に従う／親を拡大配置すると位置と文字高さも拡大する／
  モデル空間直下の INSERT の属性は従来どおり／親ブロックだけの配置違いで属性も差分になる
"""
import os
import sys

import ezdxf
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from model.compare_dxf import (  # noqa: E402
    CoordinateTransformer, DiffAnalyzer, EntityExpander, SignatureGenerator, ToleranceConfig,
)


def _attribs(doc):
    tr = CoordinateTransformer(ToleranceConfig(0.01), debug=False)
    entities = EntityExpander(tr).expand_insert_entities(doc, 'test')
    return {e['attributes']['text']: e for e in entities
            if e['dxftype'] == 'ATTRIB' and e.get('insert_info', {}).get('is_insert_attrib')}


def _symbol_block(doc):
    sym = doc.blocks.new('SYM')
    sym.add_attdef('TAG', (0, 0), dxfattribs={'height': 2.5})


def _insert_with_tag(layout, block, point, tag, **attribs):
    ref = layout.add_blockref(block, point, dxfattribs=attribs)
    ref.add_auto_attribs({'TAG': tag})
    return ref


def _pos(entity):
    p = entity['attributes']['insert']
    return p[0], p[1]


def test_attrib_directly_in_modelspace_keeps_its_position():
    doc = ezdxf.new('R2010')
    _symbol_block(doc)
    _insert_with_tag(doc.modelspace(), 'SYM', (30, 40), 'D1')

    x, y = _pos(_attribs(doc)['D1'])

    assert (x, y) == (pytest.approx(30), pytest.approx(40))


def test_attrib_in_two_level_nesting_follows_parent_placement():
    doc = ezdxf.new('R2010')
    _symbol_block(doc)
    outer = doc.blocks.new('OUTER')
    _insert_with_tag(outer, 'SYM', (10, 20), 'D2')
    doc.modelspace().add_blockref('OUTER', (1000, 500))

    x, y = _pos(_attribs(doc)['D2'])

    assert (x, y) == (pytest.approx(1010), pytest.approx(520))


def test_attrib_in_three_level_nesting_uses_composed_matrix():
    doc = ezdxf.new('R2010')
    _symbol_block(doc)
    inner = doc.blocks.new('INNER')
    _insert_with_tag(inner, 'SYM', (1, 2), 'D3')
    outer = doc.blocks.new('OUTER')
    outer.add_blockref('INNER', (10, 20))
    doc.modelspace().add_blockref('OUTER', (100, 200))

    x, y = _pos(_attribs(doc)['D3'])

    assert (x, y) == (pytest.approx(111), pytest.approx(222))


def test_scaled_parent_scales_attrib_position_and_height():
    doc = ezdxf.new('R2010')
    _symbol_block(doc)
    outer = doc.blocks.new('OUTER')
    _insert_with_tag(outer, 'SYM', (10, 20), 'D4')
    doc.modelspace().add_blockref('OUTER', (1000, 0), dxfattribs={'xscale': 2, 'yscale': 2, 'zscale': 2})

    entity = _attribs(doc)['D4']

    x, y = _pos(entity)
    assert (x, y) == (pytest.approx(1020), pytest.approx(40))
    assert entity['attributes']['height'] == pytest.approx(5.0)


def _signatures(doc, label):
    tr = CoordinateTransformer(ToleranceConfig(0.01), debug=False)
    da = DiffAnalyzer(SignatureGenerator(tr, debug=False), debug=False)
    entities, _, _, _ = da.extract_entities_from_doc(doc, label, EntityExpander(tr))
    return entities


def _doc_with_parent_at(x):
    doc = ezdxf.new('R2010')
    _symbol_block(doc)
    outer = doc.blocks.new('OUTER')
    outer.add_line((0, 0), (5, 0))
    _insert_with_tag(outer, 'SYM', (10, 20), 'D5')
    doc.modelspace().add_blockref('OUTER', (x, 0))
    return doc


def test_moving_only_the_parent_block_makes_the_attrib_a_difference():
    """修正前は属性が原点付近で動かず UNCHANGED になっていた。親の移動で属性も新旧の差分になる。"""
    old = _signatures(_doc_with_parent_at(0), 'OLD')
    new = _signatures(_doc_with_parent_at(500), 'NEW')

    only_old, only_new = set(old) - set(new), set(new) - set(old)

    def has_attrib(sigs, entities):
        return any(v['absolute_entity']['dxftype'] == 'ATTRIB' for s in sigs for _loc, v in entities[s])

    assert has_attrib(only_old, old) and has_attrib(only_new, new)
