"""参照図面（他図面の表示）の形式判定・Summary「参照図面」列・ZIP への格納を検証する（streamlit 非依存）。"""
import io
import zipfile

import openpyxl

from model.label_diff import build_diff_labels_workbook, REFERENCE_DRAWINGS_COLUMN
from model.reference_drawings import (
    detect_drawing_format, reference_folder_name, fill_reference_column, build_download_zip)

LABELS = "diff_labels.xlsx"


def _xlsx():
    rows = [{'機器符号候補': '', 'X': 1, 'Y': 2, 'Old Label': 'A', 'New Label': None}]
    sheets = [{'sheet_name': n, 'rows': rows, 'old_label_name': f'Old: {s}', 'new_label_name': f'New: {n}'}
              for n, s in (('EE1-1A', 'EE0-1A'), ('EE2-1A', 'none'))]
    summary = [{'図番': n, '流用元図番': s, '追加ラベル数': 0, '削除ラベル数': 1, '変更ラベル数': 0,
                '機器符号候補数': 0, 'タイトル': 't', 'サブタイトル': 's'}
               for n, s in (('EE1-1A', 'EE0-1A'), ('EE2-1A', 'none'))]
    return build_diff_labels_workbook(sheets, summary_data=summary, include_ref_designator_column=True)


def _zip(members):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    return b.getvalue()


def _base_zip():
    return _zip({"EE1-1A_vs_EE0-1A.dxf": b"dxf1", "EE2-1A_vs_none.dxf": b"dxf2", LABELS: _xlsx(), "ledger.xlsx": b"L"})


def test_detect_format():
    assert detect_drawing_format("a.PDF", b"%PDF-1.7 ...") == "pdf"
    assert detect_drawing_format("noext", b"\n%PDF-1.4") == "pdf"
    assert detect_drawing_format("a.DXF", b"  0\nSECTION") == "dxf"
    assert detect_drawing_format("a.xlsx", b"PK") is None
    assert detect_drawing_format("a.png", b"\x89PNG") is None


def test_summary_has_empty_reference_column_last():
    ws = openpyxl.load_workbook(io.BytesIO(_xlsx()))["Summary"]
    assert [c.value for c in ws[1]][-1] == REFERENCE_DRAWINGS_COLUMN
    assert ws.cell(2, ws.max_column).value in (None, "")


def test_no_references_returns_same_bytes():
    z = _base_zip()
    assert build_download_zip(z, {}, LABELS) is z
    assert build_download_zip(z, {"EE1-1A_vs_EE0-1A.dxf": []}, LABELS) is z


def test_folders_only_for_outputs_with_references_and_members_unchanged():
    z = _base_zip()
    out = build_download_zip(z, {"EE2-1A_vs_none.dxf": [("a.pdf", b"P"), ("sub/b.dxf", b"D"), ("a.pdf", b"dup")],
                                 "EE1-1A_vs_EE0-1A.dxf": []}, LABELS)
    zo, zi = zipfile.ZipFile(io.BytesIO(out)), zipfile.ZipFile(io.BytesIO(z))
    names = zo.namelist()
    assert "EE2-1A_vs_none/a.pdf" in names and "EE2-1A_vs_none/b.dxf" in names
    assert not any(n.startswith("EE1-1A_vs_EE0-1A/") for n in names)
    assert zo.read("EE2-1A_vs_none/a.pdf") == b"P"  # 重複は先の1件だけ
    for n in ("EE1-1A_vs_EE0-1A.dxf", "EE2-1A_vs_none.dxf", "ledger.xlsx"):
        assert zo.read(n) == zi.read(n)


def test_summary_reference_column_joined_with_comma_space_incl_none_source():
    out = build_download_zip(_base_zip(), {"EE2-1A_vs_none.dxf": [("a.pdf", b"P"), ("b.dxf", b"D")],
                                           "EE1-1A_vs_EE0-1A.dxf": [("c.pdf", b"P")]}, LABELS)
    wb = openpyxl.load_workbook(io.BytesIO(zipfile.ZipFile(io.BytesIO(out)).read(LABELS)))
    ws = wb["Summary"]
    col = [c.value for c in ws[1]].index(REFERENCE_DRAWINGS_COLUMN) + 1
    assert ws.cell(2, col).value == "c.pdf"
    assert ws.cell(3, col).value == "a.pdf, b.dxf"
    assert ws["A2"].hyperlink.location == "'EE1-1A'!A1"  # ハイパーリンクが残る
    assert ws["A1"].fill.fgColor.rgb == "FF4472C4"


def test_folder_name():
    assert reference_folder_name("A_vs_B.dxf") == "A_vs_B"
