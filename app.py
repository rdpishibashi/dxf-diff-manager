import streamlit as st
import os
import tempfile
import sys
from pathlib import Path
import zipfile
from io import BytesIO
from collections import defaultdict
import pandas as pd
from datetime import datetime

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
            # 既存レコードを更新（Relation, Title, Subtitle, Recorded Date, エンティティ数を上書き）
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
            entity_count_columns = ['Deleted Entities', 'Added Entities', 'Diff Entities',
                                   'Unchanged Entities', 'Total Entities']
            for col in entity_count_columns:
                if col not in updated_df.columns:
                    updated_df[col] = pd.Series(dtype='Int64')  # 整数型（NULLを許容）

            updated_df.loc[mask, 'Relation'] = relation
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

            # 他のカラムが存在する場合は空値を設定
            for col in updated_df.columns:
                if col not in new_record:
                    new_record[col] = None

            new_records.append(new_record)
            added_count += 1

    if new_records:
        # 新しいレコードを追加
        new_df = pd.DataFrame(new_records)
        updated_df = pd.concat([updated_df, new_df], ignore_index=True)

    return updated_df, added_count


def save_master_to_bytes(master_df, filename=None):
    """
    親子関係台帳DataFrameをExcelバイトデータに変換

    Args:
        master_df: 親子関係台帳DataFrame
        filename: 出力ファイル名（使用しないが、インターフェースの一貫性のために保持）

    Returns:
        bytes: Excelファイルのバイトデータ
    """
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        master_df.to_excel(writer, sheet_name='Sheet1', index=False)
    output.seek(0)
    return output.getvalue()


def extract_drawing_info_from_file(uploaded_file):
    """
    アップロードされたDXFファイルから図面番号情報を抽出する

    Args:
        uploaded_file: アップロードファイル・オブジェクト

    Returns:
        dict: {
            'filename': str,
            'temp_path': str,
            'main_drawing_number': str or None,
            'source_drawing_number': str or None
        }
    """
    try:
        # 一時ファイルに保存
        temp_path = save_uploadedfile(uploaded_file)

        # 図面番号、タイトル、サブタイトルを抽出
        _, info = extract_labels(
            temp_path,
            filter_non_parts=False,
            sort_order="none",
            debug=False,
            selected_layers=None,
            validate_ref_designators=False,
            extract_drawing_numbers_option=True,
            extract_title_option=True
        )

        # 図番が見つからない場合はファイル名を使用
        main_drawing = info.get('main_drawing_number')
        if not main_drawing:
            main_drawing = Path(uploaded_file.name).stem

        return {
            'filename': uploaded_file.name,
            'temp_path': temp_path,
            'main_drawing_number': main_drawing,
            'source_drawing_number': info.get('source_drawing_number'),
            'title': info.get('title'),
            'subtitle': info.get('subtitle')
        }

    except Exception as e:
        st.error(f"ファイル {uploaded_file.name} の図番抽出中にエラーが発生しました: {str(e)}")
        return None


def extract_base_drawing_number(drawing_number):
    """
    図番から最後の1英文字（Revision識別子）を除いたベース図番を抽出

    Args:
        drawing_number: 図番文字列

    Returns:
        tuple: (ベース図番, Revision識別子) または (None, None)
    """
    if not drawing_number or len(drawing_number) < 2:
        return None, None

    # 最後の1文字を確認
    last_char = drawing_number[-1]

    # 英大文字（半角または全角）の場合のみRevision識別子として扱う
    if last_char.isalpha() and last_char.isupper():
        base = drawing_number[:-1]
        revision = last_char
        return base, revision

    # 全角英大文字の場合
    if '\uff21' <= last_char <= '\uff3a':  # 全角A-Z
        base = drawing_number[:-1]
        revision = last_char
        return base, revision

    return None, None


