"""
このテストが守るもの: DXF-diff-manager の model/dxf_preview.py（正本）と fonts/ が
DXF-visual-diff の utils/dxf_preview.py・fonts/ とバイト一致であること。

背景（2026-10-23）:
    差分DXFのブラウザ内プレビューは DXF-visual-diff で試作し、DXF-diff-manager へ展開した。
    offset_detector.py と同じく正本は **DXF-diff-manager** とし、DXF-visual-diff はコピー。
    変更は先にこちらで行い、次のコマンドで伝播する:
        command cp -f model/dxf_preview.py ../DXF-visual-diff/utils/dxf_preview.py
    DXF-visual-diff が隣にない環境ではコピー側が比較できないため skip する。
    正本側のファイルが無い場合は skip せず fail する。

    ⚠️ sync_utils.py の UTILS_FILES には追加しない（実行禁止のスクリプトのため）。
"""
import hashlib
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_COPY_ROOT = os.path.join(_ROOT, "..", "DXF-visual-diff")

_PAIRS = [
    (os.path.join("model", "dxf_preview.py"), os.path.join("utils", "dxf_preview.py")),
    (os.path.join("fonts", "ipaexg.ttf"), os.path.join("fonts", "ipaexg.ttf")),
    (os.path.join("fonts", "IPA_Font_License_Agreement_v1.0.txt"),
     os.path.join("fonts", "IPA_Font_License_Agreement_v1.0.txt")),
    (os.path.join("fonts", "Readme_IPAexfont00401.txt"),
     os.path.join("fonts", "Readme_IPAexfont00401.txt")),
]


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


@pytest.mark.parametrize("primary_rel,copy_rel", _PAIRS)
def test_preview_files_are_byte_identical_to_visual_diff(primary_rel, copy_rel):
    primary = os.path.join(_ROOT, primary_rel)
    assert os.path.exists(primary), f"正本が見つかりません: {primary}"
    copy = os.path.join(_COPY_ROOT, copy_rel)
    if not os.path.exists(os.path.join(_COPY_ROOT, "app.py")):
        pytest.skip(f"DXF-visual-diff が隣にチェックアウトされていないため比較できません ({_COPY_ROOT})")
    assert os.path.exists(copy), f"DXF-visual-diff 側にコピーがありません: {copy}"
    assert _md5(primary) == _md5(copy), (
        f"{primary_rel}（正本）が DXF-visual-diff の {copy_rel} と乖離しています。"
        "正本を直した場合は command cp -f で DXF-visual-diff へ再同期してください。"
    )
