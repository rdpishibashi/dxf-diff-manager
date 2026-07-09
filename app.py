import streamlit as st
import os
import re
import sys
import traceback
from pathlib import Path
import zipfile
from io import BytesIO
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
from utils.common_utils import save_uploadedfile, cleanup_stale_temp_files
from utils import pairing
from utils.pairing import build_pairs, build_pairs_from_list, primary_status_by_drawing
from utils.master_ledger import (
    load_parent_child_master,
    update_parent_child_master,
    create_empty_master_df,
    save_master_to_bytes,
    make_dataframe_arrow_compatible,
)
from utils.diff_export import create_diff_zip, DIFF_LABELS_FILENAME, UNCHANGED_LABELS_FILENAME

# 設定をインポート
from config import ui_config, diff_config, help_text

st.set_page_config(
    page_title="DXF Diff Manager",
    page_icon="📊",
    layout="wide",
)

PREFIX_CONFIG_PATH = Path(current_dir) / "prefix_config.txt"

# 図面管理台帳の新規作成時に使用する入力フォーマット
SHIBAN_PATTERN = re.compile(r'^[A-Z]{2}\d{2}-\d{4}-\d$')   # 例: AA11-1111-1
MODULE_PATTERN = re.compile(r'^[A-Z0-9]{4}$')              # 例: XXXX（英大文字・数字）
SIDE_PATTERN = re.compile(r'^[A-Z0-9]{3}$')                # 例: XXX（英大文字・数字）


def read_zip_member(zip_data, member_name):
    """zip_data（bytes）からメンバーを読み出す。存在しない場合は None。

    diff_labels.xlsx / unchanged_labels.xlsx を session_state に二重保持しないため、
    プレビュー表示時に zip_data から都度読み出す用途で使う。
    """
    if not zip_data:
        return None
    try:
        with zipfile.ZipFile(BytesIO(zip_data)) as zf:
            if member_name in zf.namelist():
                return zf.read(member_name)
    except Exception:
        pass
    return None


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


def create_pair_list(source_files_dict, dest_files_dict, progress_callback=None):
    """auto モード用ペアリング（薄いシム）。

    実体は流用判定と RevUp 判定を独立実行する `utils.pairing.build_pairs`。
    流用元グループ・流用先グループに限定してペアを生成する。
    """
    return build_pairs(source_files_dict, dest_files_dict, progress_callback=progress_callback)


def load_pair_list(uploaded_file):
    """
    ペアリストファイルを読み込む（ExcelまたはCSV）

    必須カラム: 流用元図番, 流用先図番
    （旧カラム名 比較元図番/比較先図番、または Reference/Target も後方互換で受け付ける）。
    ファイル読み込み（I/O）のみを担当し、カラム名・値の正規化は
    `utils.pairing.normalize_pair_list_columns()`（streamlit非依存）に委譲する。

    Returns:
        DataFrame or None（カラム名は 流用元図番/流用先図番 に統一）
    """
    try:
        if uploaded_file.name.lower().endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        elif uploaded_file.name.lower().endswith('.xls'):
            df = pd.read_excel(uploaded_file, engine='xlrd')
        else:
            df = pd.read_excel(uploaded_file)

        df, error_message = pairing.normalize_pair_list_columns(df)
        if error_message:
            st.error(error_message)
            return None
        return df

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
    if '_stale_tmp_swept' not in st.session_state:
        # リスタートを押さずに離脱した過去セッションの孤立一時ファイルを掃除する（新規セッションで一度だけ）
        cleanup_stale_temp_files()
        st.session_state['_stale_tmp_swept'] = True

    if 'step0_mode' not in st.session_state:
        st.session_state.step0_mode = 'new'

    if 'new_master_shiban_input' not in st.session_state:
        st.session_state.new_master_shiban_input = ''

    if 'new_master_module_input' not in st.session_state:
        st.session_state.new_master_module_input = ''

    if 'new_master_side_input' not in st.session_state:
        st.session_state.new_master_side_input = ''

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


