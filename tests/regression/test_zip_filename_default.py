"""
ZIPダウンロードファイル名のデフォルト値（compute_default_zip_basename）の仕様テスト。

背景:
    Ledger-merger 側で「指番_モジュール_サイド」単位の集計機能を追加するにあたり、
    DXF-diff-manager 側のZIPダウンロードファイル名を
    "dxf_diff_results_Type{A/B/C}_{指番}_{モジュール}_{サイド}" という
    命名規則で自動生成・編集可能にした。指番/モジュール/サイドが逆算できない場合
    （台帳を作成していない、または命名規則に一致しない台帳をアップロードした場合）は
    従来通り "dxf_diff_results" のみとする。

    末尾のリビジョン識別子（旧: 常時付与していた "_01"）は 2026-09 に廃止した。
    同じ指番-モジュール-サイドで繰り返し実行する際に個別保存したい場合は、
    ユーザーが「ファイル名を確定」前に手動で "_01" 等を追記する運用に変更している。

実行:
    cd DXF-diff-manager
    python -m tests.regression.test_zip_filename_default
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import app


def test_new_master_all_in_one_mode():
    """指番_モジュール_サイド.xlsx 形式の台帳 + Type A（all_in_one）→ Type A で命名。"""
    result = app.compute_default_zip_basename("ME24-1001-0_ZC00_405.xlsx", "all_in_one")
    assert result == "dxf_diff_results_TypeA_ME24-1001-0_ZC00_405", result


def test_auto_mode_uses_type_b():
    result = app.compute_default_zip_basename("ME24-1001-0_ZC00_405.xlsx", "auto")
    assert result == "dxf_diff_results_TypeB_ME24-1001-0_ZC00_405", result


def test_pair_list_mode_uses_type_c():
    result = app.compute_default_zip_basename("ME24-1001-0_ZC00_405.xlsx", "pair_list")
    assert result == "dxf_diff_results_TypeC_ME24-1001-0_ZC00_405", result


def test_module_and_side_unspecified_uses_na():
    """モジュール・サイド未入力時は master_file_name 側で既に "na" になっている。"""
    result = app.compute_default_zip_basename("ME24-1001-0_na_na.xlsx", "all_in_one")
    assert result == "dxf_diff_results_TypeA_ME24-1001-0_na_na", result


def test_no_master_falls_back_to_legacy_name():
    """台帳を作成していない（step0_mode == 'none'）場合は master_file_name が None。"""
    result = app.compute_default_zip_basename(None, "all_in_one")
    assert result == "dxf_diff_results", result


def test_unparseable_uploaded_master_filename_falls_back_to_legacy_name():
    """命名規則に一致しない台帳をアップロードした場合も従来名にフォールバックする。"""
    result = app.compute_default_zip_basename("my_old_master_file.xlsx", "auto")
    assert result == "dxf_diff_results", result


def test_uploaded_master_filename_with_underscore_suffix_still_parses():
    """指番_モジュール_サイドの後ろに "_" 区切りで接尾辞が付いた台帳
    （例: Ledger-merger が生成する "..._all.xlsx" を再アップロードした場合）でも
    指番/モジュール/サイドを認識できる（2026-08）。"""
    result = app.compute_default_zip_basename("ME24-1001-0_ZC00_405_all.xlsx", "all_in_one")
    assert result == "dxf_diff_results_TypeA_ME24-1001-0_ZC00_405", result


def test_default_basename_has_no_trailing_revision_suffix():
    """2026-09 の仕様変更: デフォルト値の末尾に "_01" 等のリビジョン識別子が
    付かないこと（旧仕様との回帰防止）。"""
    result = app.compute_default_zip_basename("ME24-1001-0_ZC00_405.xlsx", "all_in_one")
    assert not result.endswith("_01"), result
    assert result == "dxf_diff_results_TypeA_ME24-1001-0_ZC00_405"


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
