"""
図面管理台帳（Parent-Child マスター）の読み込み・更新・Excel出力（UI 非依存のモデル層）。

streamlit には依存しないため、`tests/` から直接ユニットテストできる
（`model/pairing.py` と同じ方針）。
"""
import re
from io import BytesIO
from datetime import datetime

import pandas as pd

DRAWING_LIST_SHEET_NAME = "Package List"  # 旧名 "Drawing List"（2026-09 改名）
# 旧名で出力された既存台帳を再アップロードしても読めるよう、後方互換の探索候補として保持する。
LEGACY_DRAWING_LIST_SHEET_NAMES = ("Drawing List",)
MASTER_SHEET_NAME = "Master"  # 図面管理台帳データのシート名（旧名 "Diff List"、2026-08 改名）

# Master シートの「* Entities」列（object dtype: 整数と "n/a" 文字列が混在する）。
# update_parent_child_master() のdtype統一・save_master_to_bytes() の列フォーマット
# 適用の両方で使う共通定義。
ENTITY_COUNT_COLUMNS = ['Deleted Entities', 'Added Entities', 'Diff Entities',
                        'Unchanged Entities', 'Total Entities']

# render_step0_master() が作成する台帳ファイル名（"{指番}_{モジュール}_{サイド}.xlsx"）
# から指番/モジュール/サイドを逆算するためのパターン。既存台帳をアップロードした場合も
# 同じ命名規則に従っていれば逆算できる（モジュール/サイド未入力時は "na" になる）。
# 末尾に "_" または "-" 区切りで任意の文字列が続いてもよい（例:
# "AA11-1111-1_ZM00_405_all.xlsx"、2026-08、既存台帳の再アップロード時に
# 元のダウンロードファイル名に接尾辞が付くケースに対応するため追加）。区切り文字
# なしで直接くっつく形式（"AA11-1111-1_ZM00_4050.xlsx" 等）は非対応のままにする
# （区切りを必須にしないと、サイド直後の文字がサイドの一部か付加文字列かを
# 区別できず誤解析のリスクがあるため）。
MASTER_FILENAME_PATTERN = re.compile(
    r'^(?P<shiban>[A-Z]{2}\d{2}-\d{4}-\d)_(?P<module>[A-Z0-9]{4}|na)_(?P<side>[A-Z0-9]{3}|na)(?:[_-].*)?\.xlsx$'
)


def parse_master_filename(filename):
    """台帳ファイル名から指番/モジュール/サイドを逆算する。

    命名規則（"{指番}_{モジュール}_{サイド}.xlsx"、末尾に "_"/"-" 区切りで任意の
    文字列が続くものも含む）に一致しない場合（自由な名前でアップロードされた
    台帳等）は (None, None, None) を返す。

    Returns:
        tuple: (shiban, module, side) または (None, None, None)
    """
    match = MASTER_FILENAME_PATTERN.match(filename or '')
    if not match:
        return None, None, None
    return match['shiban'], match['module'], match['side']


def load_parent_child_master(uploaded_file):
    """
    図面管理台帳ファイルを読み込む

    save_master_to_bytes() が出力する台帳Excelは Summary シートを先頭に持つ
    （タブ順を先頭にするため）。単純に先頭シートを読むと Summary が選ばれ、
    本来のデータ（Child/Parent 列を持つシート）を見つけられず「必須カラムが
    見つかりません」と誤って失敗する（2026-07 実データで確認）。そのため、
    複数シートがある場合は Child/Parent 列を両方持つシートを優先的に探して読む。
    シート名（'Diff List' → 'Master'、2026-08改名等）はこれまでの改修で変わって
    きた実績があるため、固定シート名に依存せず列の有無で判定する。該当シートが無ければ、後方互換の
    ため先頭シートを読み（単一シートの古い台帳・手動作成ファイル等）、従来どおり
    カラム欠落エラーとする。

    Args:
        uploaded_file: アップロードされたExcelファイル（ファイルパスやファイルオブジェクト）

    Returns:
        tuple: (DataFrame または None, エラーメッセージ または None)
    """
    required_columns = ['Child', 'Parent']
    try:
        excel_file = pd.ExcelFile(uploaded_file)

        target_sheet = excel_file.sheet_names[0]
        for sheet_name in excel_file.sheet_names:
            header_df = pd.read_excel(excel_file, sheet_name=sheet_name, nrows=0)
            if all(col in header_df.columns for col in required_columns):
                target_sheet = sheet_name
                break

        df = pd.read_excel(excel_file, sheet_name=target_sheet)

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

    entity_count_columns = ENTITY_COUNT_COLUMNS

    # アップロードされた既存台帳で、まだ完全新規図面（"n/a"）の行が一度も無い場合、
    # pandas はエントリ数カラムを float64 として読み込む。この状態のカラムへ後段で
    # "n/a" 文字列を代入すると FutureWarning（将来的には TypeError）になるため、
    # 更新前に object dtype へ統一しておく（2026-07 追加。値は変えない）。
    for col in entity_count_columns:
        if col in updated_df.columns and updated_df[col].dtype != object:
            updated_df[col] = updated_df[col].astype(object)

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


