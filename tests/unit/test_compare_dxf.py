"""
model.compare_dxf の EntityExpander に関するユニットテスト（UI 非依存）。

off/frozen（非表示）レイヤー上のエンティティが、ビジュアル差分の抽出対象から
除外されることを検証する。重なった旧タイトルブロック・改訂履歴メモ等が
off/frozen レイヤーに残っている DXF で、新旧同一の不可視テキストが UNCHANGED
として差分DXFに描画される不具合の回帰テスト（実データ
EE2505-611-79B_vs_79A で確認: ブロック JZB_0004 の MTEXT 'EE2505-611-57B' 等が
off+frozen レイヤー上にあった）。

実行:
    cd DXF-diff-manager
    python -m tests.unit.test_compare_dxf
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ezdxf

from model.compare_dxf import (
    ToleranceConfig, CoordinateTransformer, EntityExpander,
    SignatureGenerator, DiffAnalyzer, compare_dxf_files_and_generate_dxf,
    count_entities_in_dxf_file,
)


def _make_expander():
    tol = ToleranceConfig(0.01)
    transformer = CoordinateTransformer(tol, debug=False)
    return EntityExpander(transformer, debug=False)


def _diff_counts(doc_old, doc_new, ignore_color=False):
    """2ドキュメントを比較し (deleted, added, unchanged) の署名数を返す。"""
    tol = ToleranceConfig(0.01)
    tr = CoordinateTransformer(tol, debug=False)
    da = DiffAnalyzer(SignatureGenerator(tr, debug=False, ignore_color=ignore_color), debug=False)
    ea, _, _, _ = da.extract_entities_from_doc(doc_old, "OLD", EntityExpander(tr))
    eb, _, _, _ = da.extract_entities_from_doc(doc_new, "NEW", EntityExpander(tr))
    sa, sb = set(ea), set(eb)
    return len(sa - sb), len(sb - sa), len(sa & sb)


def _texts_from_expansion(doc):
    expander = _make_expander()
    entities = expander.expand_insert_entities(doc, "test")
    return {e.get('text_content') for e in entities if e.get('text_content')}


def _prepare_doc():
    doc = ezdxf.new()
    # 可視レイヤー
    doc.layers.add('VISIBLE')
    # off レイヤー
    off = doc.layers.add('HIDDEN_OFF')
    off.off()
    # frozen レイヤー
    frozen = doc.layers.add('HIDDEN_FROZEN')
    frozen.freeze()
    return doc


def test_direct_modelspace_entities_on_hidden_layers_excluded():
    """modelspace 直下の off/frozen レイヤー上テキストは除外される。"""
    doc = _prepare_doc()
    msp = doc.modelspace()
    msp.add_text('VISIBLE_TEXT', dxfattribs={'layer': 'VISIBLE'})
    msp.add_text('OFF_TEXT', dxfattribs={'layer': 'HIDDEN_OFF'})
    msp.add_text('FROZEN_TEXT', dxfattribs={'layer': 'HIDDEN_FROZEN'})

    texts = _texts_from_expansion(doc)
    assert 'VISIBLE_TEXT' in texts
    assert 'OFF_TEXT' not in texts
    assert 'FROZEN_TEXT' not in texts


def test_block_entities_on_hidden_layers_excluded():
    """ブロック定義内の off/frozen レイヤー上テキストは、可視 INSERT 経由でも除外される。

    実データの不具合（ブロック JZB_0004 の MTEXT が off+frozen レイヤーにあり
    UNCHANGED に描画された）に対応する中核ケース。
    """
    doc = _prepare_doc()
    blk = doc.blocks.new('BLK')
    blk.add_text('BLK_VISIBLE', dxfattribs={'layer': 'VISIBLE'})
    blk.add_text('BLK_OFF', dxfattribs={'layer': 'HIDDEN_OFF'})
    blk.add_text('BLK_FROZEN', dxfattribs={'layer': 'HIDDEN_FROZEN'})
    # 継承レイヤー '0'（INSERT のレイヤーを継承）→ INSERT が可視なら表示される
    blk.add_text('BLK_LAYER0', dxfattribs={'layer': '0'})

    msp = doc.modelspace()
    msp.add_blockref('BLK', (0, 0), dxfattribs={'layer': 'VISIBLE'})

    texts = _texts_from_expansion(doc)
    assert 'BLK_VISIBLE' in texts
    assert 'BLK_LAYER0' in texts
    assert 'BLK_OFF' not in texts
    assert 'BLK_FROZEN' not in texts


def test_insert_on_hidden_layer_excludes_all_contents():
    """INSERT 自身が off/frozen レイヤーにある場合、参照全体が除外される。"""
    doc = _prepare_doc()
    blk = doc.blocks.new('BLK2')
    blk.add_text('INSIDE1', dxfattribs={'layer': 'VISIBLE'})
    blk.add_text('INSIDE2', dxfattribs={'layer': '0'})

    msp = doc.modelspace()
    # INSERT を off レイヤーに配置 → 中身は全て非表示
    msp.add_blockref('BLK2', (0, 0), dxfattribs={'layer': 'HIDDEN_OFF'})

    texts = _texts_from_expansion(doc)
    assert 'INSIDE1' not in texts
    assert 'INSIDE2' not in texts


def test_visible_content_preserved():
    """通常の可視レイヤー・レイヤー'0' の図形は従来どおり抽出される（過剰除外がない）。"""
    doc = _prepare_doc()
    msp = doc.modelspace()
    msp.add_text('DEFAULT_LAYER0', dxfattribs={'layer': '0'})
    msp.add_text('ON_VISIBLE', dxfattribs={'layer': 'VISIBLE'})

    texts = _texts_from_expansion(doc)
    assert 'DEFAULT_LAYER0' in texts
    assert 'ON_VISIBLE' in texts


# --- MTEXT フォーマットコードの署名正規化 ---

def test_mtext_differing_format_codes_treated_as_unchanged():
    """同じ見た目・同じ位置の MTEXT が、\\W 幅係数・\\T 文字間隔コードだけ異なる場合に
    UNCHANGED と判定される（DELETED+ADDED の偽差分を出さない）。

    実データ EE6588-405C_vs_405B で、改訂時に再計算された \\W/\\T の僅差により
    同一ラベル約313件が DELETED+ADDED に誤判定された不具合の回帰テスト。
    diff_labels.xlsx は plain_mtext でこれらを除去済みのため変化なしと出るのに、
    差分DXFだけ大量の偽差分が出ていた。
    """
    old = ezdxf.new()
    old.modelspace().add_mtext('EE6588-405', dxfattribs={'insert': (10, 20)}).text = \
        r'\A1;\W0.892749;\T0.892749;EE6588-405'
    new = ezdxf.new()
    new.modelspace().add_mtext('EE6588-405', dxfattribs={'insert': (10, 20)}).text = \
        r'\A1;\W0.883176;\T0.883176;EE6588-405'

    deleted, added, unchanged = _diff_counts(old, new)
    assert deleted == 0, f"偽の DELETED が出た: {deleted}"
    assert added == 0, f"偽の ADDED が出た: {added}"
    assert unchanged == 1


def test_mtext_genuinely_different_text_is_detected():
    """フォーマットコード除去後の本文が異なる MTEXT は、正しく差分として検出される
    （過剰な同一視で本物の変更を見逃さない）。"""
    old = ezdxf.new()
    old.modelspace().add_mtext('x', dxfattribs={'insert': (10, 20)}).text = \
        r'\A1;\W0.9;\T0.9;EE6588-405B'
    new = ezdxf.new()
    new.modelspace().add_mtext('x', dxfattribs={'insert': (10, 20)}).text = \
        r'\A1;\W0.9;\T0.9;EE6588-405C'

    deleted, added, unchanged = _diff_counts(old, new)
    assert deleted == 1
    assert added == 1


# --- compare_dxf_files_and_generate_dxf: file_old/file_new と OLD_DELETED/NEW_ADDED の対応 ---

def test_file_old_only_is_deleted_file_new_only_is_added():
    """compare_dxf_files_and_generate_dxf() は file_old のみに存在するエンティティを
    OLD_DELETED、file_new のみに存在するエンティティを NEW_ADDED として出力する契約を
    保証する。

    呼び出し元（model/diff_export.py）がこの契約と逆の順で新旧ファイルを渡すと、
    NEW_ADDED/OLD_DELETED レイヤーの内容が入れ替わる不具合が実際に発生した（実データ
    EE4144-613-49D_vs_49C で確認: ADDED レイヤーに旧図面自身のテキスト
    'EE4144-613-49C' が、DELETED レイヤーに新図面自身のテキスト 'EE4144-613-49D' が
    混入していた）。file_old=旧、file_new=新で呼ぶことが正しい契約であることを固定する
    （2026-09-18、オフセット補正機能の組み込みに伴いA/B→OLD/NEW命名統一。レイヤー名も
    DELETED/ADDED→OLD_DELETED/NEW_ADDEDへ変更）。
    """
    import tempfile

    doc_old = ezdxf.new()  # 旧ファイル役
    doc_old.modelspace().add_text('ONLY_IN_OLD', dxfattribs={'insert': (0, 0)})
    doc_new = ezdxf.new()  # 新ファイル役
    doc_new.modelspace().add_text('ONLY_IN_NEW', dxfattribs={'insert': (100, 100)})

    with tempfile.TemporaryDirectory() as d:
        path_old = os.path.join(d, 'old.dxf')
        path_new = os.path.join(d, 'new.dxf')
        out_path = os.path.join(d, 'out.dxf')
        doc_old.saveas(path_old)
        doc_new.saveas(path_new)

        ok, counts = compare_dxf_files_and_generate_dxf(path_old, path_new, out_path)
        assert ok
        assert counts['deleted_entities'] == 1
        assert counts['added_entities'] == 1

        out_doc = ezdxf.readfile(out_path)
        by_layer = {}
        for e in out_doc.modelspace():
            if e.dxftype() == 'TEXT':
                by_layer[getattr(e.dxf, 'layer', '')] = e.dxf.text

        assert by_layer.get('OLD_DELETED') == 'ONLY_IN_OLD'
        assert by_layer.get('NEW_ADDED') == 'ONLY_IN_NEW'


# --- ignore_color_only_changes: 座標・形状が一致し color だけ異なる場合の扱い ---

def test_color_only_difference_detected_by_default():
    """デフォルト（ignore_color=False）では、座標・形状が一致していても color が
    異なれば DELETED+ADDED として検出される（既存の挙動を変えない）。"""
    old = ezdxf.new()
    old.modelspace().add_line((0, 0), (10, 10), dxfattribs={'color': 7})
    new = ezdxf.new()
    new.modelspace().add_line((0, 0), (10, 10), dxfattribs={'color': 4})

    deleted, added, unchanged = _diff_counts(old, new, ignore_color=False)
    assert deleted == 1
    assert added == 1
    assert unchanged == 0


def test_color_only_difference_treated_as_unchanged_when_ignored():
    """ignore_color=True では、座標・形状（start/end）が完全一致し color だけが
    異なる LINE/CIRCLE は UNCHANGED として扱われる。

    実データ EE2505-633-43E_vs_43D で確認: 改訂マーキングと思われる色変更
    （lineweight・color を伴う）により、座標・形状が完全一致するのに334ペアが
    DELETED+ADDED として誤検出（に見える形）で出力されていた。color だけを
    署名から除外することで、これらを UNCHANGED として扱えるようにした
    （R1ラベル近傍の抵抗シンボルLINE・F8ラベル近傍のヒューズCIRCLEで確認）。
    """
    old = ezdxf.new()
    old.modelspace().add_line((0, 0), (10, 10), dxfattribs={'color': 7, 'lineweight': 25})
    new = ezdxf.new()
    new.modelspace().add_line((0, 0), (10, 10), dxfattribs={'color': 4, 'lineweight': 50})

    deleted, added, unchanged = _diff_counts(old, new, ignore_color=True)
    assert deleted == 0
    assert added == 0
    assert unchanged == 1

    # CIRCLE でも同様に確認
    old_c = ezdxf.new()
    old_c.modelspace().add_circle((5, 5), radius=4.2, dxfattribs={'color': 7})
    new_c = ezdxf.new()
    new_c.modelspace().add_circle((5, 5), radius=4.2, dxfattribs={'color': 4})

    deleted_c, added_c, unchanged_c = _diff_counts(old_c, new_c, ignore_color=True)
    assert deleted_c == 0
    assert added_c == 0
    assert unchanged_c == 1


def test_ignore_color_does_not_hide_genuine_position_changes():
    """ignore_color=True でも、実際に位置・形状が異なるエンティティは正しく
    差分として検出される（過剰な同一視で本物の変更を見逃さない）。"""
    old = ezdxf.new()
    old.modelspace().add_line((0, 0), (10, 10), dxfattribs={'color': 7})
    new = ezdxf.new()
    new.modelspace().add_line((0, 0), (20, 20), dxfattribs={'color': 7})  # 終点が違う

    deleted, added, unchanged = _diff_counts(old, new, ignore_color=True)
    assert deleted == 1
    assert added == 1


def test_ignore_color_only_changes_end_to_end_via_compare_dxf_files():
    """compare_dxf_files_and_generate_dxf() の ignore_color_only_changes 引数が、
    実際のファイル読み込み経路でも正しく機能する（app.py の呼び出しと同じ経路）。"""
    import tempfile

    old_doc = ezdxf.new()
    old_doc.modelspace().add_line((0, 0), (10, 10), dxfattribs={'color': 7})
    new_doc = ezdxf.new()
    new_doc.modelspace().add_line((0, 0), (10, 10), dxfattribs={'color': 4})

    with tempfile.TemporaryDirectory() as d:
        old_path = os.path.join(d, 'old.dxf')
        new_path = os.path.join(d, 'new.dxf')
        out_path = os.path.join(d, 'out.dxf')
        old_doc.saveas(old_path)
        new_doc.saveas(new_path)

        ok, counts = compare_dxf_files_and_generate_dxf(
            old_path, new_path, out_path, ignore_color_only_changes=True)
        assert ok
        assert counts['deleted_entities'] == 0
        assert counts['added_entities'] == 0
        assert counts['unchanged_entities'] == 1


def test_count_entities_in_dxf_file_respects_ignore_color_only_changes():
    """count_entities_in_dxf_file()（完全新規図面の Total Entities 算出に使用）も
    ignore_color_only_changes と整合していることを確認する。

    dev-workflow スキルの選択肢組み合わせ表で「ignore_color_only_changes ×
    count_entities_in_dxf_file」を影響あり→要対応と判定した組み合わせ。揃えないと、
    Deleted/Added/Unchanged Entities は color 無視で数えているのに、完全新規図面の
    Total Entities だけ color を区別して数えてしまい、台帳内で定義が食い違う。
    """
    import tempfile

    doc = ezdxf.new()
    msp = doc.modelspace()
    # 同一座標・同一形状で color だけ異なる LINE を2本配置
    msp.add_line((0, 0), (10, 10), dxfattribs={'color': 7})
    msp.add_line((0, 0), (10, 10), dxfattribs={'color': 4})

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'brand_new.dxf')
        doc.saveas(path)

        count_default = count_entities_in_dxf_file(path)
        assert count_default == 2  # color込みで別エンティティとして2つ数える

        count_ignored = count_entities_in_dxf_file(path, ignore_color_only_changes=True)
        assert count_ignored == 1  # color を無視すると同一エンティティとして1つに集約


# --- オフセット補正（offset_new）との組み合わせ（2026-09-18、dev-workflowの ---
# --- 選択肢組み合わせ表「オフセット補正 × ignore_color_only_changes」「× pair_cache」---

def test_offset_new_ignores_color_only_when_ignore_color_enabled():
    """offset_new（手動オフセット指定）と ignore_color_only_changes を併用した場合、
    位置がオフセット分ずれておりcolorだけが異なるエンティティが UNCHANGED_OFFSET
    として一致する（ignore_color_only_changes=False の場合は color差が残るため
    一致しない）。

    signature_generator は SignatureGenerator(ignore_color=...) として構築され、
    offset_detector.detect_offsets() にも同じ signature_fn が注入されるため、
    ignore_color の設定はオフセット一致判定にも一貫して効くはずという契約を
    固定する（dev-workflowスキルの組み合わせ表で「影響あり→要テスト」とした
    表B: オフセット補正 × ignore_color_only_changes）。
    """
    import tempfile

    old_doc = ezdxf.new()
    old_doc.modelspace().add_line((0, 0), (10, 10), dxfattribs={'color': 7})
    new_doc = ezdxf.new()
    # NEW側は (5, 5) だけ平行移動、かつ color が異なる
    new_doc.modelspace().add_line((5, 5), (15, 15), dxfattribs={'color': 4})

    with tempfile.TemporaryDirectory() as d:
        old_path = os.path.join(d, 'old.dxf')
        new_path = os.path.join(d, 'new.dxf')

        old_doc.saveas(old_path)
        new_doc.saveas(new_path)

        # ignore_color_only_changes=False（既定）: color差が残るためオフセット一致しない
        out_path1 = os.path.join(d, 'out1.dxf')
        ok1, counts1 = compare_dxf_files_and_generate_dxf(
            old_path, new_path, out_path1, offset_new=(-5, -5),
            ignore_color_only_changes=False)
        assert ok1
        assert counts1['deleted_entities'] == 1
        assert counts1['added_entities'] == 1
        assert counts1['unchanged_offset_entities'] == 0

        # ignore_color_only_changes=True: color差を無視するためオフセット一致する
        out_path2 = os.path.join(d, 'out2.dxf')
        ok2, counts2 = compare_dxf_files_and_generate_dxf(
            old_path, new_path, out_path2, offset_new=(-5, -5),
            ignore_color_only_changes=True)
        assert ok2
        assert counts2['deleted_entities'] == 0
        assert counts2['added_entities'] == 0
        assert counts2['unchanged_offset_entities'] == 1
        assert counts2['unchanged_offset_old_entities'] == 1


def test_pair_cache_with_offset_produces_same_result_as_without_cache():
    """pair_cache（バッチ内のファイル再解析回避キャッシュ）を使っても、オフセット
    補正（offset_new）が有効な場合の差分結果が pair_cache 無しの場合と完全一致する
    （dev-workflowスキルの組み合わせ表で「影響あり→要テスト」とした表G:
    オフセット補正 × pair_cache）。

    2026-09-18の変更で、和集合型オフセット補正への移行に伴い pair_cache の
    NEW側キャッシュキーが (file_path, offset_new) から (file_path, None) に
    単純化された（NEW側は常に生座標で展開されるため）。同じOLDファイルが
    複数ペアで再利用されるケースを模して、キャッシュ有無で結果が変わらないことを
    確認する。
    """
    import tempfile
    from model.compare_dxf import PairFileCache

    old_doc = ezdxf.new()
    old_doc.modelspace().add_line((0, 0), (10, 10), dxfattribs={'color': 7})
    new_doc = ezdxf.new()
    new_doc.modelspace().add_line((5, 5), (15, 15), dxfattribs={'color': 7})

    with tempfile.TemporaryDirectory() as d:
        old_path = os.path.join(d, 'old.dxf')
        new_path = os.path.join(d, 'new.dxf')
        old_doc.saveas(old_path)
        new_doc.saveas(new_path)

        # キャッシュ無しでの結果
        out_no_cache = os.path.join(d, 'out_no_cache.dxf')
        ok_nc, counts_no_cache = compare_dxf_files_and_generate_dxf(
            old_path, new_path, out_no_cache, offset_new=(-5, -5), pair_cache=None)
        assert ok_nc

        # 同じOLDファイルを2ペアで再利用するキャッシュ（バッチ処理を模す）
        cache = PairFileCache([(old_path, None), (old_path, None), (new_path, None), (new_path, None)])
        out_cached_1 = os.path.join(d, 'out_cached_1.dxf')
        ok_c1, counts_cached_1 = compare_dxf_files_and_generate_dxf(
            old_path, new_path, out_cached_1, offset_new=(-5, -5), pair_cache=cache)
        out_cached_2 = os.path.join(d, 'out_cached_2.dxf')
        ok_c2, counts_cached_2 = compare_dxf_files_and_generate_dxf(
            old_path, new_path, out_cached_2, offset_new=(-5, -5), pair_cache=cache)

        assert ok_c1 and ok_c2
        assert counts_no_cache == counts_cached_1 == counts_cached_2
        assert counts_no_cache['unchanged_offset_entities'] == 1
        assert counts_no_cache['unchanged_offset_old_entities'] == 1


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append(t.__name__); print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
