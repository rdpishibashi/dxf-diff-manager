"""不具合再発防止: 10倍の図面で差分が vs_none になり、図面情報欄と寸法線が出ない。

**識別子**: 2026-11-06、`EE7319-300-01A.dxf`（流用先）と `EE6094-300-01C.dxf`（流用元）。
A2 図面を 10 倍の座標（5940×4200）で描き、図面情報欄のブロック `JZB_0001` を
`xscale=10` で INSERT している。

**以前どう壊れていたか（再現条件）**:
1. 流用元図番の判定しきい値（80）が等倍前提で届かず、流用元図番が None になり
   `EE7319-300-01A_vs_none.dxf`（完全新規扱い）が出力された（`extract_labels.py`）。
2. 差分DXFで、拡大した INSERT 内の MTEXT の `char_height` が拡大されず、図面情報欄の
   文字が 1/10（高さ 2.5）で出力された（`compare_dxf._transform_size_attributes`）。
3. DIMENSION を展開せず、寸法線の代わりに `[DIMENSION]` の文字を出力していた。
4. SPLINE・HATCH・SOLID（寸法線の塗りつぶし矢印）も未対応で `[SPLINE]` 等の文字になっていた。
   SPLINE は位置属性を持たないため署名が全て同じになり、4000本以上が数件に潰れていた。

**修正後に保証したいこと**: 流用元図番が取れる。MTEXT の `char_height`・`width` が
INSERT の倍率で拡大される。DIMENSION は描画ブロックの線・文字・矢印として出力され、
`Defpoints` 画層の基準点（POINT）とプレースホルダ文字は出ない。描画ブロックが無い
DIMENSION は従来どおりプレースホルダにする。SPLINE（制御点を変換）・SOLID（4頂点）は
同じ種類のまま、HATCH は境界を折れ線に近似して模様を引き継いで出力し、形が違えば
署名も違う。オフセット補正の平行移動もこれらの点列に効く。

合成DXF（tmp_path）のみを使う。
"""
import os
import sys

import ezdxf
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, PROJECT_ROOT)

from model.compare_dxf import (  # noqa: E402
    CoordinateTransformer, DiffAnalyzer, EntityExpander, SignatureGenerator, ToleranceConfig,
    generate_all_added_dxf, translate_absolute_entity,
)
from model.extract_labels import extract_labels  # noqa: E402


def _expand(doc):
    return EntityExpander(CoordinateTransformer(ToleranceConfig(0.05))).expand_insert_entities(doc, 'NEW')


def _add_dimension(layout):
    dim = layout.add_linear_dim(base=(0, 20), p1=(0, 0), p2=(100, 0),
                                override={'dimblk': 'OPEN30'})
    dim.render()
    return dim.dimension


def test_source_drawing_number_found_in_scaled_titleblock(tmp_path):
    doc = ezdxf.new('R2010')
    block = doc.blocks.new('TB')
    for text, (x, y) in [('流用元図番', (409.8, 60.37)), ('EE6094-300-01C', (427.0, 58.2)),
                         ('DWG No.', (487.73, 15.80)), ('EE7319-300-01A', (533.0, 15.5))]:
        block.add_mtext(text, dxfattribs={'insert': (x, y), 'char_height': 2.5})
    doc.modelspace().add_blockref('TB', (0, 0), dxfattribs={'xscale': 10, 'yscale': 10})
    path = str(tmp_path / 'EE7319-300-01A.dxf')
    doc.saveas(path)

    _, info = extract_labels(path, extract_drawing_numbers_option=True)

    assert info['source_drawing_number'] == 'EE6094-300-01C'


def test_mtext_char_height_and_width_follow_insert_scale():
    doc = ezdxf.new('R2010')
    block = doc.blocks.new('TB')
    block.add_mtext('TITLE', dxfattribs={'insert': (10, 10), 'char_height': 2.5, 'width': 9.6})
    doc.modelspace().add_blockref('TB', (0, 0), dxfattribs={'xscale': 10, 'yscale': 10})

    mtexts = [e for e in _expand(doc) if e['dxftype'] == 'MTEXT']

    assert len(mtexts) == 1
    assert mtexts[0]['attributes']['char_height'] == pytest.approx(25.0)
    assert mtexts[0]['attributes']['width'] == pytest.approx(96.0)
    assert tuple(mtexts[0]['attributes']['insert'])[:2] == pytest.approx((100.0, 100.0))


@pytest.mark.parametrize('inside_block', [False, True])
def test_dimension_is_expanded_into_lines_and_text(inside_block):
    doc = ezdxf.new('R2010', setup=True)
    if inside_block:
        block = doc.blocks.new('DIMS')
        _add_dimension(block)
        doc.modelspace().add_blockref('DIMS', (1000, 0))
        offset_x = 1000.0
    else:
        _add_dimension(doc.modelspace())
        offset_x = 0.0

    entities = _expand(doc)
    types = [e['dxftype'] for e in entities]

    assert 'DIMENSION' not in types
    assert 'POINT' not in types  # Defpoints の基準点は出さない
    assert types.count('MTEXT') == 1
    assert types.count('LINE') >= 3  # 寸法線と補助線2本（矢印 OPEN30 も LINE）
    xs = [x for e in entities if e['dxftype'] == 'LINE'
          for x in (e['attributes']['start'][0], e['attributes']['end'][0])]
    assert min(xs) == pytest.approx(offset_x, abs=5)
    assert max(xs) == pytest.approx(offset_x + 100, abs=5)


