import streamlit as st
import os
import tempfile
import sys
from pathlib import Path
import zipfile
from io import BytesIO
from collections import defaultdict, Counter
import pandas as pd
from datetime import datetime
import gc
import hashlib
import time

# utils モジュールをインポート可能にするためのパスの追加
current_dir = os.path.dirname(os.path.abspath(__file__))
utils_path = os.path.join(current_dir, 'utils')
sys.path.insert(0, utils_path)

from utils.extract_labels import extract_labels
from utils.compare_dxf import compare_dxf_files_and_generate_dxf
from utils.common_utils import save_uploadedfile, handle_error
from utils.label_diff import (
    compute_label_differences,
    filter_unchanged_by_prefix,
    build_diff_labels_workbook,
    build_unchanged_labels_workbook
)
from utils.pairing import build_pairs, build_pairs_from_list

# 設定をインポート
from config import ui_config, diff_config, extraction_config, help_text

st.set_page_config(
    page_title="DXF Diff Manager",
    page_icon="📊",
    layout="wide",
)

PREFIX_CONFIG_PATH = Path(current_dir) / "prefix_config.txt"
DIFF_LABELS_FILENAME = "diff_labels.xlsx"
UNCHANGED_LABELS_FILENAME = "unchanged_labels.xlsx"


def load_default_prefixes():
    if PREFIX_CONFIG_PATH.exists():
        with open(PREFIX_CONFIG_PATH, 'r', encoding='utf-8') as f:
            lines = [line.rstrip('\n') for line in f]
        return [line for line in lines if line.strip()]
    return []


DEFAULT_PREFIXES = load_default_prefixes()


def cleanup_temp_files():
    """
    セッション状態に保存された一時ファイルをクリーンアップする
    """
    for dict_key in ('source_files_dict', 'dest_files_dict',
                     'all_files_dict', 'all_in_one_files_dict'):
        if dict_key in st.session_state:
            for drawing_number, file_info in st.session_state[dict_key].items():
                temp_path = file_info.get('temp_path')
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass  # エラーは無視


def get_prefix_list_from_state():
    text_value = st.session_state.get('prefix_text_input', "")
    return [line.strip() for line in text_value.splitlines() if line.strip()]


def load_parent_child_master(uploaded_file):
    """
    親子関係台帳ファイルを読み込む

    Args:
        uploaded_file: アップロードされたExcelファイル

    Returns:
        DataFrame: 親子関係台帳のデータフレーム
    """
    try:
        df = pd.read_excel(uploaded_file)

        # 必要なカラムが存在するか確認
        required_columns = ['Child', 'Parent']
        for col in required_columns:
            if col not in df.columns:
                st.error(f"必須カラム '{col}' が見つかりません。")
                return None

        return df

    except Exception as e:
        st.error(f"親子関係台帳ファイルの読み込み中にエラーが発生しました: {str(e)}")
        return None


