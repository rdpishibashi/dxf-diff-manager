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

st.set_page_config(
    page_title="DXF Diff Manager",
    page_icon="📊",
    layout="wide",
)


def load_parent_child_master(uploaded_file):
    """
    親子関係マスターファイル（Parent-Child_list.xlsx）を読み込む

    Args:
        uploaded_file: アップロードされたExcelファイル

    Returns:
        DataFrame: 親子関係マスターのデータフレーム
    """
    try:
        df = pd.read_excel(uploaded_file)

        # 必要なカラムが存在するか確認
        required_columns = ['Parent', 'Child']
        for col in required_columns:
            if col not in df.columns:
                st.error(f"必須カラム '{col}' が見つかりません。")
                return None

        return df

    except Exception as e:
        st.error(f"親子関係マスターファイルの読み込み中にエラーが発生しました: {str(e)}")
        return None


def update_parent_child_master(master_df, new_pairs):
    """
    親子関係マスターに新しいペアを追加する（重複はスキップ）

    Args:
        master_df: 既存の親子関係マスターDataFrame
        new_pairs: 新しいペア情報のリスト

    Returns:
        tuple: (更新されたDataFrame, 追加された件数)
    """
    added_count = 0
    new_records = []

    for pair in new_pairs:
        parent = pair.get('source_drawing')  # 流用元図番がParent
        child = pair.get('main_drawing')      # 図番がChild

        if not parent or not child:
            continue

        # 既存のレコードに同じ親子関係が存在するか確認
        exists = ((master_df['Parent'] == parent) & (master_df['Child'] == child)).any()

        if not exists:
            # 新しいレコードを追加（ParentとChild、Dateのみ。Functionは空のまま）
            new_record = {
                'Parent': parent,
                'Child': child,
                'Date': datetime.now()
            }
            new_records.append(new_record)
            added_count += 1

    if new_records:
        # 新しいレコードを追加
        new_df = pd.DataFrame(new_records)
        updated_df = pd.concat([master_df, new_df], ignore_index=True)
    else:
        updated_df = master_df

    return updated_df, added_count