def create_empty_drawing_list_df():
    """空の Package List DataFrame を作成"""
    return pd.DataFrame({
        'Sashiban': pd.Series(dtype='object'),
        'Module': pd.Series(dtype='object'),
        'Side': pd.Series(dtype='object'),
        'Child Drawing Number': pd.Series(dtype='object'),
        'Parent Drawing Number': pd.Series(dtype='object'),
        'Title': pd.Series(dtype='object'),
        'Subtitle': pd.Series(dtype='object'),
        'Recorded Date': pd.Series(dtype='object'),
    })


def load_drawing_list(uploaded_file):
    """
    台帳ファイルから 'Package List' シートを読み込む。

    シート名は "Drawing List" → "Package List" にリネームされた（2026-09）。
    現行名で見つからない場合は旧名（LEGACY_DRAWING_LIST_SHEET_NAMES）でも探し、
    既存台帳の再アップロードを引き続き受け付ける。どちらも無い場合（旧形式の台帳、
    本機能導入前に作成された台帳等）はエラーではなく空の DataFrame を返す。

    Args:
        uploaded_file: アップロードされたExcelファイル（ファイルパスやファイルオブジェクト）

    Returns:
        tuple: (DataFrame, エラーメッセージ または None)
    """
    try:
        excel_file = pd.ExcelFile(uploaded_file)
        target_sheet = None
        for candidate in (DRAWING_LIST_SHEET_NAME, *LEGACY_DRAWING_LIST_SHEET_NAMES):
            if candidate in excel_file.sheet_names:
                target_sheet = candidate
                break

        if target_sheet is not None:
            # Sashiban/Module/Side や図番が数字だけ（例: サイド "405"）の場合、
            # dtype指定なしで読むと pandas が列全体を int64 と誤推測し、
            # セル自体は文字列として保存されているにもかかわらず読み込み後に
            # 数値化されてしまう（2026-08 実データ確認）。明示的に str 指定する。
            df = pd.read_excel(excel_file, sheet_name=target_sheet, dtype={
                'Sashiban': str, 'Module': str, 'Side': str,
                'Child Drawing Number': str, 'Parent Drawing Number': str,
            })
            return df, None
        return create_empty_drawing_list_df(), None
    except Exception as e:
        return create_empty_drawing_list_df(), (
            f"Package List シートの読み込み中にエラーが発生しました: {str(e)}"
        )


