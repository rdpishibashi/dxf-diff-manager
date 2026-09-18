"""
このテストが守るもの: DXF-diff-manager の model/offset_detector.py が
DXF-visual-diff の utils/offset_detector.py と byte-identical であること。

背景（2026-09-18）:
    オフセット補正機能（一部の図形だけが平行移動した図形グループを「変化なし」と
    判定する機能）を、DXF-visual-diff（先行実装）から DXF-diff-manager へ移植した。
    offset_detector.py は compare_dxf.py に一切依存しない純粋ロジックのみのモジュール
    （座標変換・署名生成・ハッシュ計算はすべて呼び出し側から関数として注入される
    設計）のため、2プロジェクト間で完全に同一のまま共有できる——
    Tools/CLAUDE.md「Shared DXF Processing Library Pattern」の extract_labels.py と
    同じ位置づけ。

    2026-09-18以降、このファイルの正本（primary）は **DXF-diff-manager
    （このプロジェクト）の model/offset_detector.py** とする。しきい値調整・
    アルゴリズム変更は今後こちら側で先に行い、DXF-visual-diff 側へ伝播する
    （extract_labels.py の正本が DXF-extract-labels 側にあるのとは逆の力関係
    ——offset_detector.py はもともと DXF-visual-diff で先行実装されたが、
    今後の主開発はこちらで行う方針のため）。

    DXF-visual-diff が隣（`../DXF-visual-diff`）にチェックアウトされていない
    環境では比較できないため、その場合は skip する（データ欠落ではなく前提条件の
    欠如のため、pytest -rs の skip 件数がこのテストに限り出るのは正常）。

    ⚠️ sync_utils.py の UTILS_FILES には意図的に追加していない
    （label_diff.py と同じ扱い。sync_utils.py 自体が実行禁止のスクリプトであり、
    誤って実行された場合に意図しない上書きを招く可能性があるため、このガード
    テストのみで同期を保証する）。

実行:
    cd DXF-diff-manager
    python -m pytest tests/regression/spec/test_offset_detector_identical_to_visual_diff.py
"""
import hashlib
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DIFF_MANAGER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_PRIMARY_DETECTOR = os.path.join(_DIFF_MANAGER_ROOT, "model", "offset_detector.py")
_COPY_DETECTOR = os.path.join(
    _DIFF_MANAGER_ROOT, "..", "DXF-visual-diff", "utils", "offset_detector.py")


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def test_offset_detector_is_byte_identical_to_visual_diff():
    if not os.path.exists(_COPY_DETECTOR):
        pytest.skip(
            "DXF-visual-diff が隣にチェックアウトされていないため比較できません "
            f"(期待パス: {_COPY_DETECTOR})"
        )

    primary_md5 = _md5(_PRIMARY_DETECTOR)
    copy_md5 = _md5(_COPY_DETECTOR)

    assert primary_md5 == copy_md5, (
        "model/offset_detector.py（正本）が DXF-visual-diff の utils/offset_detector.py と"
        "乖離しています。\n"
        "DXF-diff-manager 側（正本）を修正した場合は、次のコマンドで DXF-visual-diff 側を"
        "再同期してください:\n"
        "  cat model/offset_detector.py > ../DXF-visual-diff/utils/offset_detector.py"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