def update_master_if_needed(pairs, mode=None):
    """図面管理台帳を更新（必要な場合のみ）

    差分抽出が可能なペア（complete）に加え、完全新規図面（流用元の参照がない
    図面、get_brand_new_drawing_pairs参照）も登録する。完全新規図面はこの時点
    ではエンティティ数が未確定（diff抽出を行わないため）なので、Parent="none"・
    Relation等のみを先行登録し、エンティティ数は create_diff_zip() 側で
    count_entities_in_dxf_file() により算出して追記する（2026-06 追加）。

    Args:
        pairs: ペア情報のリスト
        mode: ペアリング方式（'all_in_one'/'auto'/'pair_list'）。完全新規図面の
              判定（get_brand_new_drawing_pairs）に使用

    Returns:
        int: 追加された件数
    """
    if st.session_state.master_df is None:
        return 0

    complete_pairs = [p for p in pairs if p['status'] == 'complete']
    brand_new_pairs = get_brand_new_drawing_pairs(pairs, mode) if mode else []
    # Relation 欄に明示的な値を入れる（pairing.py 側の relation=None のまま登録すると
    # 台帳上で空欄になり、完全新規図面であることが分からなくなるため）
    brand_new_pairs = [dict(p, relation='完全新規図面') for p in brand_new_pairs]
    target_pairs = complete_pairs + brand_new_pairs
    if not target_pairs:
        return 0

    updated_master, added_count = update_parent_child_master(
        st.session_state.master_df,
        target_pairs
    )
    st.session_state.master_df = updated_master
    return added_count


def compute_total_drawings_count(mode):
    """Summaryシート「図面統計」の分母件数を算出する（utils.pairing の薄い呼び出し）。

    実体は streamlit 非依存の `utils.pairing.compute_total_drawings_count()`。
    本関数は session_state から必要な値を取り出して渡すだけの Driver 層アダプタ。
    """
    return pairing.compute_total_drawings_count(
        mode,
        all_in_one_count=len(st.session_state.all_in_one_files_dict),
        dest_count=len(st.session_state.dest_files_dict),
        pair_list_df=st.session_state.pair_list_df,
        uploaded_drawing_numbers=set(st.session_state.all_files_dict.keys()),
    )


def compute_unchanged_drawings(all_pairs, mode):
    """「変更していない図面」対象図番集合を算出する（utils.pairing の薄い呼び出し）。

    実体は streamlit 非依存の `utils.pairing.compute_unchanged_drawings()`。
    本関数は session_state から必要な値を取り出して渡すだけの Driver 層アダプタ。
    """
    return pairing.compute_unchanged_drawings(
        all_pairs, mode,
        source_drawing_numbers=set(st.session_state.source_files_dict.keys()),
        dest_drawing_numbers=set(st.session_state.dest_files_dict.keys()),
    )


def get_brand_new_drawing_pairs(all_pairs, mode):
    """完全新規図面のペアを算出する（utils.pairing の薄い呼び出し）。

    実体は streamlit 非依存の `utils.pairing.get_brand_new_drawing_pairs()`。
    本関数は session_state から必要な値を取り出して渡すだけの Driver 層アダプタ。
    """
    return pairing.get_brand_new_drawing_pairs(
        all_pairs, mode,
        source_drawing_numbers=set(st.session_state.source_files_dict.keys()),
        dest_drawing_numbers=set(st.session_state.dest_files_dict.keys()),
    )