def update_drawing_list(drawing_list_df, new_entries, shiban, module, side):
    """
    Package List に新規の Child Drawing Number のみを追加する。

    Master（update_parent_child_master）と異なり、既存行は一切上書きしない
    ——「新規の Child Drawing Number があれば追加する」という仕様のため、
    同じ Child が再度処理されても既存レコードはそのまま保持する。

    Args:
        drawing_list_df: 既存の Package List DataFrame（None可）
        new_entries: [{'main_drawing', 'source_drawing', 'title', 'subtitle'}, ...]
                     （update_parent_child_master の new_pairs と同じキー）
        shiban/module/side: 台帳ファイル名から得た指番/モジュール/サイド（Noneなら空欄記録）

    Returns:
        tuple: (更新後のDataFrame, 追加件数)
    """
    updated = drawing_list_df.copy() if drawing_list_df is not None else create_empty_drawing_list_df()
    if 'Child Drawing Number' not in updated.columns:
        updated['Child Drawing Number'] = pd.Series(dtype='object')

    existing_children = set(str(v) for v in updated['Child Drawing Number'].dropna())

    new_records = []
    for entry in new_entries:
        child = entry.get('main_drawing')
        if not child or child in existing_children:
            continue
        existing_children.add(child)  # 同一バッチ内の重複防止（先勝ち）
        parent = entry.get('source_drawing') or 'none'
        new_records.append({
            'Sashiban': shiban or '',
            'Module': module or '',
            'Side': side or '',
            'Child Drawing Number': child,
            'Parent Drawing Number': parent,
            'Title': entry.get('title'),
            'Subtitle': entry.get('subtitle'),
            'Recorded Date': datetime.now(),
        })

    added_count = len(new_records)
    # pd.concat は既存側が空（0行）の場合に FutureWarning
    # （"empty or all-NA entries" の dtype 除外に関する警告）を出すため、
    # update_parent_child_master() と同じ「1行ずつ .loc で追加」方式にする。
    for record in new_records:
        updated.loc[len(updated)] = record

    return updated, added_count


def save_master_to_bytes(master_df, mode=None, drawing_list_df=None):
    """
    図面管理台帳DataFrameをExcelバイトデータに変換

    シート構成:
      1. Summary     : 統計サマリー（エンティティ合計・図形変更率）
      2. Master      : 図面管理台帳データ（旧名 "Diff List"、2026-08改名）
      3. Package List: 差分処理対象となった入力ファイルの記録（Child Drawing Numberでユニーク。
                       旧名 "Drawing List"、2026-09改名）

    Args:
        master_df: 図面管理台帳DataFrame
        mode: ペアリング方式（'all_in_one'(Type A) / 'auto'(Type B) / 'pair_list'(Type C)）。
              Type A は「アップロード図面 図形総数」、Type B/C は「流用先図面 図形総数」の
              ラベルに使う（エンティティ統計の Total Entities 行のみ）。
        drawing_list_df: Package List DataFrame（Noneの場合は空シートを出力）

    Returns:
        bytes: Excelファイルのバイトデータ
    """
    if mode == 'all_in_one':
        total_entities_label = 'アップロード図面 図形総数'
    else:
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

        # --- Master シート（Child で昇順ソート） ---
        master_sheet_df = master_df
        if 'Child' in master_df.columns:
            master_sheet_df = master_df.sort_values('Child', kind='stable', na_position='last')
        master_sheet_df.to_excel(writer, sheet_name=MASTER_SHEET_NAME, index=False)
        writer.sheets[MASTER_SHEET_NAME].freeze_panes(1, 0)  # タイトル行を固定

        # 「* Entities」列は整数と "n/a" 文字列が混在する object dtype のため、
        # to_excel() の既定書式のままだと数値セルは右揃え・"n/a" セルは左揃えになり
        # 見た目が揃わない（2026-08 ユーザー指摘）。列フォーマット（xlsxwriterの
        # set_column）は to_excel() が既に書き込んだセルにも後から一括適用されるため、
        # 中央揃え＋桁区切りに統一する。
        entity_col_fmt = workbook.add_format({'align': 'center', 'num_format': '#,##0'})
        master_sheet_ws = writer.sheets[MASTER_SHEET_NAME]
        for col_idx, col_name in enumerate(master_sheet_df.columns):
            if col_name in ENTITY_COUNT_COLUMNS:
                master_sheet_ws.set_column(col_idx, col_idx, None, entity_col_fmt)

        # --- Package List シート（Master の後ろ。Child Drawing Number で昇順ソート） ---
        dl_df = drawing_list_df if drawing_list_df is not None else create_empty_drawing_list_df()
        if 'Child Drawing Number' in dl_df.columns:
            dl_df = dl_df.sort_values('Child Drawing Number', kind='stable', na_position='last')
        dl_df.to_excel(writer, sheet_name=DRAWING_LIST_SHEET_NAME, index=False)
        writer.sheets[DRAWING_LIST_SHEET_NAME].freeze_panes(1, 0)  # タイトル行を固定

    output.seek(0)
    return output.getvalue()