def update_parent_child_master(master_df, new_pairs):
    """
    親子関係台帳に新しいペアを追加、もしくは既存ペアを更新する

    Args:
        master_df: 既存の親子関係台帳DataFrame
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
        relation = pair.get('relation')       # 'RevUp' または '流用'
        entity_counts = pair.get('entity_counts')  # エンティティ数情報

        if not parent or not child:
            continue

        # 既存のレコードに同じ親子関係が存在するか確認
        mask = (updated_df['Parent'] == parent) & (updated_df['Child'] == child)
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
            for col in entity_count_columns:
                if col not in updated_df.columns:
                    updated_df[col] = pd.Series(dtype='Int64')  # 整数型（NULLを許容）

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

            # エンティティ数を更新（存在する場合）
            if entity_counts:
                updated_df.loc[mask, 'Deleted Entities'] = entity_counts.get('deleted_entities')
                updated_df.loc[mask, 'Added Entities'] = entity_counts.get('added_entities')
                updated_df.loc[mask, 'Diff Entities'] = entity_counts.get('diff_entities')
                updated_df.loc[mask, 'Unchanged Entities'] = entity_counts.get('unchanged_entities')
                updated_df.loc[mask, 'Total Entities'] = entity_counts.get('total_entities')
        else:
            # 新しいレコードを追加
            new_record = {
                'Child': child,
                'Parent': parent,
                'Relation': relation,
                'Title': title,
                'Subtitle': subtitle,
                'Recorded Date': datetime.now()
            }

            # エンティティ数を追加（存在する場合）
            if entity_counts:
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
                    if key in entity_count_columns:
                        updated_df[key] = pd.Series(dtype='Int64')
                    else:
                        updated_df[key] = pd.Series(dtype='object')
            updated_df.loc[len(updated_df)] = record

    return updated_df, added_count


def create_empty_master_df():
    """空の親子関係台帳DataFrameを作成（図面親子管理台帳.xlsx のフォーマットに準拠）"""
    return pd.DataFrame({
        'Child': pd.Series(dtype='object'),
        'Parent': pd.Series(dtype='object'),
        'Relation': pd.Series(dtype='object'),
        'Title': pd.Series(dtype='object'),
        'Subtitle': pd.Series(dtype='object'),
        'Recorded Date': pd.Series(dtype='object'),
        'Note': pd.Series(dtype='object'),
        'Deleted Entities': pd.Series(dtype='Int64'),
        'Added Entities': pd.Series(dtype='Int64'),
        'Diff Entities': pd.Series(dtype='Int64'),
        'Unchanged Entities': pd.Series(dtype='Int64'),
        'Total Entities': pd.Series(dtype='Int64'),
    })


def save_master_to_bytes(master_df, pairs=None):
    """
    親子関係台帳DataFrameをExcelバイトデータに変換

    シート構成:
      1. Summary  : 統計サマリー（エンティティ合計・図形変更率・図面統計・流用率）
      2. Diff List: 親子関係台帳データ

    Args:
        master_df: 親子関係台帳DataFrame
        pairs: ペア情報リスト（図面統計の計算に使用。Noneの場合は 0 で埋める）

    Returns:
        bytes: Excelファイルのバイトデータ
    """
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
            ('Deleted Entities',   '削除図形数 合計'),
            ('Added Entities',     '追加図形数 合計'),
            ('Diff Entities',      '差分図形数 合計'),
            ('Unchanged Entities', '変更なし図形数 合計'),
            ('Total Entities',     '総図形数 合計'),
        ]
        entity_sums = {}
        for col, _ in entity_specs:
            if col in master_df.columns and not master_df[col].isna().all():
                entity_sums[col] = int(master_df[col].sum(skipna=True))
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

        if pairs is not None:
            total_drawings = len(set(p['main_drawing'] for p in pairs))
            pair_count = len([p for p in pairs if p['status'] == 'complete'])
        else:
            total_drawings = 0
            pair_count = 0
        reuse_rate = (pair_count / total_drawings) if total_drawings > 0 else 0.0

        summary_ws.write(row, 0, '入力図面総数', label_fmt)
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


def extract_source_number_from_dest_file(uploaded_file):
    """
    流用先DXFファイルを処理する。
    図番（main_drawing_number）はファイル名から取得し、
    DXFからは流用元図番（source_drawing_number）のみを抽出する。

    Args:
        uploaded_file: アップロードファイル・オブジェクト

    Returns:
        dict or None
    """
    try:
        drawing_number = Path(uploaded_file.name).stem
        file_hash = hashlib.sha256(uploaded_file.getbuffer()).hexdigest()
        temp_path = save_uploadedfile(uploaded_file)

        cache = st.session_state.get('drawing_info_cache', {})
        cached_info = cache.get(file_hash)

        if cached_info:
            source_drawing = cached_info.get('source_drawing_number')
        else:
            _, info = extract_labels(
                temp_path,
                filter_non_parts=False,
                sort_order="none",
                debug=False,
                selected_layers=None,
                validate_ref_designators=False,
                extract_drawing_numbers_option=True,
                extract_title_option=False,
                original_filename=uploaded_file.name
            )
            source_drawing = info.get('source_drawing_number')
            cache[file_hash] = {'source_drawing_number': source_drawing}
            st.session_state.drawing_info_cache = cache

        return {
            'filename': uploaded_file.name,
            'temp_path': temp_path,
            'main_drawing_number': drawing_number,
            'source_drawing_number': source_drawing,
            'title': None,
            'subtitle': None,
        }

    except Exception as e:
        st.error(f"ファイル {uploaded_file.name} の処理中にエラーが発生しました: {str(e)}")
        return None


def extract_drawing_info_from_file(uploaded_file):
    """
    アップロードされたDXFファイルから図面番号情報を抽出する

    Args:
        uploaded_file: アップロードファイル・オブジェクト

    Returns:
        dict or None
    """
    try:
        file_hash = hashlib.sha256(uploaded_file.getbuffer()).hexdigest()
        temp_path = save_uploadedfile(uploaded_file)

        cache = st.session_state.get('drawing_info_cache', {})
        cached_info = cache.get(file_hash)

        if cached_info:
            info = dict(cached_info)
        else:
            _, info = extract_labels(
                temp_path,
                filter_non_parts=False,
                sort_order="none",
                debug=False,
                selected_layers=None,
                validate_ref_designators=False,
                extract_drawing_numbers_option=True,
                extract_title_option=True,
                original_filename=uploaded_file.name
            )

        main_drawing = info.get('main_drawing_number')
        if not main_drawing:
            main_drawing = Path(uploaded_file.name).stem

        result = {
            'filename': uploaded_file.name,
            'temp_path': temp_path,
            'main_drawing_number': main_drawing,
            'source_drawing_number': info.get('source_drawing_number'),
            'title': info.get('title'),
            'subtitle': info.get('subtitle'),
            'file_hash': file_hash
        }

        if not cached_info:
            cache[file_hash] = {
                key: value for key, value in result.items()
                if key not in ('filename', 'temp_path')
            }
            st.session_state.drawing_info_cache = cache

        return result

    except Exception as e:
        st.error(f"ファイル {uploaded_file.name} の図番抽出中にエラーが発生しました: {str(e)}")
        return None


def create_pair_list(source_files_dict, dest_files_dict, progress_callback=None):
    """auto モード用ペアリング（薄いシム）。

    実体は流用判定と RevUp 判定を独立実行する `utils.pairing.build_pairs`。
    比較元は流用元グループ、比較先は流用先グループに限定される。
    """
    return build_pairs(source_files_dict, dest_files_dict, progress_callback=progress_callback)


def create_diff_zip(pairs, master_df=None, master_filename=None, tolerance=None, deleted_color=None, added_color=None,
                    unchanged_color=None, prefixes=None, progress_callback=None,
                    filter_non_parts=False, validate_ref_designators=False):
    """
    ペアリストに基づいて差分DXFファイルを作成し、ZIPアーカイブを生成

    Args:
        pairs: ペア情報のリスト
        master_df: 親子関係台帳DataFrame（Noneでない場合はZIPに含める）
        master_filename: 親子関係台帳のファイル名（Noneの場合はデフォルト名を使用）
        tolerance: 座標許容誤差（Noneの場合はconfigのデフォルト値を使用）
        deleted_color: 削除エンティティの色（Noneの場合はconfigのデフォルト値を使用）
        added_color: 追加エンティティの色（Noneの場合はconfigのデフォルト値を使用）
        unchanged_color: 変更なしエンティティの色（Noneの場合はconfigのデフォルト値を使用）

    Returns:
        tuple: (zip_data, results)
    """
    # デフォルト値をconfigから取得
    if tolerance is None:
        tolerance = diff_config.DEFAULT_TOLERANCE
    if deleted_color is None:
        deleted_color = diff_config.DEFAULT_DELETED_COLOR
    if added_color is None:
        added_color = diff_config.DEFAULT_ADDED_COLOR
    if unchanged_color is None:
        unchanged_color = diff_config.DEFAULT_UNCHANGED_COLOR

    results = []
    prefixes = prefixes or []
    diff_label_sheets = []
    unchanged_label_sheets = []
    summary_data = []
    total_counter = Counter()
    invalid_dict = defaultdict(lambda: {'count': 0, 'files': set()})
    pair_extracted_info = {}  # main_drawing → {title, subtitle} (DXF から抽出)
    label_cache = {}
    zip_buffer = BytesIO()
    complete_pairs = [p for p in pairs if p['status'] == 'complete']
    total_pairs = len(complete_pairs)

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:

        for index, pair in enumerate(complete_pairs, start=1):
            main_drawing = pair['main_drawing']
            source_drawing = pair['source_drawing']
            main_file_path = pair['main_file_info']['temp_path']
            source_file_path = pair['source_file_info']['temp_path']

            # 出力ファイル名を生成
            output_filename = f"{main_drawing}_vs_{source_drawing}.dxf"

            # 一時出力ファイルを作成
            temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf").name

            change_rows = []
            filtered_unchanged = []
            change_label_count = 0
            unchanged_label_count = 0

            extra_info = {'labels_new': [], 'invalid_ref_designators': []}
            try:
                change_rows, unchanged_entries, extra_info = compute_label_differences(
                    main_file_path,
                    source_file_path,
                    tolerance=tolerance,
                    label_cache=label_cache,
                    filter_non_parts=filter_non_parts,
                    validate_ref_designators=validate_ref_designators,
                )
                filtered_unchanged = filter_unchanged_by_prefix(unchanged_entries, prefixes)
                change_label_count = len(change_rows)
                unchanged_label_count = sum(row.get('Count', 0) for row in filtered_unchanged)
            except Exception as e:
                st.error(f"ラベル比較中にエラーが発生しました ({main_drawing}): {str(e)}")
                change_rows = []
                filtered_unchanged = []

            # Summary 行を収集
            added_count = sum(1 for r in change_rows if r['Old Label'] is None)
            deleted_count = sum(1 for r in change_rows if r['New Label'] is None)
            changed_count = sum(1 for r in change_rows if r['Old Label'] is not None and r['New Label'] is not None)
            resolved_title = extra_info.get('title') or pair.get('title')
            resolved_subtitle = extra_info.get('subtitle') or pair.get('subtitle')
            pair_extracted_info[main_drawing] = {'title': resolved_title, 'subtitle': resolved_subtitle}
            summary_data.append({
                '図番': main_drawing,
                '流用元図番': source_drawing,
                '追加ラベル数': added_count,
                '削除ラベル数': deleted_count,
                '変更ラベル数': changed_count,
                'タイトル': resolved_title,
                'サブタイトル': resolved_subtitle,
            })

            # Total 用ラベル集計
            if filter_non_parts:
                for label, _x, _y in extra_info['labels_new']:
                    total_counter[label] += 1

            # Invalid 集計
            if validate_ref_designators:
                for sym in extra_info['invalid_ref_designators']:
                    invalid_dict[sym]['count'] += 1
                    invalid_dict[sym]['files'].add(main_drawing)

            diff_label_sheets.append({
                'sheet_name': main_drawing,
                'rows': change_rows,
                'old_label_name': f"Old: {source_drawing}",
                'new_label_name': f"New: {main_drawing}"
            })
            unchanged_label_sheets.append({'sheet_name': main_drawing, 'rows': filtered_unchanged})

            try:
                if progress_callback:
                    progress_callback(index - 1, total_pairs, f"{main_drawing} vs {source_drawing} 処理中")

                # DXF比較処理（図番（新）を基準A、流用元図番（旧）を比較対象B）
                success, entity_counts = compare_dxf_files_and_generate_dxf(
                    main_file_path,        # 基準ファイルA (新)
                    source_file_path,      # 比較対象ファイルB (旧)
                    temp_output,
                    tolerance=tolerance,
                    deleted_color=deleted_color,
                    added_color=added_color,
                    unchanged_color=unchanged_color,
                    offset_b=None
                )

                if success:
                    zip_file.write(temp_output, arcname=output_filename)
                    results.append({
                        'pair_name': f"{main_drawing} vs {source_drawing}",
                        'main_drawing': main_drawing,
                        'source_drawing': source_drawing,
                        'output_filename': output_filename,
                        'success': True,
                        'entity_counts': entity_counts,
                        'relation': pair.get('relation', 'なし'),
                        'change_label_count': change_label_count,
                        'unchanged_label_count': unchanged_label_count
                    })
                else:
                    results.append({
                        'pair_name': f"{main_drawing} vs {source_drawing}",
                        'main_drawing': main_drawing,
                        'source_drawing': source_drawing,
                        'output_filename': output_filename,
                        'success': False,
                        'entity_counts': None,
                        'relation': pair.get('relation', 'なし'),
                        'change_label_count': change_label_count,
                        'unchanged_label_count': unchanged_label_count
                    })

            except Exception as e:
                st.error(f"ペア {main_drawing} vs {source_drawing} の図面作成中にエラーが発生しました: {str(e)}")
                results.append({
                    'pair_name': f"{main_drawing} vs {source_drawing}",
                    'main_drawing': main_drawing,
                    'source_drawing': source_drawing,
                    'output_filename': output_filename,
                    'success': False,
                    'error': str(e),
                    'relation': pair.get('relation', 'なし'),
                    'entity_counts': None,
                    'change_label_count': change_label_count,
                    'unchanged_label_count': unchanged_label_count
                })
            finally:
                try:
                    os.unlink(temp_output)
                except:
                    pass

            if progress_callback:
                progress_callback(index, total_pairs, f"{main_drawing} vs {source_drawing} 処理完了")

        # 親子関係台帳を結果で更新（エンティティ数を含む）
        if master_df is not None:
            pairs_with_entity_counts = []
            for result in results:
                if result['success']:
                    original_pair = next((p for p in complete_pairs
                                         if p['main_drawing'] == result['main_drawing']
                                         and p['source_drawing'] == result['source_drawing']), None)

                    if original_pair:
                        pair_with_counts = original_pair.copy()
                        pair_with_counts['entity_counts'] = result['entity_counts']
                        extracted = pair_extracted_info.get(result['main_drawing'], {})
                        if extracted.get('title'):
                            pair_with_counts['title'] = extracted['title']
                        if extracted.get('subtitle'):
                            pair_with_counts['subtitle'] = extracted['subtitle']
                        pairs_with_entity_counts.append(pair_with_counts)

            if pairs_with_entity_counts:
                master_df, _ = update_parent_child_master(master_df, pairs_with_entity_counts)

        # Total データ生成
        total_data = None
        if filter_non_parts and total_counter:
            total_data = [{'ラベル': lbl, '個数': cnt} for lbl, cnt in sorted(total_counter.items())]

        # Invalid データ生成
        invalid_data = None
        if validate_ref_designators and invalid_dict:
            invalid_data = [
                {'機器符号': sym, '個数': v['count'], 'ファイル名': ', '.join(sorted(v['files']))}
                for sym, v in sorted(invalid_dict.items())
            ]

        diff_labels_excel = build_diff_labels_workbook(
            diff_label_sheets,
            summary_data=summary_data if summary_data else None,
            total_data=total_data,
            invalid_data=invalid_data,
        )
        unchanged_labels_excel = build_unchanged_labels_workbook(unchanged_label_sheets)

        if diff_labels_excel:
            zip_file.writestr(DIFF_LABELS_FILENAME, diff_labels_excel)
        if unchanged_labels_excel:
            zip_file.writestr(UNCHANGED_LABELS_FILENAME, unchanged_labels_excel)

        if master_df is not None:
            master_excel_data = save_master_to_bytes(master_df, pairs=pairs)
            output_master_filename = master_filename if master_filename else diff_config.MASTER_FILENAME
            zip_file.writestr(output_master_filename, master_excel_data)

    zip_buffer.seek(0)
    zip_data = zip_buffer.getvalue()

    # メモリ解放: 大きなデータ構造を削除
    del diff_label_sheets
    del unchanged_label_sheets
    gc.collect()

    return zip_data, results, diff_labels_excel, unchanged_labels_excel, master_df


def load_pair_list(uploaded_file):
    """
    ペアリストファイルを読み込む（ExcelまたはCSV）

    必須カラム: 比較元図番, 比較先図番（または Reference, Target）

    Returns:
        DataFrame or None（カラム名は 比較元図番/比較先図番 に統一）
    """
    try:
        if uploaded_file.name.lower().endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)

        # カラム名の正規化（英語名→日本語名にマッピング）
        column_aliases = {
            'Reference': '比較元図番',
            'Target': '比較先図番',
        }
        df = df.rename(columns=column_aliases)

        required_columns = ['比較元図番', '比較先図番']
        missing = [c for c in required_columns if c not in df.columns]
        if missing:
            st.error(
                f"必須カラムが見つかりません: {missing}\n"
                f"実際のカラム: {list(df.columns)}\n"
                f"「比較元図番」「比較先図番」または「Reference」「Target」のカラム名が必要です。"
            )
            return None

        df = df[required_columns].copy()
        # 文字列化し、空セル(NaN→'nan')や空白は空文字に正規化
        for col in required_columns:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].where(df[col].str.lower() != 'nan', '')
        # 両方が空白の行のみ除外（片側だけ空白の行は「片側のみペア」として残す）
        df = df[(df['比較元図番'] != '') | (df['比較先図番'] != '')]
        return df.reset_index(drop=True)

    except Exception as e:
        st.error(f"ペアリストの読み込み中にエラーが発生しました: {str(e)}")
        return None


def _extract_by_filename(uploaded_file):
    """ファイル名（拡張子なし）を図番として使用するシンプルな抽出関数"""
    drawing_number = Path(uploaded_file.name).stem
    temp_path = save_uploadedfile(uploaded_file)
    return {
        'filename': uploaded_file.name,
        'temp_path': temp_path,
        'main_drawing_number': drawing_number,
    }


def process_dxf_files_by_filename(uploaded_files, files_dict, upload_key_name, failures_key, summary_key):
    """
    ファイル名を図番として使用してDXFファイルを処理する（DXF解析なし）

    Returns:
        bool: いずれかのファイルが処理されたかどうか
    """
    return process_all_uploaded_files([{
        'uploaded_files': uploaded_files,
        'files_dict': files_dict,
        'upload_key_name': upload_key_name,
        'failures_key': failures_key,
        'summary_key': summary_key,
        'extractor': _extract_by_filename,
    }])


def create_pairs_from_pair_list(pair_list_df, all_files_dict):
    """pair_list モード用ペアリング（薄いシム）。

    実体は明示ペアをそのまま解決する `utils.pairing.build_pairs_from_list`。
    RevUp の自動補完は行わない。
    """
    return build_pairs_from_list(pair_list_df, all_files_dict)


def initialize_session_state():
    """セッション状態を初期化"""
    if 'step0_mode' not in st.session_state:
        st.session_state.step0_mode = 'new'

    if 'new_master_filename_input' not in st.session_state:
        st.session_state.new_master_filename_input = '図面親子管理台帳'

    if 'source_files_dict' not in st.session_state:
        st.session_state.source_files_dict = {}

    if 'dest_files_dict' not in st.session_state:
        st.session_state.dest_files_dict = {}

    if 'pairs' not in st.session_state:
        st.session_state.pairs = []

    if 'pairs_dirty' not in st.session_state:
        st.session_state.pairs_dirty = False

    if 'master_df' not in st.session_state:
        st.session_state.master_df = None

    if 'master_file_name' not in st.session_state:
        st.session_state.master_file_name = None

    if 'added_relationships_count' not in st.session_state:
        st.session_state.added_relationships_count = 0

    if 'uploader_key' not in st.session_state:
        st.session_state.uploader_key = 0

    if 'source_upload_key' not in st.session_state:
        st.session_state.source_upload_key = 0

    if 'dest_upload_key' not in st.session_state:
        st.session_state.dest_upload_key = 0

    if 'source_upload_failures' not in st.session_state:
        st.session_state.source_upload_failures = []

    if 'dest_upload_failures' not in st.session_state:
        st.session_state.dest_upload_failures = []

    if 'source_upload_summary' not in st.session_state:
        st.session_state.source_upload_summary = None

    if 'dest_upload_summary' not in st.session_state:
        st.session_state.dest_upload_summary = None

    if 'prefix_text_input' not in st.session_state:
        st.session_state.prefix_text_input = "\n".join(DEFAULT_PREFIXES)

    if 'drawing_info_cache' not in st.session_state:
        st.session_state.drawing_info_cache = {}

    # ペアリストモード用
    if 'step1_mode' not in st.session_state:
        st.session_state.step1_mode = 'all_in_one'

    if 'pair_list_df' not in st.session_state:
        st.session_state.pair_list_df = None

    if 'pair_list_file_name' not in st.session_state:
        st.session_state.pair_list_file_name = None

    if 'all_files_dict' not in st.session_state:
        st.session_state.all_files_dict = {}

    if 'all_upload_key' not in st.session_state:
        st.session_state.all_upload_key = 0

    if 'all_upload_failures' not in st.session_state:
        st.session_state.all_upload_failures = []

    if 'all_upload_summary' not in st.session_state:
        st.session_state.all_upload_summary = None

    # 一括アップロードモード用
    if 'all_in_one_files_dict' not in st.session_state:
        st.session_state.all_in_one_files_dict = {}

    if 'all_in_one_upload_key' not in st.session_state:
        st.session_state.all_in_one_upload_key = 0

    if 'all_in_one_upload_failures' not in st.session_state:
        st.session_state.all_in_one_upload_failures = []

    if 'all_in_one_upload_summary' not in st.session_state:
        st.session_state.all_in_one_upload_summary = None


def create_pairs_from_single_pool(files_dict):
    """all_in_one モード用ペアリング（薄いシム）。

    実体は単一プールに対し流用判定と RevUp 判定を独立実行する
    `utils.pairing.build_pairs`（source と target に同一プールを渡す）。
    """
    return build_pairs(files_dict, files_dict)


def update_master_if_needed(pairs):
    """親子関係台帳を更新（必要な場合のみ）

    Args:
        pairs: ペア情報のリスト

    Returns:
        int: 追加された件数
    """
    if st.session_state.master_df is None:
        return 0

    complete_pairs = [p for p in pairs if p['status'] == 'complete']
    if not complete_pairs:
        return 0

    updated_master, added_count = update_parent_child_master(
        st.session_state.master_df,
        complete_pairs
    )
    st.session_state.master_df = updated_master
    return added_count


def render_pair_list():
    """ペアリストを表示

    Returns:
        list: 差分抽出可能なペアのリスト
    """
    if not st.session_state.pairs:
        return []

    st.subheader("図面ペア・リスト")

    complete_pairs = [p for p in st.session_state.pairs if p['status'] == 'complete']
    missing_pairs = [p for p in st.session_state.pairs if p['status'] == 'missing_source']
    missing_target_pairs = [p for p in st.session_state.pairs if p['status'] == 'missing_target']
    missing_both_pairs = [p for p in st.session_state.pairs if p['status'] == 'missing_both']
    one_sided_pairs = [p for p in st.session_state.pairs if p['status'] == 'one_sided']
    no_source_pairs = [p for p in st.session_state.pairs if p['status'] == 'no_source_defined']

    # 差分抽出可能なペア
    if complete_pairs:
        st.success(f"差分抽出が可能なペア: {len(complete_pairs)}組")

        pair_data = []
        for pair in complete_pairs:
            pair_data.append({
                '比較先（新）': pair['main_drawing'],
                '比較元（旧）': pair['source_drawing'],
                '関係': pair.get('relation', 'なし'),
            })

        st.dataframe(pair_data, width='stretch', hide_index=True)

    # 比較元のDXFファイルが未アップロードのペア
    # 同じ比較先に RevUp の差分抽出可能ペアがある場合は、その比較元図番を併記する
    if missing_pairs:
        revup_source_by_target = {
            p['main_drawing']: p['source_drawing']
            for p in complete_pairs
            if p.get('relation') == 'RevUp'
        }
        missing_data = []
        for pair in missing_pairs:
            revup_source = revup_source_by_target.get(pair['main_drawing'])
            if revup_source:
                status = f'⚠️ 比較元のDXFなし・RevUpあり（{revup_source}）'
            else:
                status = '⚠️ 比較元のDXFなし'
            missing_data.append({
                '比較先（新）': pair['main_drawing'],
                '比較元（旧）': pair['source_drawing'],
                '関係': pair.get('relation', 'なし'),
                'ステータス': status
            })

        with st.expander(f"⚠️ 比較元のDXFファイルが未アップロード（{len(missing_pairs)}件）", expanded=True):
            st.dataframe(missing_data, width='stretch', hide_index=True)

    # 比較先のDXFファイルが未アップロードのペア（ペアリストモード用）
    if missing_target_pairs:
        missing_target_data = []
        for pair in missing_target_pairs:
            missing_target_data.append({
                '比較先（新）': pair['main_drawing'],
                '比較元（旧）': pair['source_drawing'],
                'ステータス': '⚠️ 比較先のDXFなし'
            })

        with st.expander(f"⚠️ 比較先のDXFファイルが未アップロード（{len(missing_target_pairs)}件）", expanded=True):
            st.dataframe(missing_target_data, width='stretch', hide_index=True)

    # 両方未アップロードのペア（ペアリストモード用）
    if missing_both_pairs:
        missing_both_data = []
        for pair in missing_both_pairs:
            missing_both_data.append({
                '比較先（新）': pair['main_drawing'],
                '比較元（旧）': pair['source_drawing'],
                'ステータス': '⚠️ 比較元・比較先ともにDXFなし'
            })

        with st.expander(f"⚠️ 比較元・比較先ともに未アップロード（{len(missing_both_pairs)}件）", expanded=True):
            st.dataframe(missing_both_data, width='stretch', hide_index=True)

    # 片側のみのペア（ペアリストで比較元または比較先を空白にしたケース）
    if one_sided_pairs:
        one_sided_data = []
        for pair in one_sided_pairs:
            one_sided_data.append({
                '比較先（新）': pair['main_drawing'] or '（なし）',
                '比較元（旧）': pair['source_drawing'] or '（なし）',
            })

        with st.expander(f"➖ 片側のみのペア（{len(one_sided_pairs)}件）", expanded=True):
            st.caption("ペアリストで比較元または比較先が空白の行です。相手図番がないため差分抽出は行いません。")
            st.dataframe(one_sided_data, width='stretch', hide_index=True)

    # 流用元図番が指定されていないペア（自動ペアリングモード用）
    if no_source_pairs:
        no_source_data = []
        for pair in no_source_pairs:
            no_source_data.append({
                '図番': pair['main_drawing'],
                '関係': pair.get('relation') or 'なし',
                'ステータス': '⚠️ 流用元図番の未記入'
            })

        with st.expander("流用元図番の記載がない図面（比較対象外）", expanded=False):
            st.dataframe(no_source_data, width='stretch', hide_index=True)

    # 親子関係台帳更新状況の表示
    if st.session_state.master_df is not None and st.session_state.added_relationships_count > 0:
        st.success(f"親子関係台帳に {st.session_state.added_relationships_count} 件の新しい関係を追加しました")

    return complete_pairs

def render_preview_dataframe(df, key_prefix):
    """プレビュー用データフレームの列幅を調整して表示"""
    column_config = {
        col: st.column_config.Column(col, width="small")
        if col in ("Coordinate X", "Coordinate Y", "Count")
        else st.column_config.Column(col)
        for col in df.columns
    }
    st.dataframe(
        df,
        width='stretch',
        hide_index=True,
        column_config=column_config,
        key=key_prefix
    )


def render_help_section():
    """プログラム説明セクションを表示"""
    with st.expander("ℹ️ プログラム説明", expanded=False):
        st.info("\n".join(help_text.USAGE_STEPS))


def process_all_uploaded_files(groups):
    """
    複数グループのアップロードDXFファイルを単一の進捗バーで処理する

    Args:
        groups: 処理グループのリスト。各要素は dict:
            - uploaded_files: アップロードされたファイルのリスト
            - files_dict: 格納先の辞書
            - upload_key_name: アップロードキーのsession_state名
            - failures_key: 失敗ファイルリストのsession_state名
            - summary_key: サマリーのsession_state名
            - extractor: (省略可) ファイル情報抽出関数。省略時は extract_drawing_info_from_file

    Returns:
        bool: いずれかのファイルが処理されたかどうか
    """
    # 全グループの合計ファイル数を算出
    all_items = []
    for g in groups:
        if g['uploaded_files']:
            for f in g['uploaded_files']:
                all_items.append((f, g))

    if not all_items:
        return False

    total_files = len(all_items)
    start_time = time.time()
    progress_placeholder = st.empty()
    progress_bar = progress_placeholder.progress(0.0, text="ファイルを処理中...")

    # グループごとの集計用
    group_results = {id(g): {'processed': 0, 'failed': []} for _, g in all_items}

    for idx, (uploaded_file, group) in enumerate(all_items, start=1):
        extractor = group.get('extractor', extract_drawing_info_from_file)
        file_info = extractor(uploaded_file)
        gid = id(group)
        if file_info:
            main_drawing = file_info['main_drawing_number']
            group['files_dict'][main_drawing] = file_info
            group_results[gid]['processed'] += 1
        else:
            group_results[gid]['failed'].append(uploaded_file.name)

        elapsed = time.time() - start_time
        progress_bar.progress(
            min(idx / total_files, 1.0),
            text=f"{idx}/{total_files}件の図番を抽出中...（経過 {elapsed:.1f} 秒）"
        )

    progress_placeholder.empty()
    elapsed_total = time.time() - start_time

    # グループごとにsession_stateを更新
    processed_any = False
    for g in groups:
        if not g['uploaded_files']:
            continue
        gid = id(g)
        res = group_results[gid]
        if res['processed'] > 0:
            st.session_state.pairs_dirty = True
            processed_any = True
        st.session_state[g['upload_key_name']] += 1
        st.session_state[g['failures_key']] = res['failed']
        st.session_state[g['summary_key']] = {
            'processed': res['processed'],
            'failed': len(res['failed']),
            'elapsed': elapsed_total
        }

    return processed_any


def render_upload_status(summary_key, failures_key, label):
    """
    アップロード結果のサマリーと失敗ファイルを表示する共通ロジック

    Args:
        summary_key: サマリーのsession_state名
        failures_key: 失敗ファイルリストのsession_state名
        label: 表示ラベル（「流用元」「流用先」など）
    """
    upload_summary = st.session_state.get(summary_key)
    if upload_summary:
        processed = upload_summary.get('processed', 0)
        failed = upload_summary.get('failed', 0)
        elapsed = upload_summary.get('elapsed', 0.0)
        if processed > 0:
            st.success(f"直近の{label}ファイル読み込み: {processed}件（経過 {elapsed:.1f} 秒, 失敗 {failed}件）")
        elif failed > 0:
            st.warning(f"直近の{label}ファイル読み込みは失敗しました（経過 {elapsed:.1f} 秒）")

    if st.session_state.get(failures_key):
        with st.expander(f"アップロードできなかった{label}ファイル", expanded=False):
            for name in st.session_state[failures_key]:
                st.write(f"- {name}")


def render_step0_master():
    """Step 0: 親子関係台帳の設定"""
    st.subheader("Step 0: 親子関係台帳の設定")

    prev_step0_mode = st.session_state.step0_mode

    step0_mode = st.radio(
        "台帳の利用方法",
        options=['new', 'upload'],
        format_func=lambda x: {
            'new': '新規作成',
            'upload': '既存ファイルをアップロードする',
        }[x],
        key='step0_mode',
        horizontal=True,
        label_visibility='collapsed',
    )

    if prev_step0_mode != step0_mode:
        st.session_state.master_df = None
        st.session_state.master_file_name = None
        st.session_state.added_relationships_count = 0

    if step0_mode == 'new':
        col1, col2 = st.columns([4, 1])
        with col1:
            filename_base = st.text_input(
                "台帳ファイル名（.xlsx は自動付与されます）",
                key='new_master_filename_input',
            )
        filename_base = (filename_base or '').strip() or '図面親子管理台帳'
        with col2:
            st.write("")
            st.caption(f"→ **{filename_base}.xlsx**")

        master_filename = f"{filename_base}.xlsx"

        if st.session_state.master_df is None:
            st.session_state.master_df = create_empty_master_df()
            st.session_state.added_relationships_count = 0
        st.session_state.master_file_name = master_filename

        st.info(f"新規台帳「{master_filename}」を作成します。差分抽出後、台帳が自動更新されてダウンロードZIPに含まれます。")

    else:
        master_file = st.file_uploader(
            "親子関係台帳Excelファイルをアップロードしてください",
            type=ui_config.MASTER_FILE_TYPES,
            key=f"master_upload_{st.session_state.uploader_key}",
            help="親子関係を一元管理するExcelファイルです。新しく見つかった親子関係が自動的に追加されます。"
        )

        if master_file is not None:
            if st.session_state.master_df is None or st.session_state.get('master_file_name') != master_file.name:
                master_df = load_parent_child_master(master_file)
                if master_df is not None:
                    st.session_state.master_df = master_df
                    st.session_state.master_file_name = master_file.name
                    st.session_state.added_relationships_count = 0
                    st.success(f"記録済み親子関係（{len(master_df)}件のレコード）")
            else:
                st.info(f"既存の親子関係に追加します（{len(st.session_state.master_df)}件のレコード）")
        else:
            if st.session_state.master_df is not None:
                st.session_state.master_df = None
                st.session_state.master_file_name = None
                st.session_state.added_relationships_count = 0


def render_step1_upload():
    """Step 1: DXFファイルのアップロードと図番抽出

    Returns:
        tuple: (source_count, dest_count)
          auto モード:        実際の流用元件数と流用先件数
          pair_list モード:   DXFファイル件数と 0
          all_in_one モード:  DXFファイル件数と 0
    """
    mode = st.session_state.step1_mode
    if mode == 'auto':
        return _render_step1_auto_mode()
    elif mode == 'pair_list':
        return _render_step1_pair_list_mode()
    else:
        return _render_step1_all_in_one_mode()


def _render_step1_auto_mode():
    """自動ペアリングモードのStep 1"""
    # Step 1-1: 流用元DXFファイルのアップロード
    st.subheader("Step 1-1: 流用元（旧）DXFファイルのアップロード")
    st.caption("ファイル名（拡張子なし）が図番として使用されます。")

    source_uploaded_files = st.file_uploader(
        "流用元（旧）DXFファイルをアップロードしてください（複数可・フォルダ可・複数回可）",
        type=ui_config.DXF_FILE_TYPES,
        accept_multiple_files=True,
        key=f"source_upload_{st.session_state.source_upload_key}",
        help="比較元となる旧図面をアップロードしてください"
    )

    render_upload_status('source_upload_summary', 'source_upload_failures', '流用元')

    source_count = len(st.session_state.source_files_dict)
    if source_count > 0:
        st.info(f"流用元（旧）図面: {source_count}件 読み込み済み")

    # Step 1-2: 流用先DXFファイルのアップロード
    st.subheader("Step 1-2: 流用先（新）DXFファイルのアップロード")

    dest_uploaded_files = st.file_uploader(
        "流用先（新）DXFファイルをアップロードしてください（複数可・フォルダ可・複数回可）",
        type=ui_config.DXF_FILE_TYPES,
        accept_multiple_files=True,
        key=f"dest_upload_{st.session_state.dest_upload_key}",
        help="新しく作成した図面をアップロードしてください"
    )

    render_upload_status('dest_upload_summary', 'dest_upload_failures', '流用先')

    dest_count = len(st.session_state.dest_files_dict)
    if dest_count > 0:
        st.info(f"流用先（新）図面: {dest_count}件 抽出済み")

    # 読み込みボタン（両グループ共通）
    has_new_files = bool(source_uploaded_files) or bool(dest_uploaded_files)
    process_button = st.button("ファイルを読み込む", key="process_files", type="primary", disabled=not has_new_files)

    if process_button:
        any_processed = False

        if source_uploaded_files:
            # 流用元はファイル名を図番として使用（DXF解析なし）
            if process_dxf_files_by_filename(
                source_uploaded_files,
                st.session_state.source_files_dict,
                'source_upload_key',
                'source_upload_failures',
                'source_upload_summary',
            ):
                any_processed = True

        if dest_uploaded_files:
            # 流用先はDXFから流用元図番のみ抽出（図番はファイル名を使用）
            groups = [{
                'uploaded_files': dest_uploaded_files,
                'files_dict': st.session_state.dest_files_dict,
                'upload_key_name': 'dest_upload_key',
                'failures_key': 'dest_upload_failures',
                'summary_key': 'dest_upload_summary',
                'extractor': extract_source_number_from_dest_file,
            }]
            if process_all_uploaded_files(groups):
                any_processed = True

        if any_processed:
            gc.collect()
            st.rerun()

    return source_count, dest_count


def _render_step1_pair_list_mode():
    """ペアリストモードのStep 1

    Returns:
        tuple: (all_count, 0)
    """
    # Step 1-1: ペアリストのアップロード
    st.subheader("Step 1-1: ペアリストのアップロード")
    st.caption(
        "比較元図番（旧）と比較先図番（新）のペアを記載したExcelまたはCSVファイルをアップロードしてください。\n"
        "必須カラム：**比較元図番** と **比較先図番**（または **Reference** と **Target**）"
    )

    pair_list_file = st.file_uploader(
        "ペアリスト（Excel/CSV）",
        type=['xlsx', 'xls', 'csv'],
        key=f"pair_list_upload_{st.session_state.uploader_key}",
    )

    if pair_list_file is not None:
        if (st.session_state.pair_list_df is None
                or st.session_state.pair_list_file_name != pair_list_file.name):
            pair_list_df = load_pair_list(pair_list_file)
            if pair_list_df is not None:
                st.session_state.pair_list_df = pair_list_df
                st.session_state.pair_list_file_name = pair_list_file.name
                st.session_state.pairs_dirty = True
    else:
        if st.session_state.pair_list_df is not None:
            st.session_state.pair_list_df = None
            st.session_state.pair_list_file_name = None
            st.session_state.pairs_dirty = True

    if st.session_state.pair_list_df is not None:
        df = st.session_state.pair_list_df
        st.success(f"ペアリスト読み込み済み: {len(df)}組のペア")
        with st.expander("ペアリストプレビュー", expanded=False):
            st.dataframe(df, hide_index=True, width='stretch')

    # Step 1-2: DXFファイルのアップロード
    st.subheader("Step 1-2: DXFファイルのアップロード（比較元・比較先まとめて）")
    st.caption("ファイル名（拡張子なし）が図番として使用されます。比較元と比較先のファイルをまとめてアップロードしてください。")

    all_uploaded_files = st.file_uploader(
        "DXFファイル（複数可）",
        type=ui_config.DXF_FILE_TYPES,
        accept_multiple_files=True,
        key=f"all_upload_{st.session_state.all_upload_key}",
    )

    render_upload_status('all_upload_summary', 'all_upload_failures', 'DXF')

    all_count = len(st.session_state.all_files_dict)
    if all_count > 0:
        st.info(f"読み込み済みDXFファイル: {all_count}件")

    has_new_files = bool(all_uploaded_files)
    if st.button("ファイルを読み込む", key="process_all_files", type="primary", disabled=not has_new_files):
        if process_dxf_files_by_filename(
            all_uploaded_files,
            st.session_state.all_files_dict,
            'all_upload_key',
            'all_upload_failures',
            'all_upload_summary',
        ):
            gc.collect()
            st.rerun()

    # ペアリストと照合して未アップロード図番を即時表示
    if st.session_state.pair_list_df is not None and all_count > 0:
        _show_missing_drawings(st.session_state.pair_list_df, st.session_state.all_files_dict)

    return all_count, 0


def _show_missing_drawings(pair_list_df, all_files_dict):
    """ペアリストにあるがアップロードされていない図番を表示"""
    def _norm(value):
        # 空セル(NaN=float)対策で文字列化し、前後空白を除去
        s = str(value).strip()
        return '' if s.lower() == 'nan' else s

    ref_drawings = set()
    target_drawings = set()
    for _, row in pair_list_df.iterrows():
        ref = _norm(row['比較元図番'])
        target = _norm(row['比較先図番'])
        # 比較元と比較先が同一図番の行は比較対象外のため未アップロード判定から除外
        if ref and target and ref == target:
            continue
        if ref:
            ref_drawings.add(ref)
        if target:
            target_drawings.add(target)

    uploaded = {str(k).strip() for k in all_files_dict.keys()}

    missing_ref = sorted(ref_drawings - uploaded)
    missing_target = sorted(target_drawings - uploaded)

    if not missing_ref and not missing_target:
        st.success("ペアリストの全図番がアップロード済みです。")
        return

    if missing_ref:
        with st.expander(f"⚠️ 未アップロードの比較元図番（{len(missing_ref)}件）", expanded=True):
            st.dataframe(
                pd.DataFrame({'比較元図番（未アップロード）': missing_ref}),
                hide_index=True, width='stretch'
            )

    if missing_target:
        with st.expander(f"⚠️ 未アップロードの比較先図番（{len(missing_target)}件）", expanded=True):
            st.dataframe(
                pd.DataFrame({'比較先図番（未アップロード）': missing_target}),
                hide_index=True, width='stretch'
            )


def _render_step1_all_in_one_mode():
    """一括アップロードモードのStep 1

    全DXFファイルをまとめてアップロードし、各ファイルのDXFから
    流用元図番を抽出してペアを自動作成する。

    Returns:
        tuple: (all_in_one_count, 0)
    """
    st.subheader("Step 1: DXFファイルの一括アップロード")
    st.caption(
        "流用元・流用先を区別せず全DXFファイルをアップロードしてください。\n"
        "ファイル名（拡張子なし）が図番として使用され、DXFから抽出した流用元図番でペアを自動作成します。"
    )

    all_in_one_uploaded_files = st.file_uploader(
        "DXFファイル（複数可）",
        type=ui_config.DXF_FILE_TYPES,
        accept_multiple_files=True,
        key=f"all_in_one_upload_{st.session_state.all_in_one_upload_key}",
    )

    render_upload_status('all_in_one_upload_summary', 'all_in_one_upload_failures', 'DXF')

    all_in_one_count = len(st.session_state.all_in_one_files_dict)
    if all_in_one_count > 0:
        st.info(f"読み込み済みDXFファイル: {all_in_one_count}件")

    has_new_files = bool(all_in_one_uploaded_files)
    if st.button("ファイルを読み込む", key="process_all_in_one_files", type="primary", disabled=not has_new_files):
        groups = [{
            'uploaded_files': all_in_one_uploaded_files,
            'files_dict': st.session_state.all_in_one_files_dict,
            'upload_key_name': 'all_in_one_upload_key',
            'failures_key': 'all_in_one_upload_failures',
            'summary_key': 'all_in_one_upload_summary',
            'extractor': extract_source_number_from_dest_file,
        }]
        if process_all_uploaded_files(groups):
            gc.collect()
            st.rerun()

    return all_in_one_count, 0


def render_step2_pairing(source_count, dest_count):
    """Step 2: 図面ペア・リスト作成

    Args:
        source_count: 流用元件数（auto）またはDXFファイル件数（その他モード）
        dest_count:   流用先件数（auto）または 0（その他モード）

    Returns:
        tuple: (complete_pairs, pairs_ready)
    """
    mode = st.session_state.step1_mode
    st.subheader("Step 2: 図面ペア・リスト確認")

    if mode == 'pair_list':
        pair_list_ready = st.session_state.pair_list_df is not None
        has_files = source_count > 0
        ready_to_pair = pair_list_ready and has_files
        if not ready_to_pair:
            st.info("Step 1-1でペアリストをアップロードしてください。" if not pair_list_ready
                    else "Step 1-2でDXFファイルをアップロードしてください。")
        else:
            st.write(f"ペアリスト: {len(st.session_state.pair_list_df)}組、DXFファイル: {source_count}件")
    elif mode == 'all_in_one':
        ready_to_pair = source_count > 0
        if not ready_to_pair:
            st.info("Step 1でDXFファイルをアップロードしてください。")
        else:
            st.write(f"DXFファイル: {source_count}件")
    else:  # auto
        ready_to_pair = source_count > 0 and dest_count > 0
        if not ready_to_pair:
            if source_count == 0 and dest_count == 0:
                st.info("流用元（旧）と流用先（新）のDXFファイルをそれぞれアップロードしてください。")
            elif source_count == 0:
                st.info("流用元（旧）DXFファイルをアップロードしてください。")
            else:
                st.info("流用先（新）DXFファイルをアップロードしてください。")
        else:
            st.write(f"流用元 {source_count}件、流用先 {dest_count}件（合計 {source_count + dest_count}件）")

    pairs_available = bool(st.session_state.pairs)
    pairs_ready = pairs_available and not st.session_state.get('pairs_dirty', False)

    pair_button = st.button(
        "図面ペア・リスト作成",
        key="generate_pairs",
        type="primary",
        disabled=not ready_to_pair or pairs_ready
    )

    if pair_button:
        if mode == 'pair_list':
            st.session_state.pairs = create_pairs_from_pair_list(
                st.session_state.pair_list_df,
                st.session_state.all_files_dict,
            )
        elif mode == 'all_in_one':
            st.session_state.pairs = create_pairs_from_single_pool(
                st.session_state.all_in_one_files_dict,
            )
        else:  # auto
            pairing_start = time.time()
            progress_placeholder = st.empty()
            progress_bar = progress_placeholder.progress(0.0, text="図面ペア・リスト作成を開始...")

            def pairing_progress(progress, message, count, total):
                elapsed = time.time() - pairing_start
                text = message
                if total and count is not None:
                    text += f" {count}/{total}件"
                text += f"（経過 {elapsed:.1f} 秒）"
                progress_bar.progress(min(max(progress, 0.0), 1.0), text=text)

            try:
                st.session_state.pairs = create_pair_list(
                    st.session_state.source_files_dict,
                    st.session_state.dest_files_dict,
                    progress_callback=pairing_progress
                )
            finally:
                progress_placeholder.empty()

        st.session_state.pairs_dirty = False
        added_count = update_master_if_needed(st.session_state.pairs)
        st.session_state.added_relationships_count += added_count
        gc.collect()
        st.rerun()

    # pair_button ハンドラで pairs が更新された場合に再計算
    pairs_available = bool(st.session_state.pairs)
    pairs_ready = pairs_available and not st.session_state.get('pairs_dirty', False)

    complete_pairs = []
    if pairs_available:
        if pairs_ready:
            complete_pairs = render_pair_list()
        else:
            st.warning("新しいファイルが追加されています。「図面ペア・リスト作成」を実行して最新のペアを生成してください。")
    elif ready_to_pair:
        st.info("「図面ペア・リスト作成」を押してください。")

    return complete_pairs, pairs_ready


def render_step3_diff(complete_pairs):
    """Step 3: 差分比較（ペアが準備完了時）

    Args:
        complete_pairs: 差分抽出可能なペアのリスト
    """
    # オプション設定
    with st.expander("オプション設定", expanded=False):
        col1, col2 = st.columns(2)

        with col1:
            validate_ref_designators = st.checkbox(
                "**機器符号妥当性チェック**",
                value=False,
                help="機器符号パターンに一致するラベルのみを抽出し（Total シート追加）、標準フォーマット非適合の機器符号を Invalid シートに出力します。"
            )
            filter_non_parts = validate_ref_designators

            st.write("")
            tolerance = st.number_input(
                "**差分検出の際の座標マージン**",
                min_value=1e-8,
                max_value=1.0,
                value=diff_config.DEFAULT_TOLERANCE,
                step=0.01,
                format="%.2f",
                help="同じ図形と判定する座標の許容誤差です。大きくするほど位置ずれを無視します。",
            )

            prefix_text = st.text_area(
                "**未変更ラベルの中から抽出したい先頭文字列**（1行1件）",
                value=st.session_state.prefix_text_input,
                height=150,
                help="prefix_config.txt に定義された初期値を基に編集できます。空行は無視されます。",
                key=f"prefix_text_area_{st.session_state.uploader_key}"
            )
            st.session_state.prefix_text_input = prefix_text
            prefix_list = get_prefix_list_from_state()

        with col2:
            st.write("**レイヤー色設定**")

            # デフォルト値のインデックスを取得
            deleted_default_index = next(i for i, (val, _) in enumerate(diff_config.COLOR_OPTIONS) if val == diff_config.DEFAULT_DELETED_COLOR)
            added_default_index = next(i for i, (val, _) in enumerate(diff_config.COLOR_OPTIONS) if val == diff_config.DEFAULT_ADDED_COLOR)
            unchanged_default_index = next(i for i, (val, _) in enumerate(diff_config.COLOR_OPTIONS) if val == diff_config.DEFAULT_UNCHANGED_COLOR)

            deleted_color = st.selectbox(
                "削除図形の色（比較元図面のみ）",
                options=diff_config.COLOR_OPTIONS,
                index=deleted_default_index,
                format_func=lambda x: x[1]
            )[0]

            added_color = st.selectbox(
                "追加図形の色（新図面のみ）",
                options=diff_config.COLOR_OPTIONS,
                index=added_default_index,
                format_func=lambda x: x[1]
            )[0]

            unchanged_color = st.selectbox(
                "変更なし図形の色",
                options=diff_config.COLOR_OPTIONS,
                index=unchanged_default_index,
                format_func=lambda x: x[1]
            )[0]

    # 比較開始ボタン
    if complete_pairs:
        st.info(f"差分抽出可能なペア: {len(complete_pairs)}組")

        has_results = bool(st.session_state.get('results'))
        if st.button("差分抽出開始", key="start_comparison", type="primary", disabled=has_results):
            total_pairs = len(complete_pairs)
            progress_placeholder = st.empty()
            progress_bar = progress_placeholder.progress(0.0, text="差分抽出を開始しています...")

            def diff_progress(current, total, message):
                progress = current / total if total else 1.0
                progress_bar.progress(min(progress, 1.0), text=f"{message}（{current}/{total}組）")

            try:
                zip_data, results, diff_labels_excel, unchanged_labels_excel, updated_master = create_diff_zip(
                    st.session_state.pairs,
                    master_df=st.session_state.master_df,
                    master_filename=st.session_state.master_file_name,
                    tolerance=tolerance,
                    deleted_color=deleted_color,
                    added_color=added_color,
                    unchanged_color=unchanged_color,
                    prefixes=prefix_list,
                    progress_callback=diff_progress,
                    filter_non_parts=filter_non_parts,
                    validate_ref_designators=validate_ref_designators,
                )

                # セッション状態に保存
                st.session_state.zip_data = zip_data
                st.session_state.results = results
                st.session_state.diff_labels_excel_data = diff_labels_excel
                st.session_state.unchanged_labels_excel_data = unchanged_labels_excel
                st.session_state.processing_settings = {
                    'tolerance': tolerance,
                    'deleted_color': deleted_color,
                    'added_color': added_color,
                    'unchanged_color': unchanged_color,
                    'validate_ref_designators': validate_ref_designators,
                }
                if updated_master is not None:
                    st.session_state.master_df = updated_master

                # メモリ解放
                gc.collect()

            except Exception as e:
                handle_error(e)
                gc.collect()
            finally:
                progress_placeholder.empty()

            st.rerun()
    else:
        st.warning("比較対象となる旧図面がありません。旧図面をアップロードしてください。")

    # 結果の表示
    if 'results' in st.session_state and st.session_state.results:
        st.subheader("差分抽出結果")

        results = st.session_state.results
        settings = st.session_state.get('processing_settings', {})

        # 成功/失敗のサマリー
        successful_count = sum(1 for r in results if r['success'])
        total_count = len(results)

        if successful_count == total_count:
            st.success(f"全{total_count}組のペアの差分抽出が完了しました")
        elif successful_count > 0:
            st.warning(f"{successful_count}/{total_count}組のペアの差分抽出が完了しましたが、一部のペアで処理に失敗しました。")
        else:
            st.error("全てのペアで処理に失敗しました ❌")

        # 結果詳細
        result_data = []
        for result in results:
            status = "✅ 成功" if result['success'] else "❌ 失敗"
            entity_counts = result.get('entity_counts')

            row = {
                '流用先（新）': result['main_drawing'],
                '流用元（旧）': result['source_drawing'],
                '出力ファイル名': result['output_filename'],
                '関係': result.get('relation', 'なし')
            }

            # エンティティ数を追加（成功した場合のみ）
            if entity_counts:
                row['削除図形数'] = entity_counts.get('deleted_entities', '-')
                row['追加図形数'] = entity_counts.get('added_entities', '-')
                row['総図形数'] = entity_counts.get('total_entities', '-')
            else:
                row['削除図形数'] = '-'
                row['追加図形数'] = '-'
                row['総図形数'] = '-'
            row['変更ラベル数'] = result.get('change_label_count', '-')
            row['未変更抽出ラベル数'] = result.get('unchanged_label_count', '-')

            row['ステータス'] = status
            result_data.append(row)

        st.dataframe(result_data, width='stretch', hide_index=True)

        # プレビューセクション
        preview_available = st.session_state.get('diff_labels_excel_data') is not None or \
                            st.session_state.get('unchanged_labels_excel_data') is not None or \
                            st.session_state.master_df is not None

        if preview_available:
            st.subheader("出力内容プレビュー")

            preview_items = []
            if st.session_state.master_df is not None:
                preview_items.append("親子関係台帳")
            if st.session_state.get('diff_labels_excel_data'):
                preview_items.append("diff_labels.xlsx")
            if st.session_state.get('unchanged_labels_excel_data'):
                preview_items.append("unchanged_labels.xlsx")
            if preview_items:
                st.caption("表示可能: " + ", ".join(preview_items))

            if st.session_state.master_df is not None:
                with st.expander("親子関係台帳プレビュー", expanded=False):
                    render_preview_dataframe(st.session_state.master_df, "master_preview")

            if st.session_state.get('diff_labels_excel_data'):
                diff_expanded = st.session_state.get('diff_preview_expanded', False)
                with st.expander("diff_labels.xlsx プレビュー", expanded=diff_expanded):
                    diff_xl = pd.ExcelFile(BytesIO(st.session_state.diff_labels_excel_data))
                    sheet_name = st.selectbox(
                        "シートを選択（diff_labels）",
                        diff_xl.sheet_names,
                        key="diff_labels_preview_sheet"
                    )
                    render_preview_dataframe(diff_xl.parse(sheet_name), "diff_preview")
                    st.session_state['diff_preview_expanded'] = True

            if st.session_state.get('unchanged_labels_excel_data'):
                with st.expander("unchanged_labels.xlsx プレビュー", expanded=False):
                    unchanged_xl = pd.ExcelFile(BytesIO(st.session_state.unchanged_labels_excel_data))
                    sheet_name = st.selectbox(
                        "シートを選択（unchanged_labels）",
                        unchanged_xl.sheet_names,
                        key="unchanged_labels_preview_sheet"
                    )
                    render_preview_dataframe(unchanged_xl.parse(sheet_name), "unchanged_preview")

        # ダウンロードボタン
        if successful_count > 0:
            st.subheader("Step 4: 差分抽出ファイルのダウンロード")

            downloaded = st.session_state.get('downloaded', False)
            st.download_button(
                label="ZIPでダウンロード",
                data=st.session_state.zip_data,
                file_name="dxf_diff_results.zip",
                mime="application/zip",
                key="download_zip",
                type="primary",
                disabled=downloaded,
                on_click=lambda: st.session_state.update({'downloaded': True})
            )

            # オプション設定の情報を表示
            st.info(f"""
                **生成されたファイルについて：**
                - ADDED: 新図面にのみ存在する要素（追加された図形）
                - DELETED: 旧図面にのみ存在する要素（削除された図形）
                - UNCHANGED: 両方の図面に存在し変更がない図形
                - diff_labels.xlsx: 各図面の変更ラベル一覧（シート名は新図面の図番）
                - unchanged_labels.xlsx: 指定の先頭文字列に一致する未変更ラベル一覧
                - 座標許容誤差: {settings.get('tolerance', 0.01)}
                """)

        # 新しい比較を開始するボタン
        if st.button("🔄 新しい差分抽出を開始", key="restart_button"):
            # 一時ファイルのクリーンアップ
            cleanup_temp_files()

            # セッション状態をクリア
            for key in ['source_files_dict', 'dest_files_dict',
                        'pairs', 'pairs_dirty',
                        'source_upload_key', 'dest_upload_key',
                        'drawing_info_cache',
                        'source_upload_failures', 'dest_upload_failures',
                        'source_upload_summary', 'dest_upload_summary',
                        'pair_list_df', 'pair_list_file_name',
                        'all_files_dict', 'all_upload_key',
                        'all_upload_failures', 'all_upload_summary',
                        'all_in_one_files_dict', 'all_in_one_upload_key',
                        'all_in_one_upload_failures', 'all_in_one_upload_summary',
                        'results', 'zip_data', 'processing_settings',
                        'master_df', 'master_file_name', 'added_relationships_count',
                        'diff_labels_excel_data', 'unchanged_labels_excel_data',
                        'downloaded']:
                if key in st.session_state:
                    del st.session_state[key]

            # ファイルアップロード入力をクリアするためにキーをインクリメント
            st.session_state.uploader_key += 1

            # ガベージコレクションを実行してメモリを解放
            gc.collect()

            st.rerun()


def render_step3_inactive(source_count, dest_count, pairs_available):
    """Step 3: 差分比較（ペアが未準備時のガイダンス表示）

    Args:
        source_count: 流用元件数（auto）またはDXFファイル件数（その他モード）
        dest_count:   流用先件数（auto）または 0（その他モード）
        pairs_available: ペアが存在するかどうか
    """
    mode = st.session_state.step1_mode

    if mode in ('pair_list', 'all_in_one'):
        if source_count == 0:
            st.info("DXFファイルをアップロードしてから「図面ペア・リスト作成」を実行してください。")
        elif not pairs_available:
            st.info("「図面ペア・リスト作成」を実行後に差分比較を開始できます。")
        else:
            st.warning("最新ファイルを反映したペアリストを作成してください。")
    else:  # auto
        if source_count == 0 and dest_count == 0:
            st.info("流用元（旧）と流用先（新）のDXFファイルをそれぞれアップロードしてください。")
        elif source_count == 0:
            st.info("流用元（旧）DXFファイルをアップロードしてください。")
        elif dest_count == 0:
            st.info("流用先（新）DXFファイルをアップロードしてください。")
        elif not pairs_available:
            st.info("「図面ペア・リスト作成」を実行後に差分比較を開始できます。")
        else:
            st.warning("最新ファイルを反映したペアリストを作成してください。")


def app():
    st.title(ui_config.TITLE)
    st.write(ui_config.SUBTITLE)

    render_help_section()
    initialize_session_state()

    # ペアリング方式の選択（プログラム説明の直後）
    prev_mode = st.session_state.step1_mode
    with st.container(border=True):
#        st.markdown("#### :gear: ペアリング方式の選択")
        st.markdown("### ペアリング方式の選択")
        st.caption("方式によってDXFファイルのアップロード方法が変わります")
        mode = st.radio(
            "ペアリング方式を選択してください",
            options=['all_in_one', 'auto', 'pair_list'],
            format_func=lambda x: {
                'all_in_one': 'A: 全ファイルをまとめてアップロードし、各DXFファイルから流用元図番を抽出してペアを自動作成',
                'auto':       'B: 流用元と流用先とを別々にアップロードし、流用先ファイルから流用元図番を抽出してペアを自動作成',
                'pair_list':  'C: 全ファイルをまとめてアップロードし、ペアリストの内容でペアを作成',
            }[x],
            horizontal=False,
            key='step1_mode',
            label_visibility="collapsed",
        )
    if prev_mode != mode:
        st.session_state.pairs = []
        st.session_state.pairs_dirty = False

    st.divider()

    render_step0_master()
    st.divider()

    source_count, dest_count = render_step1_upload()
    st.divider()

    complete_pairs, pairs_ready = render_step2_pairing(source_count, dest_count)

    st.subheader("Step 3: 差分比較")
    if pairs_ready:
        render_step3_diff(complete_pairs)
    else:
        pairs_available = bool(st.session_state.pairs)
        render_step3_inactive(source_count, dest_count, pairs_available)


if __name__ == "__main__":
    app()
