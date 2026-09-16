"""機器符号（Reference Designator）候補判定 — DXF-extract-labels からの最小移植。

`DXF-extract-labels/model/ref_designator.py::is_ref_designator_label()` の
判定ロジックのみを移植したもの（DXF読み込み・図面枠検出等は一切含まない）。
本ファイルは意図的に `ref_designator.py` と別名にしている——同名だと
「primary と diff して同期すべきファイル」と誤認され、byte-identical 前提の
運用（`model/ref_designator_patterns.py` 等）と混同されるおそれがあるため。

判定パターン自体（`CANDIDATE_PATTERN`）は `ref_designator_patterns.py`
（primaryから byte-identical コピー、回帰テストで保証）に委譲し、
本ファイルは以下2点のみを独自に持つ:
  - `_SINGLE_LETTER_PATTERN` / `_TRAILING_SIGN_PATTERN`
    （primary の `ref_designator.py` 側にあり、`ref_designator_patterns.py`
    には含まれないため）
  - `_judgment_text()` 相当のデリミタ処理は `ref_designator_patterns.py` の
    ものをそのまま使う

⚠️ 正規化について: DXF-extract-labels 側では上流のラベル抽出パイプラインが
NFKC正規化（`normalize_label()`/`normalize_width()`）済みの文字列を渡してくる
前提だが、DXF-diff-manager の `model/extract_labels.py` は正規化を行わない。
そのため本ファイルの `is_ref_designator_label()` は呼び出し側で正規化済みか
どうかに関わらず、内部で必ず `normalize_label()` を適用してから判定する。
"""
import re

from .ref_designator_patterns import CANDIDATE_PATTERN, normalize_label, _judgment_text

# 英大文字1字だけの文字列は機器符号ではない（図形枠外の位置記号等）。
# primary (`ref_designator.py`) の同名パターンと同一。
_SINGLE_LETTER_PATTERN = re.compile(r'^[A-Z]$')

# 末尾が "+"/"-" で終わる文字列は電源端子表記であって単体の機器符号ではない。
# primary (`ref_designator.py`) の同名パターンと同一。
_TRAILING_SIGN_PATTERN = re.compile(r'.*[+-]$')


def is_ref_designator_label(text: str) -> bool:
    """textが機器符号（Reference Designator）候補パターンに一致するかを返す。

    DXF-extract-labels の `ref_designator.py::is_ref_designator_label()` と
    判定条件は同一だが、呼び出し前に `normalize_label()`（NFKC正規化）を
    必ず適用する点が異なる（本ファイル冒頭の注意参照）。
    """
    normalized = normalize_label(text)
    judgment = _judgment_text(normalized)
    if _SINGLE_LETTER_PATTERN.match(judgment) or _TRAILING_SIGN_PATTERN.match(judgment):
        return False
    return bool(CANDIDATE_PATTERN.match(judgment))
