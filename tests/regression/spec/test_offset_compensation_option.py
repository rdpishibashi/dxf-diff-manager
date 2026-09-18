"""
このテストが守るもの: Step4「オフセット補正を行う」オプションが
create_diff_zip()（延いては compare_dxf_files_and_generate_dxf()）の
offset_detection 引数として正しく伝わり、ON/OFF でレイヤー構成・エンティティ件数
・図面管理台帳の値が期待どおり切り替わること。

背景（2026-09-18）:
    DXF-visual-diffで実装済みのオフセット補正機能（変更がなく平行移動した一定の
    図形グループを「変化なし」と判定する機能）をDXF-diff-managerへ組み込んだ。
    Step4画面にチェックボックス（既定ON）として追加し、config.pyのしきい値
    （AUTO_OFFSET_*）で検出条件を制御する。

対応する受入条件（ユーザー承認済み）:
    1. offset_detection=None（チェックボックスOFF相当）の場合、移動した図形群は
       従来どおり OLD_DELETED/NEW_ADDED として検出される
       （UNCHANGED_OFFSET系レイヤーは作られない）。
    2. offset_detection が指定され（チェックボックスON相当）、しきい値
       （min_matches・min_distinct_shapes）を満たす移動が検出された場合、
       その図形群は OLD_UNCHANGED_OFFSET/NEW_UNCHANGED_OFFSET に分類され、
       OLD_DELETED/NEW_ADDED からは除外される。
    3. entity_counts の unchanged_offset_entities（NEW側）・
       unchanged_offset_old_entities（OLD側）が検出件数を反映する。
    4. Master台帳の Unchanged Offset Entities 列に NEW側の件数が記録される
       （2026-09-18新設。詳細は tests/unit/test_master_ledger.py 参照）。

実行:
    cd DXF-diff-manager
    python -m pytest tests/regression/spec/test_offset_compensation_option.py -v
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf
import pandas as pd

from model.diff_export import create_diff_zip
from model.offset_detector import OffsetDetectionConfig


def _build_moved_block_pair(d, main_drawing='NEW-OFS', source_drawing='OLD-OFS',
                             shift=(50.0, 30.0)):
    """3種類の形状（CIRCLE半径1・2・3）から成る「ブロック」を、NEW側だけ
    (shift)分だけ平行移動して配置したペアを作る。

    OLD/NEWで共通の1個の円（radius=99、原点）も置き、常に一致する
    「本当に変更がない」要素として扱う（この1件は offset_detection の
    有無に関わらず常に common_hashes に入り UNCHANGED になる）。
    """
    old_doc = ezdxf.new()
    old_msp = old_doc.modelspace()
    old_msp.add_circle(center=(0, 0), radius=99.0, dxfattribs={'layer': '0'})  # 常時一致
    for i, r in enumerate([1.0, 2.0, 3.0]):
        old_msp.add_circle(center=(i * 20.0, 1000.0), radius=r, dxfattribs={'layer': '0'})

    new_doc = ezdxf.new()
    new_msp = new_doc.modelspace()
    new_msp.add_circle(center=(0, 0), radius=99.0, dxfattribs={'layer': '0'})  # 常時一致
    dx, dy = shift
    for i, r in enumerate([1.0, 2.0, 3.0]):
        new_msp.add_circle(center=(i * 20.0 + dx, 1000.0 + dy), radius=r, dxfattribs={'layer': '0'})

    old_path = os.path.join(d, f'{source_drawing}.dxf')
    new_path = os.path.join(d, f'{main_drawing}.dxf')
    old_doc.saveas(old_path)
    new_doc.saveas(new_path)

    pair = {
        'main_drawing': main_drawing,
        'source_drawing': source_drawing,
        'main_file_info': {'temp_path': new_path, 'title': None, 'subtitle': None},
        'source_file_info': {'temp_path': old_path},
        'status': 'complete',
        'relation': 'RevUp',
        'title': None,
        'subtitle': None,
    }
    return [pair]


def _low_threshold_config():
    """合成データ（3件・3形状）でも採用条件①を満たす緩いしきい値。
    本番のconfig.py既定値（min_matches=10, min_distinct_shapes=5）は
    実データ規模を前提にしており、この合成テストでは満たせないため、
    しきい値の意味そのものをテストするのではなく「検出パイプラインが
    正しく繋がっているか」を確認する目的で緩めた値を使う。"""
    return OffsetDetectionConfig(
        min_matches=3,
        min_distinct_shapes=3,
        max_offsets=50,
        max_candidates=200,
        max_instances_per_shape=8,
        compact_min_matches=2,
        compact_min_distinct_shapes=2,
        compact_max_span=1000.0,
    )


def test_offset_detection_none_keeps_moved_block_as_deleted_and_added():
    """offset_detection=None の場合、移動したブロックは OLD_DELETED/NEW_ADDED の
    まま検出される（UNCHANGED_OFFSET系レイヤーは作られない）。"""
    with tempfile.TemporaryDirectory() as d:
        pairs = _build_moved_block_pair(d)
        _, results, _, _, _ = create_diff_zip(pairs, offset_detection=None)

        assert results[0]['success']
        counts = results[0]['entity_counts']
        assert counts['deleted_entities'] == 3  # OLD側のブロック3個
        assert counts['added_entities'] == 3    # NEW側のブロック3個
        assert counts['unchanged_entities'] == 1  # 常時一致の円のみ
        assert counts['unchanged_offset_entities'] == 0
        assert counts['unchanged_offset_old_entities'] == 0
        assert counts['detected_offsets'] == []


def test_offset_detection_enabled_reclassifies_moved_block_as_unchanged_offset():
    """offset_detection を指定すると、しきい値を満たす移動ブロックが
    OLD_UNCHANGED_OFFSET/NEW_UNCHANGED_OFFSET に分類され、
    OLD_DELETED/NEW_ADDED からは除外される。"""
    with tempfile.TemporaryDirectory() as d:
        pairs = _build_moved_block_pair(d)
        _, results, _, _, _ = create_diff_zip(pairs, offset_detection=_low_threshold_config())

        assert results[0]['success']
        counts = results[0]['entity_counts']
        assert counts['deleted_entities'] == 0
        assert counts['added_entities'] == 0
        assert counts['unchanged_entities'] == 1  # 常時一致の円（オフセット関係なし）
        assert counts['unchanged_offset_entities'] == 3      # NEW側の移動ブロック
        assert counts['unchanged_offset_old_entities'] == 3  # OLD側の移動ブロック
        assert len(counts['detected_offsets']) == 1
        detected = counts['detected_offsets'][0]
        assert detected['matches'] == 3
        assert detected['shapes'] == 3
        # Total = Deleted + Added + Unchanged(純粋一致) + UnchangedOffset(NEW側)
        assert counts['total_entities'] == (
            counts['deleted_entities'] + counts['added_entities']
            + counts['unchanged_entities'] + counts['unchanged_offset_entities']
        )
        assert counts['total_old_entities'] == counts['deleted_entities'] + counts['unchanged_entities'] + counts['unchanged_offset_old_entities']


# 図面管理台帳（Master）の Unchanged Offset Entities 列への反映は、この時点では
# まだ未実装（master_ledger.py 側の対応は別途行う）。
# → tests/unit/test_master_ledger.py::test_unchanged_offset_entities_column 参照
# （_build_moved_block_pair と同じ合成データパターンを使って検証する）。


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
