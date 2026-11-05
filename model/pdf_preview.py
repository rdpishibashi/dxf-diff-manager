"""
PDF のブラウザ内プレビュー（モデル層。streamlit 非依存）

「他図面の表示」で PDF を viewer（`dxf_preview.build_viewer_html(layer_panel=False)`）に載せるため、
pypdfium2 で各ページを PNG にし、data URI の <image> として1枚の SVG に縦に並べる。
戻り値は `dxf_preview.PreviewResult`（layers は name="DRAWING" の1件）なので、
DXF の他図面と同じ viewer（拡大縮小・全画面・別ウィンドウ）で表示できる。
外部参照は含まない（画像は data URI）。
"""

import base64
import io
from typing import Tuple

import html

from .dxf_preview import LayerSvg, PreviewResult, build_viewer_html

DEFAULT_MAX_LONG_SIDE_PX = 4000
DEFAULT_MAX_PAGES = 20
PAGE_GAP_RATIO = 0.02  # ページ間の余白（ページ高さに対する比）


def render_pdf_svg(pdf_bytes: bytes, max_long_side_px: int = DEFAULT_MAX_LONG_SIDE_PX,
                   max_pages: int = DEFAULT_MAX_PAGES) -> Tuple[PreviewResult, int]:
    """PDF を、ページを縦に並べた1枚の SVG にする。戻り値は (PreviewResult, 総ページ数)。

    描くのは先頭 `max_pages` ページまで（総ページ数は打ち切り前の値）。
    壊れた PDF・パスワード付き PDF は ValueError（日本語メッセージ）。
    """
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg:
            raise ValueError("パスワード付きの PDF は表示できません") from e
        raise ValueError("PDF を開けませんでした（壊れているか、PDF ではありません）") from e

    try:
        total = len(pdf)
        result = PreviewResult(cjk_font=None)
        if total == 0:
            result.empty = True
            return result, 0

        pages = []  # (png_bytes, w, h)  座標単位は PDF のポイント
        for i in range(min(total, max_pages)):
            page = pdf[i]
            pw, ph = page.get_size()
            scale = max_long_side_px / max(pw, ph)
            img = page.render(scale=scale).to_pil()
            buf = io.BytesIO()
            img.save(buf, "PNG")
            pages.append((buf.getvalue(), pw, ph))
    finally:
        pdf.close()

    width = max(p[1] for p in pages)
    gap = max(p[2] for p in pages) * PAGE_GAP_RATIO
    parts = ['<g class="layer" data-layer="DRAWING">']
    y = 0.0
    for png, pw, ph in pages:
        x = (width - pw) / 2
        b64 = base64.b64encode(png).decode("ascii")
        parts.append(f'<image x="{x:.2f}" y="{y:.2f}" width="{pw:.2f}" height="{ph:.2f}" '
                     f'href="data:image/png;base64,{b64}"/>')
        y += ph + gap
    parts.append("</g>")
    height = y - gap

    result.layers.append(LayerSvg("DRAWING", "図面", "#ffffff", len(pages), "".join(parts)))
    result.view_box = (int(round(width)), int(round(height)))
    return result, total


# build_viewer_html は result.cjk_font が None のとき、左パネルにこの文言を出す。
# PDF は画像で描くのでフォントは関係なく、出すと誤解を招くため消す
VIEWER_FONT_NOTE = "日本語フォントが見つからないため、日本語の文字は表示されません。"


def build_pdf_viewer_html(result: PreviewResult, height_px: int = 720) -> str:
    """PDF の viewer HTML（左パネルは操作のみ。日本語フォントの注意書きは出さない）。"""
    page = build_viewer_html(result, height_px=height_px, layer_panel=False)
    return page.replace(html.escape(VIEWER_FONT_NOTE), "")
