"""不具合再発防止: 差分DXFに出る寸法値（465・45 など）と、ブロックの属性値（改訂記号の B・C・D）が
diff_labels.xlsx に出ない。

**識別子**: 2026-11-06、`EE7319-300-01A.dxf`（流用先）と `EE6094-300-01C.dxf`（流用元）、
`EE7319-307A.dxf` と `EE5610-307D.dxf`。

**以前どう壊れていたか（再現条件）**: 差分DXFは、寸法線（DIMENSION）を描画ブロックに展開し、
ブロック参照の属性（ATTRIB）も文字として出力する。一方、ラベル抽出（`extract_labels.py`、
5 プロジェクトで共有）は TEXT／MTEXT だけを見るので、次が diff_labels.xlsx に出ず、DXF の差分と
食い違っていた。
- 寸法値: 実データで 250・35・45・465・5 が追加、750 が削除
- 属性値: 改訂記号のブロック `SX_DeltaSymbol`（三角形の LWPOLYLINE ＋ 属性定義）の属性。
  三角形の中の `Ｂ`・`Ｃ`・`Ｄ` はこの属性値（300-01C に10個、307D に15個）

**修正後に保証したいこと**: DXF-diff-manager だけが `model/extra_labels.py` で寸法値と属性値を足し
（共有の `extract_labels.py` は変えない）、ペア比較・完全新規図面のどちらでも diff_labels.xlsx に出る。
機器符号候補は空欄。非表示の画層・`invisible` の図形は出さない。`filter_non_parts`（機器符号以外を除く）の
ときは足さない。該当する図形が無い図面は変わらない。

合成DXF（tmp_path）のみを使う。
"""
import io
import os
import sys

import ezdxf
import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, PROJECT_ROOT)

from model.extra_labels import extract_extra_labels  # noqa: E402
from model.diff_export import create_diff_zip  # noqa: E402
from model.label_diff import compute_label_differences  # noqa: E402
from model.pairing import build_pairs_from_list  # noqa: E402


def _doc_with_dimension(length, layer=None, in_block=False):
    """長さ `length` の寸法線を1本持つ DXF。in_block=True ならブロック内に置き 2 倍・(1000, 0) で INSERT。"""
    doc = ezdxf.new('R2010', setup=True)
    target = doc.blocks.new('DIMS') if in_block else doc.modelspace()
    kwargs = {'dxfattribs': {'layer': layer}} if layer else {}
    # dimlfac=1: ezdxf の既定の寸法スタイルは寸法値を 100 倍して表示するので、実寸のまま表示させる
    dim = target.add_linear_dim(base=(0, 20), p1=(0, 0), p2=(length, 0),
                                override={'dimlfac': 1}, **kwargs)
    dim.render()
    if in_block:
        doc.modelspace().add_blockref('DIMS', (1000, 0), dxfattribs={'xscale': 2, 'yscale': 2})
    return doc


def _save(doc, tmp_path, name):
    path = str(tmp_path / name)
    doc.saveas(path)
    return path


def test_dimension_value_is_extracted_with_position(tmp_path):
    labels = extract_extra_labels(_save(_doc_with_dimension(100), tmp_path, 'a.dxf'))

    assert [t for t, _x, _y in labels] == ['100']
    _t, x, _y = labels[0]
    assert x == pytest.approx(50, abs=10)  # 寸法線の中央あたり


def test_dimension_value_inside_scaled_block_follows_insert_transform(tmp_path):
    labels = extract_extra_labels(_save(_doc_with_dimension(100, in_block=True), tmp_path, 'b.dxf'))

    assert [t for t, _x, _y in labels] == ['100']
    assert labels[0][1] == pytest.approx(1000 + 2 * 50, abs=20)


def test_drawing_without_dimension_gives_no_labels(tmp_path):
    doc = ezdxf.new('R2010')
    doc.modelspace().add_text('CB001', dxfattribs={'insert': (0, 0)})

    assert extract_extra_labels(_save(doc, tmp_path, 'c.dxf')) == []


