"""
図面管理台帳（Parent-Child マスター）の読み込み・更新・Excel出力（UI 非依存のモデル層）。

streamlit には依存しないため、`tests/` から直接ユニットテストできる
（`utils/pairing.py` と同じ方針）。
"""
from io import BytesIO
from datetime import datetime

import pandas as pd


def load_parent_child_master(uploaded_file):
    """
    図面管理台帳ファイルを読み込む

    Args:
        uploaded_file: アップロードされたExcelファイル（ファイルパスやファイルオブジェクト）

    Returns:
        tuple: (DataFrame または None, エラーメッセージ または None)
    """
    try:
        df = pd.read_excel(uploaded_file)

        # 必要なカラムが存在するか確認
        required_columns = ['Child', 'Parent']
        for col in required_columns:
            if col not in df.columns:
                return None, f"必須カラム '{col}' が見つかりません。"

        return df, None

    except Exception as e:
        return None, f"図面管理台帳ファイルの読み込み中にエラーが発生しました: {str(e)}"


def update_parent_child_master(master_df, new_pairs):
    """
    図面管理台帳に新しいペアを追加、もしくは既存ペアを更新する

    Args:
        master_df: 既存の図面管理台帳DataFrame
        new_pairs: 新しいペア情報のリスト

    Returns:
        tuple: (更新されたDataFrame, 追加された件数)
    """
    added_count = 0
    new_records = []
    updated_df = master_df.copy()

    entity_count_columns = ['Deleted Entities', 'Added Entities', 'Diff Entities',
                            'Unchanged Entities', 'Total Entities']

    for pair in new_pairs:
        parent = pair.get('source_drawing')  # 流用元図番がParent
        child = pair.get('main_drawing')      # 図番がChild
        title = pair.get('title')
        subtitle = pair.get('subtitle')
        relation = pair.get('relation')       # 'RevUp' / '流用' / 完全新規図面など
        entity_counts = pair.get('entity_counts')  # エンティティ数情報

        if not child:
            continue

        # 流用元が存在しない（完全新規図面）場合、Parent欄は "none" とする
        # （流用元の参照なしを明示する。2026-06 追加）
        is_brand_new = not parent
        parent_value = parent if parent else 'none'

        # 既存のレコードに同じ親子関係が存在するか確認
        mask = (updated_df['Parent'] == parent_value) & (updated_df['Child'] == child)
        exists = mask.any()

        if exists:
            # 既存レコードを更新（Child/Parent/Noteは保持）
            current_date = datetime.now()

            # 必要な列が存在しない場合は追加（文字列型として明示）
            if 'Relation' not in updated_df.columns:
                updated_df['Relation'] = pd.Series(dtype='object')
            if 'Title' not in updated_df.columns:
                updated_df['Title'] = pd.Series(dtype='object')
            if 'Subtitle' not in updated_df.columns:
                updated_df['Subtitle'] = pd.Series(dtype='object')
            if 'Recorded Date' not in updated_df.columns:
                # 古い'Date'列があれば'Recorded Date'にリネーム
                if 'Date' in updated_df.columns:
                    updated_df.rename(columns={'Date': 'Recorded Date'}, inplace=True)
                else:
                    updated_df['Recorded Date'] = None

            # エンティティ数カラムを追加（存在しない場合）
            # object dtype: 通常は整数、完全新規図面の行では "n/a" 文字列も入るため
            for col in entity_count_columns:
                if col not in updated_df.columns:
                    updated_df[col] = pd.Series(dtype='object')

            if 'Note' not in updated_df.columns:
                updated_df['Note'] = pd.Series(dtype='object')

            if relation:
                prev_relation_series = updated_df.loc[mask, 'Relation']
                relation_to_set = relation
                if prev_relation_series.notna().any():
                    prev_unique = prev_relation_series.dropna().unique()
                    if len(prev_unique) > 0 and prev_unique[0] != relation:
                        relation_to_set = f"{relation}-changed"
                updated_df.loc[mask, 'Relation'] = relation_to_set

            updated_df.loc[mask, 'Title'] = title
            updated_df.loc[mask, 'Subtitle'] = subtitle
            updated_df.loc[mask, 'Recorded Date'] = current_date

            # エンティティ数を更新
            # 完全新規図面（流用元なし）: 比較を行っていないため Added=Total（その図面
            # 自体の総エンティティ数）とし、それ以外（Deleted/Diff/Unchanged）は
            # 比較対象が存在しないため "n/a" を明示する（2026-06 追加）。
            if is_brand_new:
                updated_df.loc[mask, 'Deleted Entities'] = 'n/a'
                updated_df.loc[mask, 'Diff Entities'] = 'n/a'
                updated_df.loc[mask, 'Unchanged Entities'] = 'n/a'
                if entity_counts:
                    updated_df.loc[mask, 'Added Entities'] = entity_counts.get('added_entities')
                    updated_df.loc[mask, 'Total Entities'] = entity_counts.get('total_entities')
            elif entity_counts:
                updated_df.loc[mask, 'Deleted Entities'] = entity_counts.get('deleted_entities')
                updated_df.loc[mask, 'Added Entities'] = entity_counts.get('added_entities')
                updated_df.loc[mask, 'Diff Entities'] = entity_counts.get('diff_entities')
                updated_df.loc[mask, 'Unchanged Entities'] = entity_counts.get('unchanged_entities')
                updated_df.loc[mask, 'Total Entities'] = entity_counts.get('total_entities')
        else:
            # 新しいレコードを追加
            new_record = {
                'Child': child,
                'Parent': parent_value,
                'Relation': relation,
                'Title': title,
                'Subtitle': subtitle,
                'Recorded Date': datetime.now()
            }

            # エンティティ数を追加（完全新規図面は上記と同じ規則。2026-06 追加）
            if is_brand_new:
                new_record['Deleted Entities'] = 'n/a'
                new_record['Diff Entities'] = 'n/a'
                new_record['Unchanged Entities'] = 'n/a'
                if entity_counts:
                    new_record['Added Entities'] = entity_counts.get('added_entities')
                    new_record['Total Entities'] = entity_counts.get('total_entities')
            elif entity_counts:
                new_record['Deleted Entities'] = entity_counts.get('deleted_entities')
                new_record['Added Entities'] = entity_counts.get('added_entities')
                new_record['Diff Entities'] = entity_counts.get('diff_entities')
                new_record['Unchanged Entities'] = entity_counts.get('unchanged_entities')
                new_record['Total Entities'] = entity_counts.get('total_entities')

            new_records.append(new_record)
            added_count += 1

    if new_records:
        for record in new_records:
            for key in record.keys():
                if key not in updated_df.columns:
                    updated_df[key] = pd.Series(dtype='object')
            updated_df.loc[len(updated_df)] = record

    return updated_df, added_count


