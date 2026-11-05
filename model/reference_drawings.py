"""
「他図面の表示」で表示した参照図面を、ダウンロード用 ZIP に含めるための処理（モデル層。streamlit 非依存）

- 形式の自動判定（DXF／PDF）
- `diff_labels.xlsx` の Summary シート「参照図面」列への書き込み（複数は ", " 区切り）
- ZIP の直下に `<回路差分ファイル名から .dxf を除いた名前>/` フォルダを作り、参照図面を入れる
  （参照図面の無い回路差分にはフォルダを作らない）
"""

import io
import posixpath
import zipfile
from typing import Dict, List, Optional, Tuple

import openpyxl

from .label_diff import REFERENCE_DRAWINGS_COLUMN

REFERENCE_SEPARATOR = ", "
DIFF_OUTPUT_SUFFIX = ".dxf"


def detect_drawing_format(filename: str, data: bytes) -> Optional[str]:
    """'pdf'／'dxf'／None（非対応）を返す。先頭 1024 バイトに `%PDF-` があれば PDF、
    それ以外で拡張子が .dxf（大文字小文字を問わない）なら DXF。"""
    if b"%PDF-" in (data or b"")[:1024]:
        return "pdf"
    if filename.lower().endswith(".dxf"):
        return "dxf"
    return None


def reference_folder_name(output_filename: str) -> str:
    """回路差分ファイル名（`A_vs_B.dxf`）から ZIP 内の参照図面フォルダ名（`A_vs_B`）を返す。"""
    base = posixpath.basename(output_filename)
    if base.lower().endswith(DIFF_OUTPUT_SUFFIX):
        base = base[:-len(DIFF_OUTPUT_SUFFIX)]
    return base


def _safe_basename(filename: str) -> str:
    return posixpath.basename(filename.replace("\\", "/")) or "reference"


def fill_reference_column(xlsx_bytes: bytes, refs_by_output: Dict[str, List[str]]) -> bytes:
    """Summary シートの「参照図面」列に、回路差分ごとの参照図面のファイル名を書き込む。

    `refs_by_output` は {出力ファイル名: [参照図面のファイル名, ...]}。Summary の行と回路差分の対応は
    `f"{図番}_vs_{流用元図番}.dxf" == 出力ファイル名`（完全新規図面は流用元図番が "none"）。
    ハイパーリンク・見出し書式・列幅は openpyxl の読み書きで保たれる。
    """
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    if "Summary" not in wb.sheetnames:
        return xlsx_bytes
    ws = wb["Summary"]
    headers = [c.value for c in ws[1]]
    if REFERENCE_DRAWINGS_COLUMN not in headers or "図番" not in headers or "流用元図番" not in headers:
        return xlsx_bytes
    ref_col = headers.index(REFERENCE_DRAWINGS_COLUMN) + 1
    num_col = headers.index("図番") + 1
    src_col = headers.index("流用元図番") + 1

    for row in range(2, ws.max_row + 1):
        num = ws.cell(row, num_col).value
        src = ws.cell(row, src_col).value
        if not num:
            continue
        names = refs_by_output.get(f"{num}_vs_{src or 'none'}.dxf")
        if names:
            ws.cell(row, ref_col, REFERENCE_SEPARATOR.join(names))

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def build_download_zip(zip_data: bytes,
                       refs_by_output: Dict[str, List[Tuple[str, bytes]]],
                       diff_labels_filename: str) -> bytes:
    """Step 4 の ZIP に参照図面を加えた ZIP を返す。

    参照図面が1件も無ければ `zip_data` をそのまま返す。あれば、既存メンバーは内容を変えずに
    コピーし（`diff_labels.xlsx` だけ「参照図面」列を書き込んだ版に差し替え）、
    `<出力名>/<ファイル名>` を追加する。同じフォルダ内の同名は先の1件だけ。
    """
    refs_by_output = {k: v for k, v in refs_by_output.items() if v}
    if not refs_by_output:
        return zip_data

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zin, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        names_by_output = {k: list(dict.fromkeys(_safe_basename(n) for n, _ in v))
                           for k, v in refs_by_output.items()}
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == diff_labels_filename:
                data = fill_reference_column(data, names_by_output)
            zout.writestr(info, data)
        for output_name, refs in refs_by_output.items():
            folder = reference_folder_name(output_name)
            seen = set()
            for name, data in refs:
                base = _safe_basename(name)
                if base in seen:
                    continue
                seen.add(base)
                zout.writestr(f"{folder}/{base}", data)
    return out.getvalue()
