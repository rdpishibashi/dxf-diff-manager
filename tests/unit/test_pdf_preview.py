"""PDF プレビュー（pypdfium2 でページを画像化して SVG に縦に並べる）を検証する。"""
import pypdfium2 as pdfium
import pytest

from model.dxf_preview import build_viewer_html
from model.pdf_preview import render_pdf_svg


def _pdf(n_pages, size=(200, 100)):
    doc = pdfium.PdfDocument.new()
    for _ in range(n_pages):
        doc.new_page(*size)
    import io
    b = io.BytesIO()
    doc.save(b)
    return b.getvalue()


def test_pages_stacked_vertically():
    r, total = render_pdf_svg(_pdf(3))
    assert total == 3 and r.layers[0].count == 3 and not r.empty
    w, h = r.view_box
    assert w == 200 and h > 300  # 3ページ分＋余白


def test_page_limit_keeps_total():
    r, total = render_pdf_svg(_pdf(5), max_pages=2)
    assert total == 5 and r.layers[0].count == 2


def test_viewer_has_no_external_references():
    r, _ = render_pdf_svg(_pdf(1))
    h = build_viewer_html(r, layer_panel=False)
    assert "data:image/png;base64," in h and "http://" not in h.replace("http://www.w3.org/2000/svg", "")


def test_broken_bytes_raise_value_error():
    with pytest.raises(ValueError):
        render_pdf_svg(b"not a pdf")


def test_pdf_viewer_has_no_font_warning_but_dxf_viewer_without_font_does():
    """PDF は画像なので「日本語フォントが見つからない」注意書きを出さない（DXF の viewer は出す）。"""
    from model.dxf_preview import build_viewer_html
    from model.pdf_preview import VIEWER_FONT_NOTE, build_pdf_viewer_html
    r, _ = render_pdf_svg(_pdf(1))
    assert r.cjk_font is None
    assert VIEWER_FONT_NOTE in build_viewer_html(r, layer_panel=False)  # 注意書きの文言が viewer と一致している
    assert VIEWER_FONT_NOTE not in build_pdf_viewer_html(r)