def create_revup_pairs(uploaded_files_dict):
    """
    RevUpペア（Revision識別子のみ異なる同一図面のペア）を作成

    Args:
        uploaded_files_dict: 図番をキーとしたファイル情報の辞書

    Returns:
        tuple: (RevUpペアのリスト, 使用された図番のセット)
    """
    # ベース図番ごとにグループ化
    base_groups = defaultdict(list)

    for drawing_number in uploaded_files_dict.keys():
        base, revision = extract_base_drawing_number(drawing_number)
        if base and revision:
            base_groups[base].append((drawing_number, revision))

    revup_pairs = []
    used_drawings = set()

    # 各グループでペアを作成
    for base, drawings_with_rev in base_groups.items():
        # 2つ以上ある場合のみペアを作成
        if len(drawings_with_rev) < 2:
            continue

        # Revision識別子でソート（アルファベット順）
        sorted_drawings = sorted(drawings_with_rev, key=lambda x: x[1])

        # 2つずつペアを作成
        for i in range(0, len(sorted_drawings) - 1, 2):
            old_drawing, old_rev = sorted_drawings[i]
            new_drawing, new_rev = sorted_drawings[i + 1]

            old_file_info = uploaded_files_dict[old_drawing]
            new_file_info = uploaded_files_dict[new_drawing]

            pair = {
                'main_drawing': new_drawing,
                'source_drawing': old_drawing,
                'main_file_info': new_file_info,
                'source_file_info': old_file_info,
                'status': 'complete',
                'relation': 'RevUp',
                'title': new_file_info.get('title'),
                'subtitle': new_file_info.get('subtitle')
            }

            revup_pairs.append(pair)
            used_drawings.add(old_drawing)
            used_drawings.add(new_drawing)

    return revup_pairs, used_drawings


def create_pair_list(uploaded_files_dict):
    """
    アップロードされたファイル情報からペアリストを作成

    優先順位:
    1. RevUpペア（Revision識別子のみ異なる同一図面）
    2. 流用ペア（図番と流用元図番）

    Args:
        uploaded_files_dict: 図番をキーとしたファイル情報の辞書

    Returns:
        list: ペア情報のリスト
    """
    pairs = []

    # 1. RevUpペアを優先的に作成
    revup_pairs, used_drawings = create_revup_pairs(uploaded_files_dict)
    pairs.extend(revup_pairs)

    # 2. 残りのファイルで流用ペアを作成
    processed_mains = set(used_drawings)  # RevUpペアで使用された図番は除外

    for main_drawing, file_info in uploaded_files_dict.items():
        if main_drawing in processed_mains:
            continue

        source_drawing = file_info.get('source_drawing_number')

        # 流用元図番がある場合
        if source_drawing:
            # 流用元図面が存在するか確認
            source_file_info = uploaded_files_dict.get(source_drawing)

            pair = {
                'main_drawing': main_drawing,
                'source_drawing': source_drawing,
                'main_file_info': file_info,
                'source_file_info': source_file_info,
                'status': 'complete' if source_file_info else 'missing_source',
                'relation': '流用',
                'title': file_info.get('title'),
                'subtitle': file_info.get('subtitle')
            }
            pairs.append(pair)
            processed_mains.add(main_drawing)
        else:
            # 流用元図番がない場合もリストに追加（流用元なし）
            pair = {
                'main_drawing': main_drawing,
                'source_drawing': None,
                'main_file_info': file_info,
                'source_file_info': None,
                'title': file_info.get('title'),
                'subtitle': file_info.get('subtitle'),
                'relation': None,  # 関係なし
                'status': 'no_source_defined'
            }
            pairs.append(pair)
            processed_mains.add(main_drawing)

    return pairs