def make_dataframe_arrow_compatible(df):
    """object 型カラムに数値と文字列が混在した DataFrame を Arrow 互換にした
    表示用コピーを返す（元の df は変更しない）。

    図面管理台帳のエントリ数カラム（Deleted Entities 等）は、完全新規図面の行で
    'n/a' 文字列、通常のペアの行で整数、という混在 object カラムになる（この
    'n/a' 混在は本モジュールの update_parent_child_master が付与する仕様）。これを
    そのまま st.dataframe に渡すと pyarrow が先頭値から列型を int と推測し、後続の
    'n/a' で変換に失敗して警告（トレースバック）をログ出力する。表示のみの問題で
    Streamlit が自動フォールバックするため機能は動くが、ログを汚すため事前に
    混在カラムの非NULL値を文字列へ統一しておく。数値のみ・文字列のみ・日時などの
    純粋なカラムはそのまま（数値の右寄せ表示等を保つため）。
    """
    display_df = df.copy()
    for col in display_df.columns:
        if display_df[col].dtype != object:
            continue
        non_null = [v for v in display_df[col] if not pd.isna(v)]
        has_str = any(isinstance(v, str) for v in non_null)
        has_non_str = any(not isinstance(v, str) for v in non_null)
        if has_str and has_non_str:
            display_df[col] = display_df[col].map(lambda v: v if pd.isna(v) else str(v))
    return display_df


def create_empty_master_df():
    """空の図面管理台帳DataFrameを作成（図面管理台帳.xlsx のフォーマットに準拠）"""
    return pd.DataFrame({
        'Child': pd.Series(dtype='object'),
        'Parent': pd.Series(dtype='object'),
        'Relation': pd.Series(dtype='object'),
        'Title': pd.Series(dtype='object'),
        'Subtitle': pd.Series(dtype='object'),
        'Recorded Date': pd.Series(dtype='object'),
        'Note': pd.Series(dtype='object'),
        # object dtype: 通常は整数、完全新規図面の行では "n/a" 文字列も入るため
        'Deleted Entities': pd.Series(dtype='object'),
        'Added Entities': pd.Series(dtype='object'),
        'Diff Entities': pd.Series(dtype='object'),
        'Unchanged Entities': pd.Series(dtype='object'),
        'Total Entities': pd.Series(dtype='object'),
    })