def test_dimension_on_hidden_layer_is_not_extracted(tmp_path):
    doc = ezdxf.new('R2010', setup=True)
    doc.layers.add('HID').off()
    dim = doc.modelspace().add_linear_dim(base=(0, 20), p1=(0, 0), p2=(100, 0),
                                          override={'dimlfac': 1}, dxfattribs={'layer': 'HID'})
    dim.render()

    assert extract_extra_labels(_save(doc, tmp_path, 'd.dxf')) == []


def test_unreadable_file_gives_no_labels(tmp_path):
    bad = tmp_path / 'broken.dxf'
    bad.write_text('not a dxf')

    assert extract_extra_labels(str(bad)) == []


def test_pair_diff_includes_changed_dimension_values(tmp_path):
    new_file = _save(_doc_with_dimension(120), tmp_path, 'new.dxf')
    old_file = _save(_doc_with_dimension(100), tmp_path, 'old.dxf')

    change_rows, _unchanged, _extra = compute_label_differences(new_file, old_file)

    assert {(r['Old Label'], r['New Label']) for r in change_rows} == {('100', None), (None, '120')}


def test_pair_diff_excludes_dimension_values_when_filtering_non_parts(tmp_path):
    new_file = _save(_doc_with_dimension(120), tmp_path, 'new.dxf')
    old_file = _save(_doc_with_dimension(100), tmp_path, 'old.dxf')

    change_rows, _unchanged, _extra = compute_label_differences(new_file, old_file, filter_non_parts=True)

    assert change_rows == []


def test_brand_new_drawing_sheet_includes_dimension_values_with_blank_mark(tmp_path):
    path = _save(_doc_with_dimension(465), tmp_path, 'BRANDNEW-DIM.dxf')
    all_files_dict = {'BRANDNEW-DIM': {'temp_path': path, 'title': None, 'subtitle': None,
                                       'filename': 'BRANDNEW-DIM.dxf'}}
    pairs = build_pairs_from_list(
        pd.DataFrame({'流用元図番': [''], '流用先図番': ['BRANDNEW-DIM']}), all_files_dict)

    _, results, diff_labels_excel, _, _ = create_diff_zip(pairs, step1_mode='pair_list')

    assert results[0]['success']
    df = pd.read_excel(io.BytesIO(diff_labels_excel), sheet_name='BRANDNEW-DIM')
    row = df[df['New: BRANDNEW-DIM'].astype(str) == '465'].iloc[0]
    assert pd.isna(row['機器符号候補'])  # 寸法値は機器符号候補ではない
    summary = pd.read_excel(io.BytesIO(diff_labels_excel), sheet_name='Summary')
    assert summary.loc[0, '追加ラベル数'] == 1
    assert summary.loc[0, '機器符号候補数'] == 0


# --- ブロックの属性値（改訂記号 SX_DeltaSymbol の B・C・D など） ---

def _doc_with_delta_symbols(letters, scale=4.5, invisible=None, hidden_layer_letter=None):
    """三角形の LWPOLYLINE ＋ 属性定義のブロック（SX_DeltaSymbol 相当）を、属性値 `letters` で INSERT した DXF。"""
    doc = ezdxf.new('R2010')
    block = doc.blocks.new('SX_DeltaSymbol')
    block.add_lwpolyline([(0, 0), (1, 0), (0.5, 1)], close=True)
    block.add_attdef('デルタ文字', (0.5, 0.3), text='X', dxfattribs={'height': 0.4})
    if hidden_layer_letter:
        doc.layers.add('HID').off()
    for i, letter in enumerate(letters):
        ref = doc.modelspace().add_blockref('SX_DeltaSymbol', (100 + 50 * i, 200), dxfattribs={'xscale': scale, 'yscale': scale})
        ref.add_auto_attribs({'デルタ文字': letter})
    if hidden_layer_letter:
        ref = doc.modelspace().add_blockref('SX_DeltaSymbol', (500, 200), dxfattribs={'layer': 'HID'})
        ref.add_auto_attribs({'デルタ文字': hidden_layer_letter})
    if invisible:
        ref = doc.modelspace().add_blockref('SX_DeltaSymbol', (600, 200))
        ref.add_auto_attribs({'デルタ文字': invisible})
        for attrib in ref.attribs:
            attrib.dxf.invisible = 1
    return doc


