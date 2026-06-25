"""
utils.pairing コア（UI 非依存）のユニットテスト。

streamlit に依存しないため app.py をインポートせず、コアを直接検証する。

実行:
    cd DXF-diff-manager
    python -m tests.unit.test_pairing
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

from utils import pairing
from utils.pairing import (
    build_pairs,
    build_pairs_from_list,
    find_revup_pairs,
    extract_base_drawing_number,
    primary_status_by_drawing,
    drawings_with_status,
    compute_unchanged_drawings,
    get_brand_new_drawing_pairs,
    compute_total_drawings_count,
    normalize_pair_list_columns,
    RELATION_REVUP,
    RELATION_DEPENDENCY,
    RELATION_PAIR_LIST,
    STATUS_COMPLETE,
    STATUS_MISSING_SOURCE,
    STATUS_MISSING_TARGET,
    STATUS_MISSING_BOTH,
    STATUS_ONE_SIDED,
    STATUS_IDENTICAL,
    STATUS_NO_SOURCE_DEFINED,
)


def _f(dn, source=None, title=None):
    return {
        'filename': f'{dn}.dxf', 'temp_path': f'/tmp/{dn}.dxf',
        'main_drawing_number': dn, 'source_drawing_number': source, 'title': title,
    }


def _keys(pairs, status=None, relation=None):
    return {
        (p['main_drawing'], p['source_drawing']) for p in pairs
        if (status is None or p['status'] == status)
        and (relation is None or p['relation'] == relation)
    }


# --- extract_base_drawing_number ---

def test_extract_base_halfwidth():
    assert extract_base_drawing_number('DE5313-008-02B') == ('DE5313-008-02', 'B')


def test_extract_base_fullwidth():
    assert extract_base_drawing_number('DE5313-008-02Ｂ') == ('DE5313-008-02', 'Ｂ')


def test_extract_base_no_revision():
    assert extract_base_drawing_number('DE5313-008-02') == (None, None)
    assert extract_base_drawing_number('') == (None, None)
    assert extract_base_drawing_number('A') == (None, None)


# --- find_revup_pairs ---

def test_find_revup_consecutive():
    pool = {dn: _f(dn) for dn in ['EE6333-365-61A', 'EE6333-365-61B', 'EE6333-365-61C']}
    revup, used_src, used_tgt = find_revup_pairs(pool, pool)
    keys = {(p['main_drawing'], p['source_drawing']) for p in revup}
    assert keys == {('EE6333-365-61B', 'EE6333-365-61A'), ('EE6333-365-61C', 'EE6333-365-61B')}
    assert all(p['relation'] == RELATION_REVUP and p['status'] == STATUS_COMPLETE for p in revup)


def test_find_revup_cross_groups():
    source = {'EE6333-365-61A': _f('EE6333-365-61A')}
    target = {'EE6333-365-61B': _f('EE6333-365-61B')}
    revup, _, _ = find_revup_pairs(source, target)
    assert _keys(revup) == {('EE6333-365-61B', 'EE6333-365-61A')}


# --- build_pairs (mode A: pool, pool) ---

def test_build_pairs_single_pool_revup_when_source_missing():
    pool = {
        'EE6333-365-61C': _f('EE6333-365-61C', source='EE6331-365-61A'),  # 別系統・未UP
        'EE6333-365-61B': _f('EE6333-365-61B'),
    }
    pairs = build_pairs(pool, pool)
    assert ('EE6333-365-61C', 'EE6333-365-61B') in _keys(pairs, relation=RELATION_REVUP)
    assert ('EE6333-365-61C', 'EE6331-365-61A') in _keys(pairs, status=STATUS_MISSING_SOURCE)


def test_build_pairs_single_pool_same_target_twice():
    pool = {
        'EE6333-365-61C': _f('EE6333-365-61C', source='XX9999-000-01A'),
        'EE6333-365-61B': _f('EE6333-365-61B'),
        'XX9999-000-01A': _f('XX9999-000-01A'),
    }
    pairs = build_pairs(pool, pool)
    rels = {p['relation'] for p in pairs if p['main_drawing'] == 'EE6333-365-61C'}
    assert rels == {RELATION_REVUP, RELATION_DEPENDENCY}


def test_build_pairs_single_pool_revup_source_not_orphan():
    pool = {'EE6333-365-61C': _f('EE6333-365-61C'), 'EE6333-365-61B': _f('EE6333-365-61B')}
    pairs = build_pairs(pool, pool)
    orphans = {p['main_drawing'] for p in pairs if p['status'] == STATUS_NO_SOURCE_DEFINED}
    assert orphans == set()


def test_build_pairs_single_pool_isolated_orphan():
    pool = {'EE6666-610-05A': _f('EE6666-610-05A')}
    pairs = build_pairs(pool, pool)
    assert len(pairs) == 1 and pairs[0]['status'] == STATUS_NO_SOURCE_DEFINED


# --- build_pairs (mode B: source, target) ---

def test_build_pairs_auto_independent_passes():
    source = {'EE6333-365-61B': _f('EE6333-365-61B')}
    target = {'EE6333-365-61C': _f('EE6333-365-61C', source='EE6331-365-61A')}
    pairs = build_pairs(source, target)
    assert ('EE6333-365-61C', 'EE6333-365-61B') in _keys(pairs, relation=RELATION_REVUP)
    assert ('EE6333-365-61C', 'EE6331-365-61A') in _keys(pairs, status=STATUS_MISSING_SOURCE)


def test_build_pairs_auto_exact_dup_dedup():
    source = {'EE6333-365-61B': _f('EE6333-365-61B')}
    target = {'EE6333-365-61C': _f('EE6333-365-61C', source='EE6333-365-61B')}
    pairs = build_pairs(source, target)
    c_to_b = [p for p in pairs if (p['main_drawing'], p['source_drawing']) == ('EE6333-365-61C', 'EE6333-365-61B')]
    assert len(c_to_b) == 1 and c_to_b[0]['relation'] == RELATION_REVUP


def test_build_pairs_auto_plain_dependency():
    source = {'EE6097-039-06C': _f('EE6097-039-06C')}
    target = {'EE6321-039-06A': _f('EE6321-039-06A', source='EE6097-039-06C')}
    pairs = build_pairs(source, target)
    assert ('EE6321-039-06A', 'EE6097-039-06C') in _keys(pairs, status=STATUS_COMPLETE, relation=RELATION_DEPENDENCY)


def test_build_pairs_progress_callback_invoked():
    calls = []
    source = {'A1A': _f('A1A')}
    target = {'A1B': _f('A1B', source='A1A')}
    build_pairs(source, target, progress_callback=lambda *a: calls.append(a))
    assert calls and calls[0][0] == 0.0 and calls[-1][0] == 1.0


# --- build_pairs_from_list (mode C) ---

def test_build_pairs_from_list_statuses():
    files = {'A': _f('A'), 'C': _f('C')}
    df = pd.DataFrame({
        '流用元図番': ['A', 'A', 'X', 'A', '',  'A'],
        '流用先図番': ['C', 'A', 'C', 'Z', 'C', ''],
    })
    pairs = build_pairs_from_list(df, files)
    statuses = [p['status'] for p in pairs]
    assert statuses == [
        STATUS_COMPLETE,          # A->C 両方有
        STATUS_IDENTICAL,         # A->A 同一
        STATUS_MISSING_SOURCE,    # X(無)->C(有)
        STATUS_MISSING_TARGET,    # A(有)->Z(無)
        STATUS_NO_SOURCE_DEFINED, # 空白->C（流用先はあるが流用元の記載なし＝完全新規図面）
        STATUS_ONE_SIDED,         # A->空白（流用先が空白で比較対象がない）
    ]
    assert all(p['relation'] == RELATION_PAIR_LIST for p in pairs)


def test_build_pairs_from_list_missing_both():
    df = pd.DataFrame({'流用元図番': ['X'], '流用先図番': ['Y']})
    pairs = build_pairs_from_list(df, {})
    assert pairs[0]['status'] == STATUS_MISSING_BOTH


# --- primary_status_by_drawing / drawings_with_status (UI 表示の二重計上防止) ---
#
# 2026-06: 同じ流用先図番（main_drawing）が複数ステータスのペアに登場するケースで、
# Step3 の各セクション集計（差分抽出が可能なペア / 流用元図番の図面がない図面 /
# 変更していない図面 等）の合計が流用先総数と一致しなくなる実バグが見つかった。
# 実データ（sample-dxf）でも RevUp パスと流用パスの両方が同一の流用先図番に対し
# 異なるステータスのペアを生成するケースが確認されている。

def test_primary_status_prefers_complete_over_missing_source_revup_case():
    """方式A/B: 同一の流用先が RevUp(complete) と 流用(missing_source) の両方に登場するケース。"""
    source = {'X002A': _f('X002A')}
    target = {'X002B': _f('X002B', source='Y999')}  # Y999 は未アップロード
    pairs = build_pairs(source, target)
    primary = primary_status_by_drawing(pairs)
    assert primary == {'X002B': STATUS_COMPLETE}


def test_primary_status_prefers_complete_over_missing_source_duplicate_target_row():
    """方式C: ペアリストに同一の流用先図番が複数行（流用元が異なる）あるケース。"""
    df = pd.DataFrame({'流用元図番': ['A1', 'A2'], '流用先図番': ['B1', 'B1']})
    files = {'A1': {'x': 1}, 'B1': {'x': 1}}  # A2 は未アップロード
    pairs = build_pairs_from_list(df, files)
    primary = primary_status_by_drawing(pairs)
    assert primary == {'B1': STATUS_COMPLETE}


def test_primary_status_prefers_complete_over_identical_duplicate_target_row():
    """方式C: 同一の流用先図番が identical 行と complete 行の両方に登場するケース。"""
    df = pd.DataFrame({'流用元図番': ['B1', 'A9'], '流用先図番': ['B1', 'B1']})
    files = {'B1': {'x': 1}, 'A9': {'x': 1}}
    pairs = build_pairs_from_list(df, files)
    primary = primary_status_by_drawing(pairs)
    assert primary == {'B1': STATUS_COMPLETE}


def test_drawings_with_status_excludes_blank_main_drawing():
    df = pd.DataFrame({'流用元図番': ['A1'], '流用先図番': ['']})
    pairs = build_pairs_from_list(df, {'A1': {'x': 1}})
    assert pairs[0]['status'] == STATUS_ONE_SIDED
    assert drawings_with_status(pairs, STATUS_ONE_SIDED) == set()


# --- compute_unchanged_drawings / get_brand_new_drawing_pairs (mode='auto') ---
#
# 2026-06: app.py から utils.pairing へ移動（session_state 直読みから引数渡しへ
# 引数化）。'pair_list' モードの挙動は tests/regression/test_brand_new_drawing.py
# で既にカバーされているため、ここでは 'auto' モード（source/dest_drawing_numbers
# 引数を使う分岐）を確認する。

def test_compute_unchanged_drawings_auto_mode_requires_common_pool_membership():
    """方式B: no_source_defined でも、流用元・流用先の両プールに同名ファイルが
    存在しなければ「変更していない図面」には含めない。"""
    source = {'A1A': _f('A1A')}
    target = {'A1A': _f('A1A')}  # 同じプールに同名図番が存在
    pairs = build_pairs(source, target)
    result = compute_unchanged_drawings(
        pairs, 'auto',
        source_drawing_numbers=set(source.keys()),
        dest_drawing_numbers=set(target.keys()),
    )
    assert result == {'A1A'}


def test_compute_unchanged_drawings_auto_mode_empty_pools_returns_empty():
    pairs = build_pairs({'A1A': _f('A1A')}, {'A1A': _f('A1A')})
    result = compute_unchanged_drawings(pairs, 'auto')  # 引数省略時は空集合扱い
    assert result == set()


def test_compute_unchanged_drawings_all_in_one_mode_returns_empty():
    pairs = build_pairs({'A1A': _f('A1A')}, {'A1A': _f('A1A')})
    assert compute_unchanged_drawings(pairs, 'all_in_one') == set()


# --- compute_total_drawings_count ---

def test_compute_total_drawings_count_all_in_one():
    assert compute_total_drawings_count('all_in_one', all_in_one_count=5, dest_count=99) == 5


def test_compute_total_drawings_count_auto():
    assert compute_total_drawings_count('auto', all_in_one_count=99, dest_count=7) == 7


def test_compute_total_drawings_count_pair_list():
    df = pd.DataFrame({'流用先図番': ['A', 'B', 'C', '']})
    assert compute_total_drawings_count(
        'pair_list', pair_list_df=df, uploaded_drawing_numbers={'A', 'B'}
    ) == 2


def test_compute_total_drawings_count_pair_list_no_df():
    assert compute_total_drawings_count('pair_list', pair_list_df=None) == 0


# --- normalize_pair_list_columns ---

def test_normalize_pair_list_columns_renames_legacy_names():
    df = pd.DataFrame({'比較元図番': ['A'], '比較先図番': ['B']})
    result, error = normalize_pair_list_columns(df)
    assert error is None
    assert list(result.columns) == ['流用元図番', '流用先図番']
    assert result.iloc[0]['流用元図番'] == 'A'


def test_normalize_pair_list_columns_missing_required_column():
    df = pd.DataFrame({'流用元図番': ['A']})
    result, error = normalize_pair_list_columns(df)
    assert result is None
    assert '流用先図番' in error


def test_normalize_pair_list_columns_drops_fully_blank_rows():
    df = pd.DataFrame({'流用元図番': ['A', ''], '流用先図番': ['B', '']})
    result, error = normalize_pair_list_columns(df)
    assert error is None
    assert len(result) == 1


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    for t in tests:
        try:
            t(); print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append(t.__name__); print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
