"""
このテストが守るもの: DXF-diff-manager の model/label_classifier.py が
DXF-extract-labels の model/label_classifier.py（正本）とバイト一致であること。

背景（2026-10-17）: 以前のガード（旧 ref_designator_patterns.py の一致テスト）は、正本側で
ファイルが label_classifier.py へ改名された後も、比較先が無いことを「DXF-extract-labels が
隣にチェックアウトされていない」と取り違えて skip し続け、乖離を検出できなかった。
そのため skip してよいのは隣のディレクトリ自体が無いときだけにし、ディレクトリがあるのに
ファイルが無いときは fail させる。

実行:
    cd DXF-diff-manager
    python -m pytest tests/regression/spec/test_label_classifier_identical_to_extract_labels.py
"""
import hashlib
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_PRIMARY_DIR = os.path.join(_ROOT, "..", "DXF-extract-labels")
_PRIMARY = os.path.join(_PRIMARY_DIR, "model", "label_classifier.py")
_COPY = os.path.join(_ROOT, "model", "label_classifier.py")


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def test_label_classifier_is_byte_identical_to_extract_labels():
    if not os.path.isdir(_PRIMARY_DIR):
        pytest.skip(f"DXF-extract-labels が隣にチェックアウトされていません (期待パス: {_PRIMARY_DIR})")

    assert os.path.exists(_PRIMARY), (
        f"DXF-extract-labels はあるが正本 {_PRIMARY} が無い（改名・移動された可能性）。"
        "このガードの比較先を更新すること。skip で済ませない。"
    )
    assert _md5(_PRIMARY) == _md5(_COPY), (
        "model/label_classifier.py が DXF-extract-labels の正本と乖離しています。再同期:\n"
        "  command cp -f ../DXF-extract-labels/model/label_classifier.py model/label_classifier.py"
    )