def test_attribute_values_of_delta_symbols_are_extracted(tmp_path):
    labels = extract_extra_labels(_save(_doc_with_delta_symbols(['Ｂ', 'Ｃ', 'Ｂ']), tmp_path, 'e.dxf'))

    assert sorted(t for t, _x, _y in labels) == ['Ｂ', 'Ｂ', 'Ｃ']
    xs = sorted(x for _t, x, _y in labels)
    assert xs[0] == pytest.approx(100 + 0.5 * 4.5, abs=5)  # 属性の位置（INSERT の位置の近く）


def test_attribute_value_inside_nested_block_is_extracted(tmp_path):
    doc = ezdxf.new('R2010')
    inner = doc.blocks.new('SYM')
    inner.add_attdef('TAG', (0, 0), text='X')
    outer = doc.blocks.new('OUTER')
    ref = outer.add_blockref('SYM', (10, 10))
    ref.add_auto_attribs({'TAG': 'Ｄ'})
    doc.modelspace().add_blockref('OUTER', (1000, 0))

    labels = extract_extra_labels(_save(doc, tmp_path, 'f.dxf'))

    # 入れ子の INSERT の属性にも親ブロックの配置位置が掛かる（2026-11-06 修正。位置は
    # test_nested_insert_attrib_position.py でも固定）
    assert [t for t, _x, _y in labels] == ['Ｄ']
    assert labels[0][1] == pytest.approx(1010)
    assert labels[0][2] == pytest.approx(10)


def test_invisible_attribute_and_hidden_layer_insert_are_not_extracted(tmp_path):
    doc = _doc_with_delta_symbols(['Ｂ'], invisible='Ｘ', hidden_layer_letter='Ｙ')

    labels = extract_extra_labels(_save(doc, tmp_path, 'g.dxf'))

    assert [t for t, _x, _y in labels] == ['Ｂ']


def test_empty_attribute_value_is_not_extracted(tmp_path):
    labels = extract_extra_labels(_save(_doc_with_delta_symbols(['']), tmp_path, 'h.dxf'))

    assert labels == []


def test_pair_diff_includes_deleted_attribute_values(tmp_path):
    """流用元にだけ改訂記号がある（流用先では消えた）とき、属性値が削除として出る。"""
    new_file = _save(ezdxf.new('R2010'), tmp_path, 'new.dxf')
    old_file = _save(_doc_with_delta_symbols(['Ｂ', 'Ｃ']), tmp_path, 'old.dxf')

    change_rows, _unchanged, _extra = compute_label_differences(new_file, old_file)

    assert sorted((r['Old Label'], r['New Label']) for r in change_rows) == [('Ｂ', None), ('Ｃ', None)]


def test_attribute_values_are_not_added_when_filtering_non_parts(tmp_path):
    new_file = _save(ezdxf.new('R2010'), tmp_path, 'new.dxf')
    old_file = _save(_doc_with_delta_symbols(['Ｂ']), tmp_path, 'old.dxf')

    change_rows, _unchanged, _extra = compute_label_differences(new_file, old_file, filter_non_parts=True)

    assert change_rows == []


def test_brand_new_drawing_sheet_includes_attribute_values(tmp_path):
    path = _save(_doc_with_delta_symbols(['Ｄ']), tmp_path, 'BRANDNEW-ATT.dxf')
    all_files_dict = {'BRANDNEW-ATT': {'temp_path': path, 'title': None, 'subtitle': None,
                                       'filename': 'BRANDNEW-ATT.dxf'}}
    pairs = build_pairs_from_list(
        pd.DataFrame({'流用元図番': [''], '流用先図番': ['BRANDNEW-ATT']}), all_files_dict)

    _, results, diff_labels_excel, _, _ = create_diff_zip(pairs, step1_mode='pair_list')

    assert results[0]['success']
    df = pd.read_excel(io.BytesIO(diff_labels_excel), sheet_name='BRANDNEW-ATT')
    row = df[df['New: BRANDNEW-ATT'].astype(str) == 'Ｄ'].iloc[0]
    assert pd.isna(row['機器符号候補'])