def create_diff_zip(pairs, master_df=None, master_filename=None, tolerance=None, deleted_color=None, added_color=None,
                    unchanged_color=None, prefixes=None):
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
    temp_output_files = []
    diff_label_sheets = []
    unchanged_label_sheets = []

    # 完全なペアのみ処理
    complete_pairs = [p for p in pairs if p['status'] == 'complete']

    for pair in complete_pairs:
        main_drawing = pair['main_drawing']
        source_drawing = pair['source_drawing']
        main_file_path = pair['main_file_info']['temp_path']
        source_file_path = pair['source_file_info']['temp_path']

        # 出力ファイル名を生成
        output_filename = f"{main_drawing}_vs_{source_drawing}.dxf"

        # 一時出力ファイルを作成
        temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf").name
        temp_output_files.append(temp_output)

        change_rows = []
        filtered_unchanged = []
        change_label_count = 0
        unchanged_label_count = 0

        try:
            change_rows, unchanged_entries = compute_label_differences(
                main_file_path,
                source_file_path,
                tolerance=tolerance
            )
            filtered_unchanged = filter_unchanged_by_prefix(unchanged_entries, prefixes)
            change_label_count = len(change_rows)
            unchanged_label_count = sum(row.get('Count', 0) for row in filtered_unchanged)
        except Exception as e:
            st.error(f"ラベル比較中にエラーが発生しました ({main_drawing}): {str(e)}")
            change_rows = []
            filtered_unchanged = []

        diff_label_sheets.append({
            'sheet_name': main_drawing,
            'rows': change_rows,
            'old_label_name': f"Old: {source_drawing}",
            'new_label_name': f"New: {main_drawing}"
        })
        unchanged_label_sheets.append({'sheet_name': main_drawing, 'rows': filtered_unchanged})

        try:
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
                # 結果ファイルを読み込み
                with open(temp_output, 'rb') as f:
                    dxf_data = f.read()

                results.append({
                    'pair_name': f"{main_drawing} vs {source_drawing}",
                    'main_drawing': main_drawing,
                    'source_drawing': source_drawing,
                    'output_filename': output_filename,
                    'dxf_data': dxf_data,
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
                    'dxf_data': None,
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
                'dxf_data': None,
                'success': False,
                'error': str(e),
                'relation': pair.get('relation', 'なし'),
                'entity_counts': None,
                'change_label_count': change_label_count,
                'unchanged_label_count': unchanged_label_count
            })

    # 親子関係台帳を結果で更新（エンティティ数を含む）
    if master_df is not None:
        # 結果からペア情報を作成（エンティティ数を含む）
        pairs_with_entity_counts = []
        for result in results:
            if result['success']:
                # 元のペア情報を取得
                original_pair = next((p for p in complete_pairs
                                     if p['main_drawing'] == result['main_drawing']
                                     and p['source_drawing'] == result['source_drawing']), None)

                if original_pair:
                    # エンティティ数を追加したペア情報を作成
                    pair_with_counts = original_pair.copy()
                    pair_with_counts['entity_counts'] = result['entity_counts']
                    pairs_with_entity_counts.append(pair_with_counts)

        # 親子関係台帳を更新
        if pairs_with_entity_counts:
            master_df, _ = update_parent_child_master(master_df, pairs_with_entity_counts)

    # ZIPアーカイブを作成
    zip_buffer = BytesIO()

    diff_labels_excel = build_diff_labels_workbook(diff_label_sheets)
    unchanged_labels_excel = build_unchanged_labels_workbook(unchanged_label_sheets)

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # 差分DXFファイルを追加
        for result in results:
            if result['success'] and result['dxf_data']:
                zip_file.writestr(result['output_filename'], result['dxf_data'])

        # ラベル比較ファイルを追加
        if diff_labels_excel:
            zip_file.writestr(DIFF_LABELS_FILENAME, diff_labels_excel)
        if unchanged_labels_excel:
            zip_file.writestr(UNCHANGED_LABELS_FILENAME, unchanged_labels_excel)

        # 親子関係台帳ファイルを追加（存在する場合）
        if master_df is not None:
            master_excel_data = save_master_to_bytes(master_df)
            # アップロードされたファイル名を使用、なければデフォルト名を使用
            output_master_filename = master_filename if master_filename else diff_config.MASTER_FILENAME
            zip_file.writestr(output_master_filename, master_excel_data)

    zip_buffer.seek(0)
    zip_data = zip_buffer.getvalue()

    # 一時ファイルの削除
    for temp_file in temp_output_files:
        try:
            os.unlink(temp_file)
        except:
            pass

    return zip_data, results, diff_labels_excel, unchanged_labels_excel


