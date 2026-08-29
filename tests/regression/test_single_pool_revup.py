"""
方式 A（all_in_one）の create_pairs_from_single_pool における
流用ペア・RevUp ペアの検出優先順位に関する回帰テスト。

背景:
    2026-06: 流用元図番がプールに完全一致で存在しない場合に missing_source とする
    だけで、同一ベース図番の別リビジョン（RevUp 相手）がプール内にあっても検出して
    いなかった。流用判定と RevUp 判定を独立して行い、両方のペアを出力するよう修正した。
    2026-08-29: しかし「同一流用先が双方に登場し得る」仕様は、流用元・RevUp相手の
    両ファイルが揃っている場合に同じ図面へ2回差分抽出が走る不具合の原因になって
    いた。Drawing-genealogy プロジェクトの「流用とRevUpが競合する場合は流用関係を
    削除してRevUpだけを採用する」仕様に合わせ、RevUp が検出された流用先について
    は流用パスでのペア生成自体を行わない（RevUp優先・流用削除）よう変更した。

実行:
    cd DXF-diff-manager
    python -m tests.regression.test_single_pool_revup
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import app


def _f(drawing_number, source=None):
    """テスト用の最小ファイル情報 dict を作る。"""
    return {
        'filename': f'{drawing_number}.dxf',
        'temp_path': f'/tmp/{drawing_number}.dxf',
        'main_drawing_number': drawing_number,
        'source_drawing_number': source,
    }


def _keys(pairs, status=None, relation=None):
    return {
        (p['main_drawing'], p['source_drawing'])
        for p in pairs
        if (status is None or p['status'] == status)
        and (relation is None or p['relation'] == relation)
    }


def test_revup_wins_over_missing_source():
    """流用元図番がプールに無くても RevUp 相手があれば検出し、その場合は流用パスの
    missing_source ペアは作られない（RevUp優先・流用削除）。"""
    pool = {
        # ...61C は流用元として ...61A を記載しているが ...61A はプールに無い
        'EE6333-365-61C': _f('EE6333-365-61C', source='EE6333-365-61A'),
        # ...61B はプール内にある → ...61C の RevUp 相手
        'EE6333-365-61B': _f('EE6333-365-61B', source=None),
    }
    pairs = app.create_pairs_from_single_pool(pool)

    revup = _keys(pairs, status='complete', relation='RevUp')
    assert ('EE6333-365-61C', 'EE6333-365-61B') in revup, \
        f"RevUp ペア(61C×61B)が検出されていない: {pairs}"

    # RevUp と競合するため、流用元 ...61A の missing_source ペアは作られない
    missing = _keys(pairs, status='missing_source')
    assert ('EE6333-365-61C', 'EE6333-365-61A') not in missing, \
        f"RevUp と競合する流用 missing_source(61C×61A)が残ってしまっている: {pairs}"


def test_revup_wins_over_complete_dependency():
    """RevUp が検出された流用先は、別の流用元ファイルが揃っていて流用が complete に
    なり得る場合でも、流用ペアを作らず RevUp のみが残る（同一図面への二重差分抽出防止）。"""
    pool = {
        'EE6333-365-61C': _f('EE6333-365-61C', source='XX9999-000-01A'),  # 別系統の流用
        'EE6333-365-61B': _f('EE6333-365-61B', source=None),
        'XX9999-000-01A': _f('XX9999-000-01A', source=None),
    }
    pairs = app.create_pairs_from_single_pool(pool)
    targets_61c = [p for p in pairs if p['main_drawing'] == 'EE6333-365-61C']
    assert len(targets_61c) == 1, f"61C は RevUp1件のみが残るべき（二重登録防止）: {targets_61c}"
    relations = {p['relation'] for p in targets_61c}
    assert relations == {'RevUp'}, \
        f"61C は RevUp のみが残るべき: {targets_61c}"


def test_exact_revup_pair_not_duplicated():
    """流用元図番が RevUp 相手と完全一致する場合は二重登録しない。"""
    pool = {
        'EE6333-365-61C': _f('EE6333-365-61C', source='EE6333-365-61B'),
        'EE6333-365-61B': _f('EE6333-365-61B', source=None),
    }
    pairs = app.create_pairs_from_single_pool(pool)
    c_to_b = [p for p in pairs if (p['main_drawing'], p['source_drawing']) == ('EE6333-365-61C', 'EE6333-365-61B')]
    assert len(c_to_b) == 1, f"(61C×61B) が二重登録されている: {c_to_b}"
    assert c_to_b[0]['relation'] == 'RevUp', f"重複排除後は RevUp が残るべき: {c_to_b[0]}"


def test_revup_source_not_flagged_no_source_defined():
    """RevUp 相手として使われた旧リビジョンは孤立(no_source_defined)扱いしない。"""
    pool = {
        'EE6333-365-61C': _f('EE6333-365-61C', source=None),
        'EE6333-365-61B': _f('EE6333-365-61B', source=None),
    }
    pairs = app.create_pairs_from_single_pool(pool)
    orphans = {p['main_drawing'] for p in pairs if p['status'] == 'no_source_defined'}
    assert 'EE6333-365-61B' not in orphans, f"RevUp 流用元 61B が孤立扱い: {pairs}"
    assert 'EE6333-365-61C' not in orphans, f"RevUp 流用先 61C が孤立扱い: {pairs}"
    assert _keys(pairs, relation='RevUp') == {('EE6333-365-61C', 'EE6333-365-61B')}


def test_plain_dependency_pair_still_works():
    """従来の流用ペア（完全一致・RevUp 無し）が引き続き complete になる。"""
    pool = {
        'EE6321-039-06A': _f('EE6321-039-06A', source='EE6097-039-06C'),
        'EE6097-039-06C': _f('EE6097-039-06C', source=None),
    }
    pairs = app.create_pairs_from_single_pool(pool)
    complete = _keys(pairs, status='complete', relation='流用')
    assert ('EE6321-039-06A', 'EE6097-039-06C') in complete, \
        f"通常の流用ペアが complete になっていない: {pairs}"


def test_isolated_file_flagged_no_source_defined():
    """流用元もRevUp相手も無い孤立ファイルは no_source_defined。"""
    pool = {
        'EE6666-610-05A': _f('EE6666-610-05A', source=None),
    }
    pairs = app.create_pairs_from_single_pool(pool)
    assert len(pairs) == 1
    assert pairs[0]['status'] == 'no_source_defined'


def test_three_revisions_consecutive_pairing():
    """同一ベースに3リビジョンある場合は連続ペア(A→B, B→C)を生成する。"""
    pool = {
        'EE6333-365-61A': _f('EE6333-365-61A', source=None),
        'EE6333-365-61B': _f('EE6333-365-61B', source=None),
        'EE6333-365-61C': _f('EE6333-365-61C', source=None),
    }
    pairs = app.create_pairs_from_single_pool(pool)
    revup = _keys(pairs, relation='RevUp')
    assert revup == {
        ('EE6333-365-61B', 'EE6333-365-61A'),
        ('EE6333-365-61C', 'EE6333-365-61B'),
    }, f"連続 RevUp ペアになっていない: {revup}"


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
