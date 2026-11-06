"""ラベル抽出（`extract_labels.py`）が拾わない文字 — 寸法値とブロックの属性値 — を、
diff_labels.xlsx のラベルとして取り出す。

背景（2026-11-06、ユーザー指摘）: 差分DXFは、寸法線（DIMENSION）を描画ブロックに展開し、
ブロックの属性値（ATTRIB）も文字として出力する。一方、ラベル抽出は TEXT／MTEXT だけを見るので、
次の文字が diff_labels.xlsx に出ず、DXF の差分と食い違っていた。

- 寸法値: 寸法線の描画ブロックの中の文字（`465`・`45` など）
- 属性値: ブロック参照（INSERT）の属性（ATTRIB）。実例は改訂記号のブロック `SX_DeltaSymbol`
  （三角形の LWPOLYLINE ＋ 属性定義 `デルタ文字`）で、三角形の中の `Ｂ`・`Ｃ`・`Ｄ` はこの属性値
  （`EE6094-300-01C.dxf` 10個、`EE5610-307D.dxf` 15個。差分DXFの削除の数と一致）

`extract_labels.py` は 5 プロジェクトで共有（バイト一致）しているので変えず、DXF-diff-manager
だけでこのモジュールから足す。差分DXFと同じ展開（`compare_dxf.EntityExpander`）を使うので、
DXF に出る文字と同じものが Excel に出る（非表示の画層・`invisible` も同じ扱い）。
streamlit には依存しない。
"""
import logging
from typing import List, Tuple

import ezdxf

from .compare_dxf import CoordinateTransformer, EntityExpander, ToleranceConfig
from .extract_labels import clean_mtext_format_codes

logger = logging.getLogger(__name__)


def _has_extra_source(doc) -> bool:
    """DIMENSION または ATTRIB 付きの INSERT がどこかにあるか（無ければ展開を省く）。"""
    def _in(entities):
        for e in entities:
            kind = e.dxftype()
            if kind == 'DIMENSION' or (kind == 'INSERT' and e.attribs):
                return True
        return False

    return any(_in(layout) for layout in doc.layouts) or any(_in(block) for block in doc.blocks)


def _is_extra_label_source(entity) -> bool:
    """展開後のエンティティが、寸法線由来の文字か、INSERT の属性値か。"""
    info = entity.get('insert_info') or {}
    if info.get('dimension'):
        return entity['dxftype'] in ('MTEXT', 'TEXT')
    return entity['dxftype'] == 'ATTRIB' and bool(info.get('is_insert_attrib'))


def extract_extra_labels(dxf_file: str) -> List[Tuple[str, float, float]]:
    """寸法値と属性値を `(テキスト, X, Y)` のリストで返す（`extract_labels(include_coordinates=True)` と同じ形）。

    座標は図面の絶対座標。該当する図形が無い図面、読み込めない図面は空リスト（例外にしない。
    ラベル抽出本体のエラーは `extract_labels()` 側が報告する）。
    """
    try:
        doc = ezdxf.readfile(dxf_file)
        if not _has_extra_source(doc):
            return []
        expander = EntityExpander(CoordinateTransformer(ToleranceConfig(0.05)))
        absolute_entities = expander.expand_insert_entities(doc, 'EXTRA')
    except Exception as e:
        logger.warning(f"寸法値・属性値の抽出に失敗しました ({dxf_file}): {e}")
        return []

    labels = []
    seen = set()
    for entity in absolute_entities:
        if not _is_extra_label_source(entity):
            continue
        text = entity.get('text_content') or ''
        if entity['dxftype'] == 'MTEXT':
            text = clean_mtext_format_codes(text)
        text = text.strip()
        insert = entity['attributes'].get('insert')
        if not text or insert is None:
            continue
        key = (text, round(float(insert[0]), 4), round(float(insert[1]), 4))
        if key in seen:  # 同じ位置の同じ文字は1件（extract_labels の重複除去と同じ考え方）
            continue
        seen.add(key)
        labels.append((text, float(insert[0]), float(insert[1])))
    return labels