def initialize_session_state():
    """セッション状態を初期化"""
    if 'uploaded_files_dict' not in st.session_state:
        st.session_state.uploaded_files_dict = {}

    if 'pairs' not in st.session_state:
        st.session_state.pairs = []

    if 'master_df' not in st.session_state:
        st.session_state.master_df = None

    if 'master_file_name' not in st.session_state:
        st.session_state.master_file_name = None

    if 'added_relationships_count' not in st.session_state:
        st.session_state.added_relationships_count = 0

    if 'uploader_key' not in st.session_state:
        st.session_state.uploader_key = 0

    if 'prefix_text_input' not in st.session_state:
        st.session_state.prefix_text_input = "\n".join(DEFAULT_PREFIXES)


def render_custom_styles():
    """カスタムCSSスタイルを適用"""
    st.markdown(f"""
        <style>
        .stButton > button {{
            background-color: {ui_config.PRIMARY_COLOR};
            color: white;
            border: 1px solid {ui_config.PRIMARY_COLOR};
        }}
        .stButton > button:hover {{
            background-color: {ui_config.HOVER_COLOR};
            color: white;
            border: 1px solid {ui_config.HOVER_COLOR};
        }}
        .stButton > button:focus {{
            background-color: {ui_config.PRIMARY_COLOR};
            color: white;
            border: 1px solid {ui_config.PRIMARY_COLOR};
            box-shadow: 0 0 0 0.2rem {ui_config.FOCUS_SHADOW_COLOR};
        }}
        .stDownloadButton > button {{
            background-color: {ui_config.PRIMARY_COLOR};
            color: white;
            border: 1px solid {ui_config.PRIMARY_COLOR};
        }}
        .stDownloadButton > button:hover {{
            background-color: {ui_config.HOVER_COLOR};
            color: white;
            border: 1px solid {ui_config.HOVER_COLOR};
        }}
        .stDownloadButton > button:focus {{
            background-color: {ui_config.PRIMARY_COLOR};
            color: white;
            border: 1px solid {ui_config.PRIMARY_COLOR};
            box-shadow: 0 0 0 0.2rem {ui_config.FOCUS_SHADOW_COLOR};
        }}
        </style>
    """, unsafe_allow_html=True)


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
        tuple: (complete_pairs, missing_pairs)
    """
    if not st.session_state.pairs:
        return [], []

    st.subheader("図面ペア・リスト")

    complete_pairs = [p for p in st.session_state.pairs if p['status'] == 'complete']
    missing_pairs = [p for p in st.session_state.pairs if p['status'] == 'missing_source']
    no_source_pairs = [p for p in st.session_state.pairs if p['status'] == 'no_source_defined']

    # 差分抽出可能なペア
    if complete_pairs:
        st.success(f"差分抽出が可能なペア: {len(complete_pairs)}組")

        pair_data = []
        for pair in complete_pairs:
            pair_data.append({
                '図番（新）': pair['main_drawing'],
                '比較元図番（旧）': pair['source_drawing'],
                '関係': pair.get('relation', 'なし'),
                'ステータス': '✅ 差分抽出可能'
            })

        st.dataframe(pair_data, width='stretch', hide_index=True)

    # 比較元の旧図面が不足しているペア
    if missing_pairs:
        st.warning(f"⚠️ 比較元の旧図面がないペア: {len(missing_pairs)}組")

        missing_data = []
        missing_drawings = []
        for pair in missing_pairs:
            missing_data.append({
                '図番（新）': pair['main_drawing'],
                '比較元図番（旧）': pair['source_drawing'],
                '関係': pair.get('relation', 'なし'),
                'ステータス': '⚠️ 比較元図面なし'
            })
            missing_drawings.append(pair['source_drawing'])

        st.dataframe(missing_data, width='stretch', hide_index=True)
        st.info(f"不足している図面: {', '.join(missing_drawings)}")

    # 流用元図番が指定されていないペア
    if no_source_pairs:
        st.info(f"流用元図番の記載がない図面: {len(no_source_pairs)}件（比較対象外）")

        no_source_data = []
        for pair in no_source_pairs:
            no_source_data.append({
                '図番': pair['main_drawing'],
                '関係': pair.get('relation') or 'なし',
                'ステータス': '⚠️ 流用元図番の未記入'
            })

        with st.expander("詳細を表示"):
            st.dataframe(no_source_data, width='stretch', hide_index=True)

    # 親子関係台帳更新状況の表示
    if st.session_state.master_df is not None and st.session_state.added_relationships_count > 0:
        st.success(f"親子関係台帳に {st.session_state.added_relationships_count} 件の新しい関係を追加しました")

    return complete_pairs, missing_pairs

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


def app():
    st.title(ui_config.TITLE)
    st.write(ui_config.SUBTITLE)

    render_custom_styles()
    render_help_section()
    initialize_session_state()

    # 親子関係台帳ファイルのアップロード
    st.subheader("Step 0: 親子関係台帳ファイルのアップロード")

    master_file = st.file_uploader(
        "親子関係台帳Excelファイルをアップロードしてください（オプション）",
        type=ui_config.MASTER_FILE_TYPES,
        key=f"master_upload_{st.session_state.uploader_key}",
        help="親子関係を一元管理するExcelファイルです。新しく見つかった親子関係が自動的に追加されます。"
    )

    # 台帳ファイルの読み込み（ファイルがアップロードされた時点で自動処理）
    if master_file is not None:
        # まだ読み込まれていない場合、または異なるファイルの場合のみ読み込む
        if st.session_state.master_df is None or st.session_state.get('master_file_name') != master_file.name:
            master_df = load_parent_child_master(master_file)
            if master_df is not None:
                st.session_state.master_df = master_df
                st.session_state.master_file_name = master_file.name  # アップロードされたファイルの元の名前を保存
                st.session_state.added_relationships_count = 0  # リセット
                st.success(f"記録済み親子関係（{len(master_df)}件のレコード）")
        else:
            # 既に読み込まれている場合は状態表示のみ
            st.info(f"既存の親子関係に追加します（{len(st.session_state.master_df)}件のレコード）")
    else:
        # ファイルがアップロードされていない場合、セッション状態をクリア
        if st.session_state.master_df is not None:
            st.session_state.master_df = None
            st.session_state.master_file_name = None
            st.session_state.added_relationships_count = 0

    st.divider()

    # ファイルアップロード
    st.subheader("Step 1: DXFファイルのアップロード")

    col1, col2 = st.columns([3, 1])

    with col1:
        uploaded_files = st.file_uploader(
            "DXFファイルをアップロードしてください（複数可・フォルダ可）",
            type=ui_config.DXF_FILE_TYPES,
            accept_multiple_files=True,
            key=f"initial_upload_{st.session_state.uploader_key}"
        )

    with col2:
        process_button = st.button("図番を抽出", key="process_files", type="primary")

    # ファイル処理
    if process_button and uploaded_files:
        with st.spinner(f'{len(uploaded_files)}個のファイルから図番を抽出中...'):
            for uploaded_file in uploaded_files:
                file_info = extract_drawing_info_from_file(uploaded_file)
                if file_info:
                    main_drawing = file_info['main_drawing_number']
                    # 既存の図番の場合は上書き
                    st.session_state.uploaded_files_dict[main_drawing] = file_info

            # ペアリストを作成
            st.session_state.pairs = create_pair_list(st.session_state.uploaded_files_dict)

            # 親子関係台帳を更新
            added_count = update_master_if_needed(st.session_state.pairs)
            st.session_state.added_relationships_count += added_count

        st.success(f"{len(st.session_state.uploaded_files_dict)}個のファイルから図番を抽出しました")
        st.rerun()

    complete_pairs = []
    missing_pairs = []

    if st.session_state.pairs:
        complete_pairs, missing_pairs = render_pair_list()

        if missing_pairs:
            st.subheader("Step 2: 追加アップロード（オプション）")

            col1, col2 = st.columns([3, 1])

            with col1:
                additional_files = st.file_uploader(
                    "比較元図面が不足している場合はアップロードしてください",
                    type=ui_config.DXF_FILE_TYPES,
                    accept_multiple_files=True,
                    key=f"additional_upload_{st.session_state.uploader_key}"
                )

            with col2:
                add_button = st.button("ファイル追加", key="add_files", type="secondary")

            if add_button and additional_files:
                with st.spinner(f'{len(additional_files)}個のファイルを処理中...'):
                    for uploaded_file in additional_files:
                        file_info = extract_drawing_info_from_file(uploaded_file)
                        if file_info:
                            main_drawing = file_info['main_drawing_number']
                            st.session_state.uploaded_files_dict[main_drawing] = file_info

                    st.session_state.pairs = create_pair_list(st.session_state.uploaded_files_dict)
                    added_count = update_master_if_needed(st.session_state.pairs)
                    st.session_state.added_relationships_count += added_count

                st.success("ファイルを追加し図面ペア・リストを更新しました。")
                st.rerun()

        st.subheader("Step 3: 差分比較")

        # オプション設定
        with st.expander("オプション設定", expanded=False):
            col1, col2 = st.columns(2)

            with col1:
                tolerance = st.number_input(
                    "座標許容誤差",
                    min_value=1e-8,
                    max_value=1.0,
                    value=diff_config.DEFAULT_TOLERANCE,
                    format="%.8f",
                    help="差分判定の位置座標の比較における許容誤差です。大きくするほど座標の差を無視します。"
                )

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

            st.markdown("**未変更ラベルの中から抽出したい先頭文字列**")
            prefix_text = st.text_area(
                "1行につき1件を入力してください",
                value=st.session_state.prefix_text_input,
                height=150,
                help="prefix_config.txt に定義された初期値を基に編集できます。空行は無視されます。",
                key=f"prefix_text_area_{st.session_state.uploader_key}"
            )
            st.session_state.prefix_text_input = prefix_text
            prefix_list = get_prefix_list_from_state()

        # 比較開始ボタン
        if complete_pairs:
            st.info(f"差分抽出可能なペア: {len(complete_pairs)}組")

            if st.button("差分抽出開始", key="start_comparison", type="primary", disabled=len(complete_pairs) == 0):
                with st.spinner(f'{len(complete_pairs)}組のペアの差分を抽出中...'):
                    try:
                        zip_data, results, diff_labels_excel, unchanged_labels_excel = create_diff_zip(
                            st.session_state.pairs,
                            master_df=st.session_state.master_df,  # 親子関係台帳を渡す
                            master_filename=st.session_state.master_file_name,  # アップロードされたファイル名を渡す
                            tolerance=tolerance,
                            deleted_color=deleted_color,
                            added_color=added_color,
                            unchanged_color=unchanged_color,
                            prefixes=prefix_list
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
                            'unchanged_color': unchanged_color
                        }

                    except Exception as e:
                        handle_error(e)
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
                    '図番（新）': result['main_drawing'],
                    '比較元図番（旧）': result['source_drawing'],
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

                # ダウンロードボタンのラベルを作成
                download_label = f"ZIPでダウンロード ({successful_count}ファイル"
                if st.session_state.master_df is not None:
                    master_name = st.session_state.master_file_name if st.session_state.master_file_name else "親子関係台帳"
                    download_label += f" + {master_name}"
                download_label += " + diff_labels.xlsx + unchanged_labels.xlsx)"

                st.download_button(
                    label=download_label,
                    data=st.session_state.zip_data,
                    file_name="dxf_diff_results.zip",
                    mime="application/zip",
                    key="download_zip",
                    type="primary"
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
                # セッション状態をクリア
                for key in ['uploaded_files_dict', 'pairs', 'results', 'zip_data', 'processing_settings',
                            'master_df', 'master_file_name', 'added_relationships_count',
                            'diff_labels_excel_data', 'unchanged_labels_excel_data']:
                    if key in st.session_state:
                        del st.session_state[key]

                # ファイルアップロード入力をクリアするためにキーをインクリメント
                st.session_state.uploader_key += 1

                # 一時ファイルのクリーンアップ
                # （実際の本番環境では適切なクリーンアップが必要）

                st.rerun()

    else:
        st.info("DXFファイルをアップロードして「図番を抽出」ボタンをクリックしてください。")


if __name__ == "__main__":
    app()
