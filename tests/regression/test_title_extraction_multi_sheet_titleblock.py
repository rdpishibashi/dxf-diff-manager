"""
複数シートを1つのDXFにまとめた図面の、Title/Subtitle抽出に関する回帰テスト。

背景:
    sample-dxf/problems/DE3527-553-10E.dxf は同一図番のタイトルブロックが
    1ファイル内に5枚横並びに配置された図面で、最右端（このファイル自体に
    対応するシート）のタイトルブロックだけ「ELECTRICAL SCHEMATIC DIAGRAM」の
    テキストが元々欠落している。

    model/extract_labels.py の extract_title_and_subtitle() は、
    extract_drawing_numbers_option=True を渡した場合にのみ計算される
    main_drawing_group を使って、同一タイトルブロック内のラベルのみに
    候補を絞り込む（2026-07-12 修正）。ところが model/label_diff.py の
    _load_labels_with_cache() と model/diff_export.py の完全新規図面
    個別抽出が extract_drawing_numbers_option を渡していなかったため、
    この絞り込みが働かず、全ブロック横断でタイトル候補を探してしまい、
    対象ブロック内の別の行（サブタイトル・リビジョン文字・ページ数）を
    誤ってタイトル/サブタイトルとして抽出していた
    （title="ＩＮＰＵＴ ＬＡ／ＬＢ H"、subtitle="DE3527-553-10E 5 5" のように
    混入する）。DXF-extract-labels（primary）は常に両オプションをセットで
    渡しており問題が出ていなかった（2026-07-29 ユーザー報告）。

    修正: label_diff.py の _load_labels_with_cache() に
    extract_drawing_numbers_option=True を追加し、original_filename も
    貫通できるようにした。diff_export.py の完全新規図面個別抽出も
    extract_drawing_numbers_option=False → True に変更した。

実行:
    cd DXF-diff-manager
    python -m tests.regression.test_title_extraction_multi_sheet_titleblock
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from model.extract_labels import extract_labels
from model.label_diff import compute_label_differences

SAMPLE_DXF = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'sample-dxf', 'problems', 'DE3527-553-10E.dxf',
)

EXPECTED_TITLE = 'ELECTRICAL SCHEMATIC DIAGRAM'
EXPECTED_SUBTITLE = 'ＩＮＰＵＴ ＬＡ／ＬＢ'  # ラベル間は半角スペースで連結される（' '.join）


def test_extract_labels_with_drawing_numbers_option_picks_correct_titleblock():
    """extract_drawing_numbers_option=True を渡せば正しいタイトルブロックに絞り込まれる。"""
    _, info = extract_labels(
        SAMPLE_DXF,
        extract_drawing_numbers_option=True,
        extract_title_option=True,
        original_filename='DE3527-553-10E.dxf',
    )
    assert info.get('title') == EXPECTED_TITLE, f"title={info.get('title')!r}"
    assert info.get('subtitle') == EXPECTED_SUBTITLE, f"subtitle={info.get('subtitle')!r}"


def test_compute_label_differences_uses_correct_titleblock_group():
    """label_diff.compute_label_differences() が同じDXFに対して正しいTitle/Subtitleを返す。

    old_file には同じDXFを流用元として渡す（差分内容自体はこのテストの対象外）。
    """
    _, _, extra_info = compute_label_differences(
        SAMPLE_DXF,
        SAMPLE_DXF,
        new_file_original_name='DE3527-553-10E.dxf',
    )
    assert extra_info.get('title') == EXPECTED_TITLE, f"title={extra_info.get('title')!r}"
    assert extra_info.get('subtitle') == EXPECTED_SUBTITLE, f"subtitle={extra_info.get('subtitle')!r}"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append((t.__name__, str(e)))
            print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
