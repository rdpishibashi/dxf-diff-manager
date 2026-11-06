"""
このテストが守るもの: DXF-extract-labels（正本）からバイト一致でコピーしている共有ファイルが、
正本と乖離していないこと。

- `model/label_classifier.py`（機器符号候補・領域名候補の判定）
- `model/designator_master.py`（機器符号一覧の索引。diff_labels.xlsx の DEF 判定、2026-11-06〜）
- `assets/designator_digests.txt`（機器符号一覧のダイジェスト。鍵は git 管理外の secrets）

背景（2026-10-17）: 以前のガード（旧 ref_designator_patterns.py の一致テスト）は、正本側で
ファイルが label_classifier.py へ改名された後も、比較先が無いことを「DXF-extract-labels が
隣にチェックアウトされていない」と取り違えて skip し続け、乖離を検出できなかった。
そのため skip してよいのは隣のディレクトリ自体が無いときだけにし、ディレクトリがあるのに
ファイルが無いときは fail させる。2026-11-06 に旧名 `test_label_classifier_identical_to_extract_labels.py`
から改名し、対象を3ファイルに広げた。

実行:
    cd DXF-diff-manager
    python -m pytest tests/regression/spec/test_shared_files_identical_to_extract_labels.py
"""
import hashlib
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_PRIMARY_DIR = os.path.join(_ROOT, "..", "DXF-extract-labels")

SHARED_FILES = [
    "model/label_classifier.py",
    "model/designator_master.py",
    "assets/designator_digests.txt",
]


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


@pytest.mark.parametrize("relpath", SHARED_FILES)
def test_shared_file_is_byte_identical_to_extract_labels(relpath):
    if not os.path.isdir(_PRIMARY_DIR):
        pytest.skip(f"DXF-extract-labels が隣にチェックアウトされていません (期待パス: {_PRIMARY_DIR})")

    primary = os.path.join(_PRIMARY_DIR, relpath)
    copy = os.path.join(_ROOT, relpath)
    assert os.path.exists(primary), (
        f"DXF-extract-labels はあるが正本 {primary} が無い（改名・移動された可能性）。"
        "このガードの比較先を更新すること。skip で済ませない。"
    )
    assert _md5(primary) == _md5(copy), (
        f"{relpath} が DXF-extract-labels の正本と乖離しています。再同期:\n"
        f"  command cp -f ../DXF-extract-labels/{relpath} {relpath}"
    )