def save_master_to_bytes(master_df):
    """
    親子関係マスターDataFrameをExcelバイトデータに変換

    Args:
        master_df: 親子関係マスターDataFrame

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
        uploaded_file: Streamlitのアップロードファイルオブジェクト

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

        # 図面番号を抽出
        _, info = extract_labels(
            temp_path,
            filter_non_parts=False,
            sort_order="none",
            debug=False,
            selected_layers=None,
            validate_ref_designators=False,
            extract_drawing_numbers_option=True
        )

        # 図番が見つからない場合はファイル名を使用
        main_drawing = info.get('main_drawing_number')
        if not main_drawing:
            main_drawing = Path(uploaded_file.name).stem

        return {
            'filename': uploaded_file.name,
            'temp_path': temp_path,
            'main_drawing_number': main_drawing,
            'source_drawing_number': info.get('source_drawing_number')
        }

    except Exception as e:
        st.error(f"ファイル {uploaded_file.name} の処理中にエラーが発生しました: {str(e)}")
        return None


def create_pair_list(uploaded_files_dict):
    """
    アップロードされたファイル情報からペアリストを作成

    Args:
        uploaded_files_dict: 図番をキーとしたファイル情報の辞書

    Returns:
        list: ペア情報のリスト
    """
    pairs = []
    processed_mains = set()

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
                'status': 'complete' if source_file_info else 'missing_source'
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
                'status': 'no_source_defined'
            }
            pairs.append(pair)
            processed_mains.add(main_drawing)

    return pairs


def create_diff_zip(pairs, master_df=None, tolerance=0.01, deleted_color=6, added_color=4, unchanged_color=7):
    """
    ペアリストに基づいて差分DXFファイルを作成し、ZIPアーカイブを生成

    Args:
        pairs: ペア情報のリスト
        master_df: 親子関係マスターDataFrame（Noneでない場合はZIPに含める）
        tolerance: 座標許容誤差
        deleted_color: 削除エンティティの色
        added_color: 追加エンティティの色
        unchanged_color: 変更なしエンティティの色

    Returns:
        tuple: (zip_data, results)
    """
    results = []
    temp_output_files = []

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

        try:
            # DXF比較処理（図番（新）を基準A、流用元図番（旧）を比較対象B）
            success = compare_dxf_files_and_generate_dxf(
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
                    'success': True
                })
            else:
                results.append({
                    'pair_name': f"{main_drawing} vs {source_drawing}",
                    'main_drawing': main_drawing,
                    'source_drawing': source_drawing,
                    'output_filename': output_filename,
                    'dxf_data': None,
                    'success': False
                })

        except Exception as e:
            st.error(f"ペア {main_drawing} vs {source_drawing} の処理中にエラーが発生しました: {str(e)}")
            results.append({
                'pair_name': f"{main_drawing} vs {source_drawing}",
                'main_drawing': main_drawing,
                'source_drawing': source_drawing,
                'output_filename': output_filename,
                'dxf_data': None,
                'success': False,
                'error': str(e)
            })

    # ZIPアーカイブを作成
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # 差分DXFファイルを追加
        for result in results:
            if result['success'] and result['dxf_data']:
                zip_file.writestr(result['output_filename'], result['dxf_data'])

        # 親子関係マスターファイルを追加（存在する場合）
        if master_df is not None:
            master_excel_data = save_master_to_bytes(master_df)
            zip_file.writestr('Parent-Child_list.xlsx', master_excel_data)

    zip_buffer.seek(0)
    zip_data = zip_buffer.getvalue()

    # 一時ファイルの削除
    for temp_file in temp_output_files:
        try:
            os.unlink(temp_file)
        except:
            pass

    return zip_data, results


def app():
    st.title('DXF Diff Manager - DXF差分管理ツール')
    st.write('流用図面と元図面を自動的にペアリングし、差分をDXFフォーマットで出力します。親子関係マスターも更新します。')

    # ボタンのスタイルをカスタマイズ（青色背景と枠）
    st.markdown("""
        <style>
        .stButton > button {
            background-color: #0066cc;
            color: white;
            border: 1px solid #0066cc;
        }
        .stButton > button:hover {
            background-color: #0052a3;
            color: white;
            border: 1px solid #0052a3;
        }
        .stButton > button:focus {
            background-color: #0066cc;
            color: white;
            border: 1px solid #0066cc;
            box-shadow: 0 0 0 0.2rem rgba(0, 102, 204, 0.5);
        }
        .stDownloadButton > button {
            background-color: #0066cc;
            color: white;
            border: 1px solid #0066cc;
        }
        .stDownloadButton > button:hover {
            background-color: #0052a3;
            color: white;
            border: 1px solid #0052a3;
        }
        .stDownloadButton > button:focus {
            background-color: #0066cc;
            color: white;
            border: 1px solid #0066cc;
            box-shadow: 0 0 0 0.2rem rgba(0, 102, 204, 0.5);
        }
        </style>
    """, unsafe_allow_html=True)

    # プログラム説明
    with st.expander("ℹ️ プログラム説明", expanded=False):
        help_text = [
            "このツールは、複数のDXFファイルから図面番号と流用元図番を自動抽出し、",
            "ペアごとに差分を比較してDXFファイルとして出力します。",
            "",
            "**使用手順：**",
            "1. （オプション）親子関係管理台帳をアップロードすると、新しい親子関係が自動的に追加されます",
            "2. DXFファイルを一括アップロードしてください（複数可）",
            "3. 自動的に図番と流用元図番が抽出され、ペアリストが表示されます",
            "4. 流用元図面が不足している場合は「追加アップロード」で追加できます",
            "5. 「差分比較を開始」ボタンをクリックして処理を実行します",
            "6. 完全なペアのみが処理され、ZIPファイルで一括ダウンロードできます",
            "7. ZIPには差分DXFファイルと更新された親子関係マスター（アップロードした場合）が含まれます",
            "",
            "**出力DXFファイルの内容：**",
            "- ADDED (デフォルト色: シアン): 新図面にのみ存在する要素（追加された要素）",
            "- DELETED (デフォルト色: マゼンタ): 旧図面にのみ存在する要素（削除された要素）",
            "- UNCHANGED (デフォルト色: 白/黒): 両方の図面に存在し変更がない要素",
            "",
            "**注意事項：**",
            "- 図番が抽出できない場合はファイル名が図番として使用されます",
            "- 図番（新）を基準A、流用元図番（旧）を比較対象Bとして比較します",
            "- 流用元図番が指定されていない図面は比較対象外となります",
            "- 親子関係マスターには、完全なペア（図番と流用元図番の両方が存在する）のみが追加されます"
        ]

        st.info("\n".join(help_text))

    # セッション状態の初期化
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

    # 親子関係マスターファイルのアップロード
    st.subheader("Step 0: 親子関係台帳ファイルのアップロード")

    master_file = st.file_uploader(
        "親子関係台帳ファイルをアップロードしてください（オプション）",
        type=["xlsx"],
        key="master_upload",
        help="親子関係を一元管理するExcelファイルです。新しく見つかった親子関係が自動的に追加されます。"
    )

    # マスターファイルの読み込み（ファイルがアップロードされた時点で自動処理）
    if master_file is not None:
        # まだ読み込まれていない場合、または異なるファイルの場合のみ読み込む
        if st.session_state.master_df is None or st.session_state.get('master_file_name') != master_file.name:
            master_df = load_parent_child_master(master_file)
            if master_df is not None:
                st.session_state.master_df = master_df
                st.session_state.master_file_name = master_file.name
                st.session_state.added_relationships_count = 0  # リセット
                st.success(f"親子関係を読み込みました（{len(master_df)}件のレコード）")
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
            "DXFファイルを選択してください（複数可）",
            type="dxf",
            accept_multiple_files=True,
            key="initial_upload"
        )

    with col2:
        process_button = st.button("図番を抽出", key="process_files", type="primary")

    # ファイル処理
    if process_button and uploaded_files:
        with st.spinner(f'{len(uploaded_files)}個のDXFファイルを処理中...'):
            for uploaded_file in uploaded_files:
                file_info = extract_drawing_info_from_file(uploaded_file)
                if file_info:
                    main_drawing = file_info['main_drawing_number']
                    # 既存の図番の場合は上書き
                    st.session_state.uploaded_files_dict[main_drawing] = file_info

            # ペアリストを作成
            st.session_state.pairs = create_pair_list(st.session_state.uploaded_files_dict)

            # 親子関係マスターが読み込まれている場合、更新する
            if st.session_state.master_df is not None:
                # 完全なペアのみマスターに追加
                complete_pairs = [p for p in st.session_state.pairs if p['status'] == 'complete']
                if complete_pairs:
                    updated_master, added_count = update_parent_child_master(
                        st.session_state.master_df,
                        complete_pairs
                    )
                    st.session_state.master_df = updated_master
                    st.session_state.added_relationships_count += added_count

        st.success(f"{len(st.session_state.uploaded_files_dict)}個のファイルを処理しました")
        st.rerun()

    # アップロード済みファイルの表示
    if st.session_state.uploaded_files_dict:
        st.subheader("アップロード済みファイル一覧")

        file_list_data = []
        for main_drawing, file_info in st.session_state.uploaded_files_dict.items():
            file_list_data.append({
                '図番': main_drawing,
                'ファイル名': file_info['filename'],
                '流用元図番': file_info.get('source_drawing_number') or 'なし'
            })

        st.dataframe(file_list_data, width='stretch', hide_index=True)

        # ペアリストの表示
        st.subheader("図面ペア・リスト")

        complete_pairs = [p for p in st.session_state.pairs if p['status'] == 'complete']
        missing_pairs = [p for p in st.session_state.pairs if p['status'] == 'missing_source']
        no_source_pairs = [p for p in st.session_state.pairs if p['status'] == 'no_source_defined']

        # 完全なペア
        if complete_pairs:
            st.success(f"完全なペア: {len(complete_pairs)}組")

            pair_data = []
            for pair in complete_pairs:
                pair_data.append({
                    '図番（新）': pair['main_drawing'],
                    '流用元図番（旧）': pair['source_drawing'],
                    'ステータス': '✅ 完全'
                })

            st.dataframe(pair_data, width='stretch', hide_index=True)

        # 流用元図面が不足しているペア
        if missing_pairs:
            st.warning(f"⚠️ 流用元図面がないペア: {len(missing_pairs)}組")

            missing_data = []
            missing_drawings = []
            for pair in missing_pairs:
                missing_data.append({
                    '図番（新）': pair['main_drawing'],
                    '流用元図番（旧）': pair['source_drawing'],
                    'ステータス': '⚠️ 流用元図面なし'
                })
                missing_drawings.append(pair['source_drawing'])

            st.dataframe(missing_data, width='stretch', hide_index=True)

            st.info(f"不足している図番: {', '.join(missing_drawings)}")

        # 流用元図番が指定されていないペア
        if no_source_pairs:
            st.info(f"流用元図番が指定されていない図面: {len(no_source_pairs)}件（比較対象外）")

            no_source_data = []
            for pair in no_source_pairs:
                no_source_data.append({
                    '図番': pair['main_drawing'],
                    'ステータス': 'ℹ️ 流用元図番の未記入'
                })

            with st.expander("詳細を表示"):
                st.dataframe(no_source_data, width='stretch', hide_index=True)

        # 親子関係マスター更新状況の表示
        if st.session_state.master_df is not None and st.session_state.added_relationships_count > 0:
            st.success(f"親子関係台帳に {st.session_state.added_relationships_count} 件の新しい関係を追加しました")

        # 追加アップロード
        if missing_pairs:
            st.subheader("Step 2: 追加アップロード（オプション）")

            col1, col2 = st.columns([3, 1])

            with col1:
                additional_files = st.file_uploader(
                    "不足している流用元図面をアップロードしてください",
                    type="dxf",
                    accept_multiple_files=True,
                    key="additional_upload"
                )

            with col2:
                add_button = st.button("追加・更新", key="add_files", type="secondary")

            if add_button and additional_files:
                with st.spinner(f'{len(additional_files)}個のDXFファイルを処理中...'):
                    for uploaded_file in additional_files:
                        file_info = extract_drawing_info_from_file(uploaded_file)
                        if file_info:
                            main_drawing = file_info['main_drawing_number']
                            st.session_state.uploaded_files_dict[main_drawing] = file_info

                    # ペアリストを更新
                    st.session_state.pairs = create_pair_list(st.session_state.uploaded_files_dict)

                    # 親子関係マスターが読み込まれている場合、更新する
                    if st.session_state.master_df is not None:
                        # 完全なペアのみマスターに追加
                        complete_pairs = [p for p in st.session_state.pairs if p['status'] == 'complete']
                        if complete_pairs:
                            updated_master, added_count = update_parent_child_master(
                                st.session_state.master_df,
                                complete_pairs
                            )
                            st.session_state.master_df = updated_master
                            st.session_state.added_relationships_count += added_count

                st.success(f"ファイルを追加しました。図面ペアリストが更新されました。")
                st.rerun()

        # 比較開始
        st.subheader("🚀 ステップ3: 差分比較")

        # オプション設定
        with st.expander("オプション設定", expanded=False):
            col1, col2 = st.columns(2)

            with col1:
                tolerance = st.number_input(
                    "座標許容誤差",
                    min_value=1e-8,
                    max_value=1.0,
                    value=0.01,
                    format="%.8f",
                    help="図面の位置座標の比較における許容誤差です。大きくすると微小な違いを無視します。"
                )

            with col2:
                st.write("**レイヤー色設定**")

                deleted_color = st.selectbox(
                    "削除エンティティの色（流用元図面のみ）",
                    options=[(1, "1 - 赤"), (2, "2 - 黄"), (3, "3 - 緑"), (4, "4 - シアン"), (5, "5 - 青"), (6, "6 - マゼンタ"), (7, "7 - 白/黒")],
                    index=5,  # デフォルト: マゼンタ
                    format_func=lambda x: x[1]
                )[0]

                added_color = st.selectbox(
                    "追加エンティティの色（新図面のみ）",
                    options=[(1, "1 - 赤"), (2, "2 - 黄"), (3, "3 - 緑"), (4, "4 - シアン"), (5, "5 - 青"), (6, "6 - マゼンタ"), (7, "7 - 白/黒")],
                    index=3,  # デフォルト: シアン
                    format_func=lambda x: x[1]
                )[0]

                unchanged_color = st.selectbox(
                    "変更なしエンティティの色",
                    options=[(1, "1 - 赤"), (2, "2 - 黄"), (3, "3 - 緑"), (4, "4 - シアン"), (5, "5 - 青"), (6, "6 - マゼンタ"), (7, "7 - 白/黒")],
                    index=6,  # デフォルト: 白/黒
                    format_func=lambda x: x[1]
                )[0]

        # 比較開始ボタン
        if complete_pairs:
            st.info(f"比較可能なペア: {len(complete_pairs)}組")

            if st.button("差分比較を開始", key="start_comparison", type="primary", disabled=len(complete_pairs) == 0):
                with st.spinner(f'{len(complete_pairs)}組のペアを比較中...'):
                    try:
                        zip_data, results = create_diff_zip(
                            st.session_state.pairs,
                            master_df=st.session_state.master_df,  # 親子関係マスターを渡す
                            tolerance=tolerance,
                            deleted_color=deleted_color,
                            added_color=added_color,
                            unchanged_color=unchanged_color
                        )

                        # セッション状態に保存
                        st.session_state.zip_data = zip_data
                        st.session_state.results = results
                        st.session_state.processing_settings = {
                            'tolerance': tolerance,
                            'deleted_color': deleted_color,
                            'added_color': added_color,
                            'unchanged_color': unchanged_color
                        }

                    except Exception as e:
                        handle_error(e)
        else:
            st.warning("比較可能な完全なペアがありません。流用元図面をアップロードしてください。")

        # 結果の表示
        if 'results' in st.session_state and st.session_state.results:
            st.subheader("処理結果")

            results = st.session_state.results
            settings = st.session_state.get('processing_settings', {})

            # 成功/失敗のサマリー
            successful_count = sum(1 for r in results if r['success'])
            total_count = len(results)

            if successful_count == total_count:
                st.success(f"全{total_count}組のペアの差分比較が完了しました")
            elif successful_count > 0:
                st.warning(f"{successful_count}/{total_count}組のペアの差分比較が完了しました。一部のペアで処理に失敗しました。")
            else:
                st.error("全てのペアで処理に失敗しました ❌")

            # 結果詳細
            result_data = []
            for result in results:
                status = "✅ 成功" if result['success'] else "❌ 失敗"
                result_data.append({
                    '図番（新）': result['main_drawing'],
                    '流用元図番（旧）': result['source_drawing'],
                    '出力ファイル名': result['output_filename'],
                    'ステータス': status
                })

            st.dataframe(result_data, width='stretch', hide_index=True)

            # ダウンロードボタン
            if successful_count > 0:
                st.subheader("結果のダウンロード")

                # ダウンロードボタンのラベルを作成
                download_label = f"ZIPでダウンロード ({successful_count}ファイル"
                if st.session_state.master_df is not None:
                    download_label += " + 親子関係台帳"
                download_label += ")"

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
                **生成されたDXFファイルについて：**
                - ADDED (色{settings.get('added_color', 4)}): 新図面にのみ存在する要素（追加された要素）
                - DELETED (色{settings.get('deleted_color', 6)}): 旧図面にのみ存在する要素（削除された要素）
                - UNCHANGED (色{settings.get('unchanged_color', 7)}): 両方の図面に存在し変更がない要素
                - 座標許容誤差: {settings.get('tolerance', 0.01)}
                """)

            # 新しい比較を開始するボタン
            if st.button("🔄 新しい比較を開始", key="restart_button"):
                # セッション状態をクリア
                for key in ['uploaded_files_dict', 'pairs', 'results', 'zip_data', 'processing_settings',
                            'master_df', 'master_file_name', 'added_relationships_count']:
                    if key in st.session_state:
                        del st.session_state[key]

                # 一時ファイルのクリーンアップ
                # （実際の本番環境では適切なクリーンアップが必要）

                st.rerun()

    else:
        st.info("DXFファイルをアップロードして「図番を抽出」ボタンをクリックしてください。")


if __name__ == "__main__":
    app()
