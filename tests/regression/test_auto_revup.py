"""
方式 B（auto）の create_pair_list における
流用ペア・RevUp ペアの検出優先順位に関する回帰テスト。

背景:
    2026-06: 従来 auto モードは RevUp ペアを最優先で消費し、消費された流用先は
    流用判定の対象外だった。方式 A と挙動を揃え、流用判定と RevUp 判定を独立して
    実行し、両方のペアを出力する（同一流用先が双方に登場し得る）よう変更した。
    2026-08-29: しかし「同一流用先が双方に登場し得る」仕様は、流用元・RevUp相手の
    両ファイルが揃っている場合に同じ図面へ2回差分抽出が走る不具合の原因になって
    いた。Drawing-genealogy プロジェクトの「流用とRevUpが競合する場合は流用関係を
    削除してRevUpだけを採用する」仕様に合わせ、RevUp が検出された流用先について
    は流用パスでのペア生成自体を行わない（RevUp優先・流用削除）よう変更した。

実行:
    cd DXF-diff-manager
    python -m tests.regression.test_auto_revup
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import app


def _src(drawing_number):
    """流用元（旧）ファイル情報。"""
    return {
        'filename': f'{drawing_number}.dxf',
        'temp_path': f'/tmp/{drawing_number}.dxf',
        'main_drawing_number': drawing_number,
        'title': None,
        'subtitle': None,
    }


def _dst(drawing_number, source=None):
    """流用先（新）ファイル情報。"""
    return {
        'filename': f'{drawing_number}.dxf',
        'temp_path': f'/tmp/{drawing_number}.dxf',
        'main_drawing_number': drawing_number,
        'source_drawing_number': source,
        'title': None,
        'subtitle': None,
    }


def _keys(pairs, status=None, relation=None):
    return {
        (p['main_drawing'], p['source_drawing'])
        for p in pairs
        if (status is None or p['status'] == status)
        and (relation is None or p['relation'] == relation)
    }


def test_revup_wins_over_missing_source():
    """RevUp が検出された流用先は、別系統の流用元が未アップロード(missing_source)でも
    流用ペアを作らず RevUp のみが残る。"""
    source = {'EE6333-365-61B': _src('EE6333-365-61B')}
    dest = {'EE6333-365-61C': _dst('EE6333-365-61C', source='EE6331-365-61A')}  # 流用元は別系統・未UP
    pairs = app.create_pair_list(source, dest)

    assert ('EE6333-365-61C', 'EE6333-365-61B') in _keys(pairs, status='complete', relation='RevUp'), \
        f"RevUp(61C×61B)が検出されていない: {pairs}"
    # 流用元 ...61A は流用元グループに無く、かつ RevUp と競合するため流用ペア自体が作られない
    assert ('EE6333-365-61C', 'EE6331-365-61A') not in _keys(pairs, status='missing_source'), \
        f"RevUp と競合する流用 missing_source(61C×61A)が残ってしまっている: {pairs}"
    rels = {p['relation'] for p in pairs if p['main_drawing'] == 'EE6333-365-61C'}
    assert rels == {'RevUp'}, f"61C は RevUp のみが残るべき: {pairs}"


def test_revup_wins_over_complete_dependency():
    """RevUp が検出された流用先は、別の流用元ファイルが揃っていて流用が complete に
    なり得る場合でも、流用ペアを作らず RevUp のみが残る（同一図面への二重差分抽出防止）。"""
    source = {
        'EE6333-365-61B': _src('EE6333-365-61B'),
        'XX9999-000-01A': _src('XX9999-000-01A'),
    }
    dest = {'EE6333-365-61C': _dst('EE6333-365-61C', source='XX9999-000-01A')}
    pairs = app.create_pair_list(source, dest)
    targets_61c = [p for p in pairs if p['main_drawing'] == 'EE6333-365-61C']
    assert len(targets_61c) == 1, f"61C は RevUp1件のみが残るべき（二重登録防止）: {pairs}"
    rels = {p['relation'] for p in targets_61c}
    assert rels == {'RevUp'}, f"61C は RevUp のみが残るべき: {pairs}"


def test_exact_revup_pair_not_duplicated():
    """流用元図番が RevUp 相手と完全一致する場合は二重登録しない。"""
    source = {'EE6333-365-61B': _src('EE6333-365-61B')}
    dest = {'EE6333-365-61C': _dst('EE6333-365-61C', source='EE6333-365-61B')}
    pairs = app.create_pair_list(source, dest)
    c_to_b = [p for p in pairs if (p['main_drawing'], p['source_drawing']) == ('EE6333-365-61C', 'EE6333-365-61B')]
    assert len(c_to_b) == 1, f"(61C×61B) が二重登録されている: {c_to_b}"
    assert c_to_b[0]['relation'] == 'RevUp', f"重複排除後は RevUp が残るべき: {c_to_b[0]}"


def test_revup_target_not_flagged_no_source_defined():
    """RevUp 対応済みで流用元図番が無い流用先を孤立扱いしない。"""
    source = {'EE6333-365-61B': _src('EE6333-365-61B')}
    dest = {'EE6333-365-61C': _dst('EE6333-365-61C', source=None)}
    pairs = app.create_pair_list(source, dest)
    orphans = {p['main_drawing'] for p in pairs if p['status'] == 'no_source_defined'}
    assert 'EE6333-365-61C' not in orphans, f"RevUp 流用先 61C が孤立扱い: {pairs}"
    assert _keys(pairs, relation='RevUp') == {('EE6333-365-61C', 'EE6333-365-61B')}


def test_plain_dependency_pair_still_works():
    """通常の流用ペア（RevUp 無し・完全一致）が引き続き complete になる。"""
    source = {'EE6097-039-06C': _src('EE6097-039-06C')}
    dest = {'EE6321-039-06A': _dst('EE6321-039-06A', source='EE6097-039-06C')}
    pairs = app.create_pair_list(source, dest)
    assert ('EE6321-039-06A', 'EE6097-039-06C') in _keys(pairs, status='complete', relation='流用'), \
        f"通常の流用ペアが complete になっていない: {pairs}"


def test_missing_source_when_no_revup():
    """RevUp 相手も流用元も無い場合は従来通り missing_source。"""
    source = {}
    dest = {'EE6333-365-61C': _dst('EE6333-365-61C', source='EE6330-000-00A')}
    pairs = app.create_pair_list(source, dest)
    assert _keys(pairs, status='missing_source') == {('EE6333-365-61C', 'EE6330-000-00A')}


def test_no_source_defined_when_isolated():
    """流用元図番が無く RevUp 相手も無い流用先は no_source_defined。"""
    source = {}
    dest = {'EE6666-610-05A': _dst('EE6666-610-05A', source=None)}
    pairs = app.create_pair_list(source, dest)
    assert len(pairs) == 1 and pairs[0]['status'] == 'no_source_defined'


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