def render_pair_list():
    """ペアリストを表示

    Returns:
        list: 差分抽出可能なペアのリスト
    """
    if not st.session_state.pairs:
        return []

    st.subheader("図面ペア・リスト")

    mode = st.session_state.step1_mode  # 'all_in_one'(A) / 'auto'(B) / 'pair_list'(C)

    all_pairs = st.session_state.pairs
    primary_status = primary_status_by_drawing(all_pairs)

    def _drawings_with(status):
        return {md for md, s in primary_status.items() if s == status}

    def _rows_with_primary(pairs_subset, status):
        # 同じ図番がより優先度の高い別ステータス（例: complete）でも分類済みの場合、
        # その図番に関する行はこのステータスの表からは除外する（二重計上防止）。
        allowed = _drawings_with(status)
        return [p for p in pairs_subset if p.get('main_drawing') in allowed]

    complete_pairs = [p for p in all_pairs if p['status'] == 'complete']
    missing_pairs = _rows_with_primary(
        [p for p in all_pairs if p['status'] == 'missing_source'], 'missing_source')
    missing_target_pairs = _rows_with_primary(
        [p for p in all_pairs if p['status'] == 'missing_target'], 'missing_target')
    missing_both_pairs = _rows_with_primary(
        [p for p in all_pairs if p['status'] == 'missing_both'], 'missing_both')
    # 片側のみのペアは流用先が空白（main_drawing なし）の行を含むため、
    # 優先度フィルタの対象外（main_drawing がある行のみ照合する）の行も素通しする。
    one_sided_drawings = _drawings_with('one_sided')
    one_sided_pairs = [
        p for p in all_pairs
        if p['status'] == 'one_sided' and (not p.get('main_drawing') or p['main_drawing'] in one_sided_drawings)
    ]

    # 「変更していない図面（流用元と流用先とで共通）」対象の図番集合
    unchanged_drawings = compute_unchanged_drawings(all_pairs, mode)

    # 「完全新規図面」: 排他化済み・ファイルアップロード済みの no_source_defined のみ
    # （get_brand_new_drawing_pairs参照。図面管理台帳への登録と同じ集合を使う）
    no_source_pairs = get_brand_new_drawing_pairs(all_pairs, mode)

    # 差分抽出が可能なペア
    # 「：N件」の件数は図面（main_drawing）のユニーク数（他セクションとの合計が
    # 流用先総数と一致するようにするため）。同じ図面が複数の流用元と比較される
    # 場合（RevUp と流用の双方で complete になる等）、表には全ペアを表示するため
    # 表の行数が件数より多くなることがある。
    if complete_pairs:
        st.success(f"差分抽出が可能なペア：{len({p['main_drawing'] for p in complete_pairs})}件")

        pair_data = []
        for pair in complete_pairs:
            pair_data.append({
                '流用先（新）': pair['main_drawing'],
                '流用元（旧）': pair['source_drawing'],
                '関係': pair.get('relation', 'なし'),
            })

        st.dataframe(pair_data, width='stretch', hide_index=True)

    if mode == 'pair_list':
        # Type C: 流用元/流用先のいずれか（または両方）のDXFファイルがない図番を
        # 1セクションに統合表示する（2026-06変更。Step2-2の未アップロード表示と
        # 統一感を持たせるため、missing_source/missing_target/missing_both の
        # 3セクションを「図面ファイルがない図番」1つにまとめた）。
        # 同じ流用先に RevUp の差分抽出可能ペアがある場合の注記は、Type C では
        # relation が常に 'ペアリスト'（RevUpという関係自体が存在しない）ため不要。
        # one_sided（流用先が空白）も、流用先のDXFファイルが無い点では実質的に
        # missing_target と同じ状況のため、本セクションに統合する（2026-06変更。
        # 旧「流用先がない流用元図面」セクションは廃止）。
        status_text = {
            'missing_source': '⚠️ 流用元 図面ファイルなし',
            'missing_target': '⚠️ 流用先 図面ファイルなし',
            'missing_both': '⚠️ 流用元・先 図面ファイルなし',
            'one_sided': '⚠️ 流用先 図面ファイルなし',
        }
        missing_file_pairs = missing_pairs + missing_target_pairs + missing_both_pairs + one_sided_pairs
        if missing_file_pairs:
            missing_file_data = [{
                '流用先（新）': pair['main_drawing'] or '（なし）',
                '流用元（旧）': pair['source_drawing'] or '（なし）',
                'ステータス': status_text[pair['status']],
            } for pair in missing_file_pairs]

            # 件数は行数（ペアリストの行＝宣言された関係の数）で数える。one_sided は
            # main_drawing が空（複数行が同じ空値に collapse する）ため、main_drawing
            # のユニーク数では正しく数えられない。
            with st.expander(f"⚠️ 図面ファイルがない図番：{len(missing_file_pairs)}件", expanded=True):
                st.dataframe(missing_file_data, width='stretch', hide_index=True)
    else:
        # Type A/B: 流用元のDXFファイルが未アップロードのペア（流用先の図面のみが対象。
        # missing_target/missing_both は方式C専用のステータスのため常に空）。
        # 同じ流用先に RevUp の差分抽出可能ペアがある場合は、その流用元図番を併記する。
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
                    status = f'⚠️ 流用元の図面ファイルなし・RevUpあり（{revup_source}）'
                else:
                    status = '⚠️ 流用元の図面ファイルなし'
                missing_data.append({
                    '流用先（新）': pair['main_drawing'],
                    '流用元（旧）': pair['source_drawing'],
                    '関係': pair.get('relation', 'なし'),
                    'ステータス': status
                })

            with st.expander(f"⚠️ 流用元図番の図面がない図面：{len({p['main_drawing'] for p in missing_pairs})}件", expanded=False):
                st.dataframe(missing_data, width='stretch', hide_index=True)

    # one_sided（流用先が空白の行）は mode == 'pair_list' の「図面ファイルがない図番」
    # に統合済み（上記参照）。Type A/Bでは one_sided は発生しない。

    # 完全新規図面（流用元図番なし）
    if no_source_pairs:
        no_source_data = []
        for pair in no_source_pairs:
            no_source_data.append({
                '図番': pair['main_drawing'],
                '関係': '完全新規図面',
                'ステータス': '流用元図番の指定なし'
            })

        with st.expander(f"完全新規図面（流用元図番なし）：{len(no_source_pairs)}件", expanded=False):
            st.dataframe(no_source_data, width='stretch', hide_index=True)

    # 変更していない図面（流用元と流用先とで共通）。Type A では表示しない
    if mode in ('auto', 'pair_list'):
        unchanged_data = sorted(unchanged_drawings)
        with st.expander(f"変更していない図面（流用元と流用先とで共通）：{len(unchanged_data)}件", expanded=False):
            if unchanged_data:
                st.dataframe(
                    pd.DataFrame({'図番': unchanged_data}),
                    width='stretch', hide_index=True
                )
            else:
                st.caption("該当する図面はありません。")

    # 図面管理台帳更新状況の表示
    if st.session_state.master_df is not None and st.session_state.added_relationships_count > 0:
        st.success(f"図面管理台帳に {st.session_state.added_relationships_count} 件の新しい関係を追加しました")

    return complete_pairs

