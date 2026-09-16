"""
Step4「差分抽出結果」の結果詳細テーブル（result_data）が、完全新規図面の行と
通常ペアの行が混在するときに pyarrow のシリアライズエラーを起こさないことを
保証する回帰テスト。

不具合の識別子: 2026-09-16 ユーザー報告
    「Serialization of dataframe to Arrow table was unsuccessful」で始まる
    警告トレースバック（末尾: "Could not convert '-' with type str: tried to
    convert to int64", 'Conversion failed for column 削除図形数 with type object'）
    がコンソールに出力される。

以前どう壊れていたか:
    完全新規図面（流用元の参照が無い図面）の entity_counts は
    {'added_entities': count, 'total_entities': count} のみで
    'deleted_entities' キーを持たない（model/diff_export.py 参照）。
    app.py の結果詳細テーブル組み立てで `entity_counts.get('deleted_entities', '-')`
    としていたため、通常ペアの行（int）と完全新規図面の行（'-' 文字列）が同じ
    '削除図形数'（同様に'変更ラベル数'）列に混在する object dtype の DataFrame が
    できる。これを直接 st.dataframe() に渡すと、pyarrow が先頭値から列型を
    int64 と推測し、後続の '-' の変換に失敗してエラートレースバックをログ出力
    する（表示自体はStreamlitが自動フォールバックするため機能は壊れないが、
    ログを汚す）。
    同種の混在は図面管理台帳の Deleted Entities 等カラムで既知で、
    model/master_ledger.py の make_dataframe_arrow_compatible() が対応済み
    だったが、Step4結果詳細テーブル（app.pyの別の表示箇所）には適用されて
    いなかった。

修正後に保証したいこと:
    通常ペア（int）と完全新規図面（'-'）の行が混在する結果テーブルに対して
    make_dataframe_arrow_compatible() を適用すると、pyarrow.Table.from_pandas()
    がエラーなく成功する。

実行:
    cd DXF-diff-manager
    python -m pytest tests/regression/bugfix/test_result_table_arrow_compatible.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import pandas as pd
import pyarrow as pa

from model.master_ledger import make_dataframe_arrow_compatible


def _build_mixed_result_data():
    """通常ペア（entity_countsにdeleted_entitiesあり）と完全新規図面
    （entity_countsにdeleted_entitiesなし）が混在する結果テーブルを模す
    （app.py::render_step3_diff() の result_data 組み立てと同じ形）。"""
    rows = []
    for result in [
        {'main_drawing': 'A', 'source_drawing': 'B', 'output_filename': 'A_vs_B.dxf',
         'relation': 'RevUp', 'entity_counts': {'deleted_entities': 3, 'added_entities': 5, 'total_entities': 20},
         'change_label_count': 2, 'success': True},
        {'main_drawing': 'C', 'source_drawing': 'none', 'output_filename': 'C_vs_none.dxf',
         'relation': '完全新規図面', 'entity_counts': {'added_entities': 7, 'total_entities': 7},
         'change_label_count': None, 'success': True},
    ]:
        entity_counts = result.get('entity_counts')
        row = {
            '流用先（新）': result['main_drawing'],
            '流用元（旧）': result['source_drawing'],
            '出力ファイル名': result['output_filename'],
            '関係': result.get('relation', 'なし'),
        }
        if entity_counts:
            row['削除図形数'] = entity_counts.get('deleted_entities', '-')
            row['追加図形数'] = entity_counts.get('added_entities', '-')
            row['総図形数'] = entity_counts.get('total_entities', '-')
        else:
            row['削除図形数'] = '-'
            row['追加図形数'] = '-'
            row['総図形数'] = '-'
        row['変更ラベル数'] = result.get('change_label_count', '-')
        row['ステータス'] = "✅ 成功" if result['success'] else "❌ 失敗"
        rows.append(row)
    return rows


def test_mixed_entity_counts_fail_arrow_conversion_without_fix():
    """修正の前提確認: 素のDataFrameのままだとArrow変換が失敗することを示す
    （このテストがpassしなくなったら、pyarrow側の挙動が変わったということ）。"""
    df = pd.DataFrame(_build_mixed_result_data())
    try:
        pa.Table.from_pandas(df)
        raised = False
    except Exception:
        raised = True
    assert raised, "前提が崩れている: 素のDataFrameでもArrow変換が成功してしまう"


def test_make_dataframe_arrow_compatible_fixes_result_table():
    """make_dataframe_arrow_compatible() を適用すると、通常ペア（int）と
    完全新規図面（'-'）が混在する結果テーブルでもArrow変換が成功する。"""
    df = pd.DataFrame(_build_mixed_result_data())
    fixed = make_dataframe_arrow_compatible(df)

    # Arrow変換がエラーを出さないこと
    pa.Table.from_pandas(fixed)

    # 混在していた列（削除図形数: int と '-' が混在）は表示用に文字列へ統一される
    assert fixed.loc[0, '削除図形数'] == '3'
    assert fixed.loc[1, '削除図形数'] == '-'
    # 混在していない列（追加図形数: どちらの行もint）は純粋な数値のまま
    # （右寄せ表示を保つため、make_dataframe_arrow_compatible は変換しない）
    assert fixed.loc[0, '追加図形数'] == 5
    assert fixed.loc[1, '追加図形数'] == 7


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