def test_all_added_output_has_dimension_geometry_without_placeholder(tmp_path):
    doc = ezdxf.new('R2010', setup=True)
    _add_dimension(doc.modelspace())
    src = str(tmp_path / 'new.dxf')
    out = str(tmp_path / 'new_vs_none.dxf')
    doc.saveas(src)

    generate_all_added_dxf(src, out)

    texts = [e.dxf.text for e in ezdxf.readfile(out).modelspace().query('TEXT')]
    mtexts = ezdxf.readfile(out).modelspace().query('MTEXT')
    assert '[DIMENSION]' not in texts
    assert any('100' in m.text for m in mtexts)


def test_dimension_without_geometry_block_keeps_placeholder():
    doc = ezdxf.new('R2010', setup=True)
    dim = _add_dimension(doc.modelspace())
    dim.dxf.geometry = '*D_MISSING'

    types = [e['dxftype'] for e in _expand(doc)]

    assert types == ['DIMENSION']


def _scaled_block_doc():
    """SPLINE・HATCH・SOLID を持つブロックを 10 倍・(1000, 0) に INSERT した図面。"""
    doc = ezdxf.new('R2010', setup=True)
    block = doc.blocks.new('SYM')
    block.add_open_spline([(0, 0), (1, 2), (3, 2), (4, 0)])
    hatch = block.add_hatch()
    hatch.set_pattern_fill('ANSI32', scale=0.5)
    hatch.paths.add_polyline_path([(0, 0), (2, 0), (2, 1), (0, 1)], is_closed=True)
    block.add_solid([(0, 0), (1, 0), (0, 1)])
    doc.modelspace().add_blockref('SYM', (1000, 0), dxfattribs={'xscale': 10, 'yscale': 10})
    return doc


def _by_type(entities):
    out = {}
    for e in entities:
        out.setdefault(e['dxftype'], []).append(e)
    return out


def test_spline_hatch_solid_follow_insert_transform():
    entities = _by_type(_expand(_scaled_block_doc()))

    spline = entities['SPLINE'][0]['attributes']
    assert [tuple(p)[:2] for p in spline['control_points']] == pytest.approx(
        [(1000, 0), (1010, 20), (1030, 20), (1040, 0)])
    hatch = entities['HATCH'][0]['attributes']
    xs = [p[0] for p in hatch['hatch_paths'][0]]
    ys = [p[1] for p in hatch['hatch_paths'][0]]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((1000, 1020, 0, 10))
    assert hatch['pattern_scale'] == pytest.approx(5.0)
    solid = entities['SOLID'][0]['attributes']
    assert tuple(solid['vtx1'])[:2] == pytest.approx((1010, 0))


def test_different_splines_have_different_signatures():
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    msp.add_open_spline([(0, 0), (1, 2), (3, 2), (4, 0)])
    msp.add_open_spline([(0, 0), (1, 5), (3, 5), (4, 0)])
    tr = CoordinateTransformer(ToleranceConfig(0.05))
    hashes, _, _, _ = DiffAnalyzer(SignatureGenerator(tr)).extract_entities_from_doc(doc, 'NEW', EntityExpander(tr))

    assert len(hashes) == 2


def test_translate_moves_spline_hatch_solid_points():
    entities = _by_type(_expand(_scaled_block_doc()))

    moved = {t: translate_absolute_entity(es[0], (5, 7))['attributes'] for t, es in entities.items()}

    assert tuple(moved['SPLINE']['control_points'][0])[:2] == pytest.approx((1005, 7))
    first = entities['HATCH'][0]['attributes']['hatch_paths'][0][0]
    assert tuple(moved['HATCH']['hatch_paths'][0][0])[:2] == pytest.approx((first[0] + 5, first[1] + 7))
    assert tuple(moved['SOLID']['vtx0'])[:2] == pytest.approx((1005, 7))


def test_output_keeps_spline_hatch_solid_without_placeholder(tmp_path):
    doc = _scaled_block_doc()
    # 塗りつぶし矢印（ブロック _CLOSEDFILLED の中身が SOLID）
    dim = doc.modelspace().add_linear_dim(base=(0, 20), p1=(0, 0), p2=(100, 0),
                                          override={'dimblk': ezdxf.ARROWS.closed_filled})
    dim.render()
    src = str(tmp_path / 'new.dxf')
    out = str(tmp_path / 'new_vs_none.dxf')
    doc.saveas(src)

    generate_all_added_dxf(src, out)

    msp = ezdxf.readfile(out).modelspace()
    added = [e for e in msp if e.dxf.layer == 'NEW_ADDED']
    types = [e.dxftype() for e in added]
    assert not [e.dxf.text for e in added if e.dxftype() == 'TEXT' and e.dxf.text.startswith('[')]
    assert types.count('SPLINE') == 1
    assert types.count('HATCH') == 1
    assert types.count('SOLID') == 3  # ブロックの1個＋寸法の矢印2個
    spline = next(e for e in added if e.dxftype() == 'SPLINE')
    assert [tuple(p)[:2] for p in spline.control_points] == pytest.approx(
        [(1000, 0), (1010, 20), (1030, 20), (1040, 0)])
    hatch = next(e for e in added if e.dxftype() == 'HATCH')
    assert hatch.dxf.pattern_name == 'ANSI32'