def render_preview_dataframe(df, key_prefix):
    """プレビュー用データフレームの列幅を調整して表示"""
    display_df = make_dataframe_arrow_compatible(df)
    column_config = {
        col: st.column_config.Column(col, width="small")
        if col in ("Coordinate X", "Coordinate Y", "Count")
        else st.column_config.Column(col)
        for col in display_df.columns
    }
    st.dataframe(
        display_df,
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
            - extractor: ファイル情報抽出関数（_extract_by_filename /
                         extract_source_number_from_dest_file のいずれか）

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
        extractor = group['extractor']
        file_info = extractor(uploaded_file)
        gid = id(group)
        if file_info:
            main_drawing = file_info['main_drawing_number']
            # 同じ図番への再アップロードで上書きする場合、古い一時ファイルが孤立しないよう削除する
            old_info = group['files_dict'].get(main_drawing)
            if old_info:
                old_path = old_info.get('temp_path')
                if old_path and old_path != file_info.get('temp_path') and os.path.exists(old_path):
                    try:
                        os.unlink(old_path)
                    except Exception:
                        pass
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
    """Step 1: 図面管理台帳の設定"""
    st.subheader("Step 1: 図面管理台帳の設定")

    prev_step0_mode = st.session_state.step0_mode

    step0_mode = st.radio(
        "台帳の利用方法",
        options=['upload', 'new', 'none'],
        format_func=lambda x: {
            'upload': '既存の図面管理台帳のアップロード',
            'new': '図面管理台帳の新規作成',
            'none': '図面管理台帳を作成せず',
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
        col1, col2 = st.columns([1, 3])
        with col1:
            st.write("指番を入力")
        with col2:
            shiban = st.text_input(
                "指番を入力", key='new_master_shiban_input',
                placeholder="AA11-1111-1", label_visibility='collapsed',
            )

        col1, col2 = st.columns([1, 3])
        with col1:
            st.write("モジュールを入力")
        with col2:
            module = st.text_input(
                "モジュールを入力", key='new_master_module_input',
                placeholder="XXXX（未入力可）", label_visibility='collapsed',
            )

        col1, col2 = st.columns([1, 3])
        with col1:
            st.write("サイド")
        with col2:
            side = st.text_input(
                "サイド", key='new_master_side_input',
                placeholder="XXX（未入力可）", label_visibility='collapsed',
            )

        shiban = (shiban or '').strip()
        module = (module or '').strip()
        side = (side or '').strip()

        errors = []
        if not shiban:
            st.info("指番を入力してください（例: AA11-1111-1）。")
        elif not SHIBAN_PATTERN.match(shiban):
            errors.append("指番のフォーマットが不正です。例: AA11-1111-1（英大文字2桁-数字4桁-数字1桁）")
        if module and not MODULE_PATTERN.match(module):
            errors.append("モジュールのフォーマットが不正です。例: XXXX（英大文字または数字4桁）")
        if side and not SIDE_PATTERN.match(side):
            errors.append("サイドのフォーマットが不正です。例: XXX（英大文字または数字3桁）")

        for err in errors:
            st.error(err)

        if errors or not shiban:
            st.session_state.master_df = None
            st.session_state.master_file_name = None
            st.session_state.added_relationships_count = 0
        else:
            module_part = module if module else 'na'
            side_part = side if side else 'na'
            master_filename = f"{shiban}_{module_part}_{side_part}.xlsx"

            col1, col2 = st.columns([1, 3])
            with col1:
                st.write("図面管理台帳")
            with col2:
                st.write(f"**{master_filename}**")

            if st.session_state.master_df is None:
                st.session_state.master_df = create_empty_master_df()
                st.session_state.added_relationships_count = 0
            st.session_state.master_file_name = master_filename

            st.info(f"新規台帳「{master_filename}」を作成します。差分抽出後、台帳が自動更新されてダウンロードZIPに含まれます。")

    elif step0_mode == 'upload':
        master_file = st.file_uploader(
            "図面管理台帳Excelファイルをアップロードしてください",
            type=ui_config.MASTER_FILE_TYPES,
            key=f"master_upload_{st.session_state.uploader_key}",
            help="親子関係を一元管理するExcelファイルです。新しく見つかった親子関係が自動的に追加されます。"
        )

        if master_file is not None:
            if st.session_state.master_df is None or st.session_state.get('master_file_name') != master_file.name:
                master_df, error_message = load_parent_child_master(master_file)
                if error_message:
                    st.error(error_message)
                elif master_df is not None:
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

    else:  # 'none'
        st.session_state.master_df = None
        st.session_state.master_file_name = None
        st.session_state.added_relationships_count = 0
        st.info("図面管理台帳は作成・更新しません。差分抽出結果（差分DXF・ラベルリスト）のみをZIPで出力します。")


def render_step1_upload():
    """Step 2: DXFファイルのアップロードと図番抽出

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
    """自動ペアリングモードのStep 2"""
    # Step 2-1: 流用元DXFファイルのアップロード
    st.subheader("Step 2-1: 流用元（旧）DXFファイルのアップロード")
    st.caption("ファイル名（拡張子なし）が図番として使用されます。")

    source_uploaded_files = st.file_uploader(
        "流用元（旧）DXFファイルをアップロードしてください（複数可・フォルダ可・複数回可）",
        type=ui_config.DXF_FILE_TYPES,
        accept_multiple_files=True,
        key=f"source_upload_{st.session_state.source_upload_key}",
        help="流用元となる旧図面をアップロードしてください"
    )

    render_upload_status('source_upload_summary', 'source_upload_failures', '流用元')

    source_count = len(st.session_state.source_files_dict)
    if source_count > 0:
        st.info(f"流用元（旧）図面: {source_count}件 読み込み済み")

    # Step 2-2: 流用先DXFファイルのアップロード
    st.subheader("Step 2-2: 流用先（新）DXFファイルのアップロード")

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
    """ペアリストモードのStep 2

    Returns:
        tuple: (all_count, 0)
    """
    # Step 2-1: ペアリストのアップロード
    st.subheader("Step 2-1: ペアリストのアップロード")
    st.caption(
        "流用元図番（旧）と流用先図番（新）のペアを記載したExcelまたはCSVファイルをアップロードしてください。\n"
        "必須カラム：**流用元図番** と **流用先図番**（旧名 **比較元図番**/**比較先図番**、"
        "または **Reference** と **Target** も使用可）"
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

    # Step 2-2: DXFファイルのアップロード
    st.subheader("Step 2-2: DXFファイルのアップロード（流用元・流用先まとめて）")
    st.caption("ファイル名（拡張子なし）が図番として使用されます。流用元と流用先のファイルをまとめてアップロードしてください。")

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
        ref = _norm(row['流用元図番'])
        target = _norm(row['流用先図番'])
        # 流用元と流用先が同一図番（identical）の行も、列に記載されている図番として
        # 未アップロード判定の対象に含める（2026-06修正。以前は比較対象外として
        # スキップしていたため、ファイルが無い「変更していない図面」宣言があっても
        # ここには現れなかった）。
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

    # 流用元・流用先の両方の未アップロード図番を1セクションにまとめて表示する
    # （2026-06変更。タイトルには件数の異なる2つのリストの件数を1つの数値として
    # 表示できないため、表の最終行に「合計件数」として各列の件数を表示する）。
    max_len = max(len(missing_ref), len(missing_target))
    missing_data = {
        '流用元図番（未アップロード）': missing_ref + [''] * (max_len - len(missing_ref)),
        '流用先図番（未アップロード）': missing_target + [''] * (max_len - len(missing_target)),
    }
    missing_df = pd.DataFrame(missing_data)
    total_row = pd.DataFrame({
        '流用元図番（未アップロード）': [f'合計件数：{len(missing_ref)}件'],
        '流用先図番（未アップロード）': [f'合計件数：{len(missing_target)}件'],
    })
    missing_df = pd.concat([missing_df, total_row], ignore_index=True)

    with st.expander("⚠️ 未アップロードの図番", expanded=True):
        st.dataframe(missing_df, hide_index=True, width='stretch')


def _render_step1_all_in_one_mode():
    """一括アップロードモードのStep 2

    全DXFファイルをまとめてアップロードし、各ファイルのDXFから
    流用元図番を抽出してペアを自動作成する。

    Returns:
        tuple: (all_in_one_count, 0)
    """
    st.subheader("Step 2: DXFファイルの一括アップロード")
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
    """Step 3: 図面ペア・リスト作成

    Args:
        source_count: 流用元件数（auto）またはDXFファイル件数（その他モード）
        dest_count:   流用先件数（auto）または 0（その他モード）

    Returns:
        tuple: (complete_pairs, pairs_ready)
    """
    mode = st.session_state.step1_mode
    st.subheader("Step 3: 図面ペア・リスト確認")

    if mode == 'pair_list':
        pair_list_ready = st.session_state.pair_list_df is not None
        has_files = source_count > 0
        ready_to_pair = pair_list_ready and has_files
        if not ready_to_pair:
            st.info("Step 2-1でペアリストをアップロードしてください。" if not pair_list_ready
                    else "Step 2-2でDXFファイルをアップロードしてください。")
        else:
            st.write(f"ペアリスト: {len(st.session_state.pair_list_df)}組、DXFファイル: {source_count}件")
    elif mode == 'all_in_one':
        ready_to_pair = source_count > 0
        if not ready_to_pair:
            st.info("Step 2でDXFファイルをアップロードしてください。")
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
        added_count = update_master_if_needed(st.session_state.pairs, mode=mode)
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
    """Step 4: 差分比較（ペアが準備完了時）

    Args:
        complete_pairs: 差分抽出可能なペアのリスト
    """
    # オプション設定
    with st.expander("オプション設定", expanded=False):
        col1, col2 = st.columns(2)

        with col1:
            ignore_moved_labels = st.checkbox(
                "**移動しただけのラベルを差分から除外**",
                value=False,
                help="回路ブロックをまるごと別の位置に移動すると、座標単位の比較では"
                     "「削除＋追加」として検出されます。同一ラベルの削除件数と追加件数が"
                     "一致する分は、座標が異なっていても diff_labels.xlsx の変更候補から"
                     "除外し、変更なしとして扱います（差分DXFのエンティティ比較には影響しません）。"
                     "「☆」を含むラベルは対象外（常に変更候補として残ります）。"
                     "\n\n注意: 座標を見ず件数だけで判定するため、たまたま同じラベル名の部品が"
                     "別の場所で削除・別の無関係な場所に追加された場合も「移動」とみなされ、"
                     "見た目上区別できなくなります。"
            )

            st.write("")
            ignore_color_only_changes = st.checkbox(
                "**色だけが異なる図形は変更なし扱いにする**",
                value=False,
                help="座標・形状（線の始点終点、円の中心・半径、文字内容等）が完全に一致し、"
                     "色（color）だけが異なる図形要素を、差分DXFで UNCHANGED（変更なし）として"
                     "扱います。改訂箇所を色分けマーキングした図面などで、同じ図形が色の違いだけで"
                     "DELETED＋ADDEDの組として大量に検出される場合に使用します"
                     "（diff_labels.xlsx のラベル比較には影響しません）。"
                     "\n\n注意: 色の変更自体が意図的な改訂マーキングである場合、この機能を"
                     "有効にするとその色変更が差分として検出されなくなります。"
            )

            st.write("")
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
                "削除図形の色（流用元図面のみ）",
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
                step1_mode = st.session_state.step1_mode
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
                    on_error=st.error,
                    filter_non_parts=filter_non_parts,
                    validate_ref_designators=validate_ref_designators,
                    ignore_moved_labels=ignore_moved_labels,
                    ignore_color_only_changes=ignore_color_only_changes,
                    step1_mode=step1_mode,
                    total_drawings_count=compute_total_drawings_count(step1_mode),
                    source_drawing_numbers=set(st.session_state.source_files_dict.keys()),
                    dest_drawing_numbers=set(st.session_state.dest_files_dict.keys()),
                )

                # セッション状態に保存
                # diff_labels.xlsx / unchanged_labels.xlsx は zip_data の中にも同内容が
                # 含まれるため、二重に保持しない。プレビュー表示時に zip から読み出す
                # （has_* フラグのみ保持し、実体のbytesはここでは持たない）。
                st.session_state.zip_data = zip_data
                st.session_state.results = results
                st.session_state.has_diff_labels = bool(diff_labels_excel)
                st.session_state.has_unchanged_labels = bool(unchanged_labels_excel)
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
                st.error(f"エラーが発生しました: {str(e)}")
                st.error(traceback.format_exc())
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
        # diff_labels.xlsx / unchanged_labels.xlsx は zip_data 内から都度読み出す（二重保持しない）
        has_diff_labels = st.session_state.get('has_diff_labels', False)
        has_unchanged_labels = st.session_state.get('has_unchanged_labels', False)
        preview_available = has_diff_labels or has_unchanged_labels or \
                            st.session_state.master_df is not None

        if preview_available:
            st.subheader("出力内容プレビュー")

            preview_items = []
            if st.session_state.master_df is not None:
                preview_items.append("図面管理台帳")
            if has_diff_labels:
                preview_items.append("diff_labels.xlsx")
            if has_unchanged_labels:
                preview_items.append("unchanged_labels.xlsx")
            if preview_items:
                st.caption("表示可能: " + ", ".join(preview_items))

            if st.session_state.master_df is not None:
                with st.expander("図面管理台帳プレビュー", expanded=False):
                    render_preview_dataframe(st.session_state.master_df, "master_preview")

            if has_diff_labels:
                # 「一度開いたら開いたままにする」は、シート選択(selectbox)の変更という
                # 明示的なユーザー操作があった場合のみ反映する（on_change）。
                # st.expander の中身は collapsed 表示中でも毎回実行されるため、ここで
                # 無条件に True を立てると初回表示から常に展開済みになってしまう
                # （2026-06 確認済みバグ。全Typeで発生）。
                def _mark_diff_preview_expanded():
                    st.session_state['diff_preview_expanded'] = True

                diff_expanded = st.session_state.get('diff_preview_expanded', False)
                with st.expander("diff_labels.xlsx プレビュー", expanded=diff_expanded):
                    diff_bytes = read_zip_member(st.session_state.zip_data, DIFF_LABELS_FILENAME)
                    if diff_bytes:
                        diff_xl = pd.ExcelFile(BytesIO(diff_bytes))
                        sheet_name = st.selectbox(
                            "シートを選択（diff_labels）",
                            diff_xl.sheet_names,
                            key="diff_labels_preview_sheet",
                            on_change=_mark_diff_preview_expanded,
                        )
                        render_preview_dataframe(diff_xl.parse(sheet_name), "diff_preview")

            if has_unchanged_labels:
                with st.expander("unchanged_labels.xlsx プレビュー", expanded=False):
                    unchanged_bytes = read_zip_member(st.session_state.zip_data, UNCHANGED_LABELS_FILENAME)
                    if unchanged_bytes:
                        unchanged_xl = pd.ExcelFile(BytesIO(unchanged_bytes))
                        sheet_name = st.selectbox(
                            "シートを選択（unchanged_labels）",
                            unchanged_xl.sheet_names,
                            key="unchanged_labels_preview_sheet"
                        )
                        render_preview_dataframe(unchanged_xl.parse(sheet_name), "unchanged_preview")

        # ダウンロードボタン
        if successful_count > 0:
            st.subheader("Step 5: 差分抽出ファイルのダウンロード")

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
                        'has_diff_labels', 'has_unchanged_labels',
                        'diff_preview_expanded',
                        'downloaded']:
                if key in st.session_state:
                    del st.session_state[key]

            # ファイルアップロード入力をクリアするためにキーをインクリメント
            st.session_state.uploader_key += 1

            # ガベージコレクションを実行してメモリを解放
            gc.collect()

            st.rerun()


def render_step3_inactive(source_count, dest_count, pairs_available):
    """Step 4: 差分比較（ペアが未準備時のガイダンス表示）

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
        st.markdown("### ペアリング方式の選択")
        st.caption("方式によってDXFファイルのアップロード方法が変わります")
        mode = st.radio(
            "ペアリング方式を選択してください",
            options=['all_in_one', 'auto', 'pair_list'],
            format_func=lambda x: {
                'all_in_one': 'Type A: 全ファイルをまとめてアップロードし、各DXFファイルから流用元図番を抽出してペアを自動作成',
                'auto':       'Type B: 流用元と流用先とを別々にアップロードし、流用先ファイルから流用元図番を抽出してペアを自動作成',
                'pair_list':  'Type C: 全ファイルをまとめてアップロードし、ペアリストの内容でペアを作成',
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

    st.subheader("Step 4: 差分比較")
    if pairs_ready:
        render_step3_diff(complete_pairs)
    else:
        pairs_available = bool(st.session_state.pairs)
        render_step3_inactive(source_count, dest_count, pairs_available)


if __name__ == "__main__":
    app()