def save_master_to_bytes(master_df, pairs=None, mode=None, total_drawings_count=None):
    """
    図面管理台帳DataFrameをExcelバイトデータに変換

    シート構成:
      1. Summary  : 統計サマリー（エンティティ合計・図形変更率・図面統計・流用率）
      2. Diff List: 図面管理台帳データ

    Args:
        master_df: 図面管理台帳DataFrame
        pairs: ペア情報リスト（差分抽出ペア数の計算に使用。Noneの場合は 0 で埋める）
        mode: ペアリング方式（'all_in_one'(Type A) / 'auto'(Type B) / 'pair_list'(Type C)）。
              Type A は「アップロード図面総数」、Type B/C は「流用先図面総数」を分母に使う。
        total_drawings_count: 図面統計の分母件数（呼び出し側で mode に応じて算出する）

    Returns:
        bytes: Excelファイルのバイトデータ
    """
    if mode == 'all_in_one':
        total_drawings_label = 'アップロード図面総数'
        total_entities_label = 'アップロード図面 図形総数'
    else:
        total_drawings_label = '流用先図面総数'
        total_entities_label = '流用先図面 図形総数'
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book

        # --- Summary シート（先に追加してタブ順を先頭にする） ---
        summary_ws = workbook.add_worksheet('Summary')

        bold = workbook.add_format({'bold': True, 'font_size': 11})
        label_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#D9E1F2', 'border': 1, 'align': 'left'
        })
        value_fmt = workbook.add_format({'border': 1, 'align': 'right', 'num_format': '#,##0'})
        pct_fmt = workbook.add_format({'border': 1, 'align': 'right', 'num_format': '0.00%'})

        summary_ws.set_column(0, 0, 22)
        summary_ws.set_column(1, 1, 14)

        row = 0

        # ── エンティティ統計 ──
        summary_ws.write(row, 0, 'エンティティ統計', bold)
        row += 1

        entity_specs = [
            ('Deleted Entities',   '削除図形 総数'),
            ('Added Entities',     '追加図形 総数'),
            ('Diff Entities',      '変更（追加+削除）図形 総数'),
            ('Unchanged Entities', '変更なし図形 総数'),
            ('Total Entities',     total_entities_label),
        ]
        entity_sums = {}
        for col, _ in entity_specs:
            if col in master_df.columns:
                # 完全新規図面の行は "n/a" 文字列が入るため、数値以外は除外して合計する
                numeric_col = pd.to_numeric(master_df[col], errors='coerce')
                entity_sums[col] = int(numeric_col.sum(skipna=True)) if not numeric_col.isna().all() else 0
            else:
                entity_sums[col] = 0

        for col, label in entity_specs:
            summary_ws.write(row, 0, label, label_fmt)
            summary_ws.write(row, 1, entity_sums[col], value_fmt)
            row += 1

        total_ent = entity_sums.get('Total Entities', 0)
        diff_ent = entity_sums.get('Diff Entities', 0)
        change_rate = (diff_ent / total_ent) if total_ent > 0 else 0.0

        summary_ws.write(row, 0, '図形変更率 [%]', label_fmt)
        summary_ws.write(row, 1, change_rate, pct_fmt)
        row += 2  # 空行を挟む

        # ── 図面統計 ──
        summary_ws.write(row, 0, '図面統計', bold)
        row += 1

        total_drawings = total_drawings_count if total_drawings_count is not None else 0
        pair_count = len([p for p in pairs if p['status'] == 'complete']) if pairs is not None else 0
        reuse_rate = (pair_count / total_drawings) if total_drawings > 0 else 0.0

        summary_ws.write(row, 0, total_drawings_label, label_fmt)
        summary_ws.write(row, 1, total_drawings, value_fmt)
        row += 1

        summary_ws.write(row, 0, '差分抽出ペア数', label_fmt)
        summary_ws.write(row, 1, pair_count, value_fmt)
        row += 1

        summary_ws.write(row, 0, '流用率 [%]', label_fmt)
        summary_ws.write(row, 1, reuse_rate, pct_fmt)

        # --- Diff List シート ---
        master_df.to_excel(writer, sheet_name='Diff List', index=False)

    output.seek(0)
    return output.getvalue()
