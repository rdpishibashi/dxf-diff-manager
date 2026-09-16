"""
このテストが守るもの: DXF-diff-manager の model/ref_designator_patterns.py が
DXF-extract-labels の model/ref_designator_patterns.py（正本）と byte-identical
であること。

背景（2026-09-16）:
    「機器符号候補」列の追加（diff_labels.xlsx）にあたり、DXF-extract-labels の
    機器符号判定パターン（model/ref_designator_patterns.py、512行・内部依存なし）を
    そのままコピーして使うことにした。判定ロジック自体（is_ref_designator_label）は
    DXF-diff-manager 独自の model/ref_designator_judge.py に最小移植している
    （NFKC正規化の要否がDXF-extract-labels側と異なるため、あえて別ファイルにした。
    Tools/CLAUDE.md 参照）が、パターン定義（CANDIDATE_PATTERN等）自体は乖離させない
    方針。Tools/CLAUDE.md に記載の通り、共有ファイルは過去に何度もサイレントに
    乖離してきた実績があるため、この一貫性テストで一致を守る。

    DXF-extract-labels が隣（`../DXF-extract-labels`）にチェックアウトされていない
    環境では比較できないため、その場合は skip する（データ欠落ではなく前提条件の
    欠如のため、pytest -rs の skip 件数がこのテストに限り出るのは正常）。

実行:
    cd DXF-diff-manager
    python -m pytest tests/regression/spec/test_ref_designator_patterns_identical_to_extract_labels.py
"""
import hashlib
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DIFF_MANAGER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_PRIMARY_PATTERNS = os.path.join(
    _DIFF_MANAGER_ROOT, "..", "DXF-extract-labels", "model", "ref_designator_patterns.py")
_COPY_PATTERNS = os.path.join(_DIFF_MANAGER_ROOT, "model", "ref_designator_patterns.py")


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def test_ref_designator_patterns_is_byte_identical_to_extract_labels():
    if not os.path.exists(_PRIMARY_PATTERNS):
        pytest.skip(
            "DXF-extract-labels が隣にチェックアウトされていないため比較できません "
            f"(期待パス: {_PRIMARY_PATTERNS})"
        )

    primary_md5 = _md5(_PRIMARY_PATTERNS)
    copy_md5 = _md5(_COPY_PATTERNS)

    assert primary_md5 == copy_md5, (
        "model/ref_designator_patterns.py が DXF-extract-labels の正本と乖離しています。\n"
        "DXF-extract-labels 側を修正した場合は、次のコマンドで再同期してください:\n"
        "  cat ../DXF-extract-labels/model/ref_designator_patterns.py "
        "> model/ref_designator_patterns.py"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
