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

# model モジュールをインポート可能にするためのパスの追加
current_dir = os.path.dirname(os.path.abspath(__file__))
model_path = os.path.join(current_dir, 'model')
sys.path.insert(0, model_path)

from model.extract_labels import extract_labels
from model.common_utils import save_uploadedfile, cleanup_stale_temp_files, is_drawing_number_filename
from model import pairing
from model.pairing import build_pairs, build_pairs_from_list, primary_status_by_drawing
from model.master_ledger import (
    load_parent_child_master,
    update_parent_child_master,
    create_empty_master_df,
    save_master_to_bytes,
    make_dataframe_arrow_compatible,
    create_empty_drawing_list_df,
    load_drawing_list,
    parse_master_filename,
)
from model.diff_export import create_diff_zip, DIFF_LABELS_FILENAME
from model.offset_detector import OffsetDetectionConfig

# 設定をインポート
from config import ui_config, diff_config, label_filter_config, help_text

st.set_page_config(
    page_title="DXF Diff Manager",
    page_icon="📊",
    layout="wide",
)

# 図面管理台帳の新規作成時に使用する入力フォーマット
SHIBAN_PATTERN = re.compile(r'^[A-Z]{2}\d{2}-\d{4}-\d$')   # 例: AA11-1111-1
MODULE_PATTERN = re.compile(r'^[A-Z0-9]{4}$')              # 例: XXXX（英大文字・数字）
SIDE_PATTERN = re.compile(r'^[A-Z0-9]{3}$')                # 例: XXX（英大文字・数字）

# ペアリング方式（step1_mode）と、ZIPファイル名に使う "Type A/B/C" 表記の対応。
PAIRING_TYPE_LETTERS = {'all_in_one': 'A', 'auto': 'B', 'pair_list': 'C'}


def compute_default_zip_basename(master_file_name, step1_mode):
    """ZIPダウンロードファイル名（拡張子なし）のデフォルト値を組み立てる。

    指番/モジュール/サイドが master_file_name から逆算できる場合は
    "dxf_diff_results_Type{A/B/C}_{指番}_{モジュール}_{サイド}"
    （"Type{A/B/C}" は画面表示の「Type A/B/C」と一致させる）、できない場合
    （台帳を作成していない、または命名規則に一致しない台帳をアップロードした場合）は
    従来通り "dxf_diff_results" のみを返す。

    末尾のリビジョン識別子（旧: 常時付与していた "_01"）は 2026-09 に廃止した。
    同じ指番-モジュール-サイドで繰り返し実行する際に個別に出力を保存したい場合は、
    ユーザーが「ファイル名を確定」前に手動で "_01" 等を追記する運用に変更している
    （Step5 のキャプション参照）。

    指番/モジュール/サイドの逆算（"{指番}_{モジュール}_{サイド}.xlsx" の命名規則）は
    model.master_ledger.parse_master_filename() に委譲する（Package List の
    Sashiban/Module/Side 記録と同じロジックを共有するため、2026-08 に一本化）。
    """
    letter = PAIRING_TYPE_LETTERS.get(step1_mode, 'A')
    shiban, module, side = parse_master_filename(master_file_name)
    if shiban is None:
        return "dxf_diff_results"
    return f"dxf_diff_results_Type{letter}_{shiban}_{module}_{side}"


def read_zip_member(zip_data, member_name):
    """zip_data（bytes）からメンバーを読み出す。存在しない場合は None。

    diff_labels.xlsx を session_state に二重保持しないため、プレビュー表示時に
    zip_data から都度読み出す用途で使う。
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

    実体は流用判定と RevUp 判定を独立実行する `model.pairing.build_pairs`。
    流用元グループ・流用先グループに限定してペアを生成する。
    """
    return build_pairs(source_files_dict, dest_files_dict, progress_callback=progress_callback)


def load_pair_list(uploaded_file):
    """
    ペアリストファイルを読み込む（ExcelまたはCSV）

    必須カラム: 流用元図番, 流用先図番
    （旧カラム名 比較元図番/比較先図番、または Reference/Target も後方互換で受け付ける）。
    ファイル読み込み（I/O）のみを担当し、カラム名・値の正規化は
    `model.pairing.normalize_pair_list_columns()`（streamlit非依存）に委譲する。

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

    実体は明示ペアをそのまま解決する `model.pairing.build_pairs_from_list`。
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

    if 'drawing_list_df' not in st.session_state:
        st.session_state.drawing_list_df = None

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
    `model.pairing.build_pairs`（source と target に同一プールを渡す）。
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


def compute_unchanged_drawings(all_pairs, mode):
    """「変更していない図面」対象図番集合を算出する（model.pairing の薄い呼び出し）。

    実体は streamlit 非依存の `model.pairing.compute_unchanged_drawings()`。
    本関数は session_state から必要な値を取り出して渡すだけの Driver 層アダプタ。
    """
    return pairing.compute_unchanged_drawings(
        all_pairs, mode,
        source_drawing_numbers=set(st.session_state.source_files_dict.keys()),
        dest_drawing_numbers=set(st.session_state.dest_files_dict.keys()),
    )


def get_brand_new_drawing_pairs(all_pairs, mode):
    """完全新規図面のペアを算出する（model.pairing の薄い呼び出し）。

    実体は streamlit 非依存の `model.pairing.get_brand_new_drawing_pairs()`。
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
    # 「：N件」の件数は complete_pairs の実件数（＝直下の表の行数、実際に
    # 差分抽出が実行される比較回数）と一致させる（2026-08 変更。以前は
    # main_drawing のユニーク数を使っており、同じ図面が複数の流用元と比較される
    # 場合（RevUp と流用の双方で complete になる等）に表の行数より少ない件数が
    # 表示され、実データで「テーブルは5行なのに4件と表示される」と誤解を招いた
    # ため、ユーザー判断でテーブル行数優先に変更。
    # トレードオフ: 同一図面が複数関係で complete になる場合、本セクションの
    # 件数と他セクション（未アップロード・変更なし・完全新規図面）の件数を
    # 合計しても流用先総数と厳密には一致しなくなる（複数関係を持つ図面が
    # 実際の関係数ぶん重複計上されるため）。
    if complete_pairs:
        st.success(f"差分抽出が可能なペア：{len(complete_pairs)}件")

        # 表示順は流用先（新）のABC順にソートする（処理順=complete_pairsの元の
        # 順序とは無関係。実データで「流用先(新)がABC順になっていない」という
        # 指摘を受けて追加。2026-08）。complete_pairs 自体の順序（Step4の処理・
        # 戻り値）は変更しない——表示用にソート済みコピーを作るのみ。
        pair_data = []
        for pair in sorted(complete_pairs, key=lambda p: p['main_drawing'] or ''):
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
            } for pair in sorted(missing_file_pairs, key=lambda p: p['main_drawing'] or '')]

            # 件数は行数（ペアリストの行＝宣言された関係の数）で数える。one_sided は
            # main_drawing が空（複数行が同じ空値に collapse する）ため、main_drawing
            # のユニーク数では正しく数えられない。
            with st.expander(f"⚠️ 図面ファイルがない図番：{len(missing_file_pairs)}件", expanded=True):
                st.dataframe(missing_file_data, width='stretch', hide_index=True)
    else:
        # Type A/B: 流用元のDXFファイルが未アップロードのペア（流用先の図面のみが対象。
        # missing_target/missing_both は方式C専用のステータスのため常に空）。
        # RevUp と競合する流用ペアは build_pairs() の時点で生成されなくなったため
        # （2026-08-29）、ここに並ぶ流用先が同時に RevUp の complete ペアを持つ
        # ことはない（RevUpがあれば流用側はそもそも作られない）。
        if missing_pairs:
            missing_data = []
            for pair in sorted(missing_pairs, key=lambda p: p['main_drawing'] or ''):
                missing_data.append({
                    '流用先（新）': pair['main_drawing'],
                    '流用元（旧）': pair['source_drawing'],
                    '関係': pair.get('relation', 'なし'),
                    'ステータス': '⚠️ 流用元の図面ファイルなし'
                })

            with st.expander(f"⚠️ 流用元図番の図面がない図面：{len({p['main_drawing'] for p in missing_pairs})}件", expanded=False):
                st.dataframe(missing_data, width='stretch', hide_index=True)

    # one_sided（流用先が空白の行）は mode == 'pair_list' の「図面ファイルがない図番」
    # に統合済み（上記参照）。Type A/Bでは one_sided は発生しない。

    # 完全新規図面（流用元図番なし）
    if no_source_pairs:
        no_source_data = []
        for pair in sorted(no_source_pairs, key=lambda p: p['main_drawing'] or ''):
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
        if col in ("X", "Y", "Count")
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


def _is_hidden_upload_path(filename):
    """アップロードされたファイル名が不可視ファイル（`.` で始まる）かどうかを判定する。

    フォルダをまとめてドラッグ&ドロップした場合、`UploadedFile.name` に
    相対パス（例: `sub/.DS_Store`）が含まれることがあるため、`/`・`\\` で
    区切った全セグメントを見る（`.DS_Store` 本体に加え、`.git/config` の
    ような不可視フォルダ配下も併せて除外できる）。

    `.` 始まりのファイルはそもそも `is_drawing_number_filename()` の図番
    フォーマットに一致しないため、この判定を追加しても実際の読み込み結果
    （processed 件数）には影響しない——「入力N件中」の N（表示件数）の定義
    からノイズを除くためだけの判定（2026-09-16 ユーザー要求）。
    """
    segments = re.split(r'[/\\]', filename)
    return any(seg.startswith('.') for seg in segments if seg)


def process_all_uploaded_files(groups):
    """
    複数グループのアップロードDXFファイルを処理する

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
    # フォルダを丸ごとドラッグ&ドロップした場合、ブラウザがサブフォルダも含めて
    # 再帰的に全ファイルを展開してアップロードする（Streamlitの標準動作）。
    # その中から図番フォーマットに一致するファイル名の.dxfのみを対象にする。
    # 不可視ファイル（`.` 始まり、例: .DS_Store）は入力件数のカウント・
    # スキップ一覧のどちらからも除外し（ノイズのため）、それ以外の不一致ファイルは
    # 「スキップされたファイル」として一覧に記録する（エラー扱いにはしない。
    # 個別にファイルを選んでアップロードする場合も同じ判定が適用される——
    # file_uploaderにはフォルダドロップと個別選択を区別する手段が無いため）。
    all_items = []
    total_input_counts = {}   # gid -> 不可視ファイルを除いた入力件数
    skipped_by_group = {}     # gid -> 図番フォーマット不一致でスキップしたファイル名のリスト
    for g in groups:
        gid = id(g)
        total_input_counts[gid] = 0
        skipped_by_group[gid] = []
        for f in (g['uploaded_files'] or []):
            if _is_hidden_upload_path(f.name):
                continue
            total_input_counts[gid] += 1
            if is_drawing_number_filename(f.name):
                all_items.append((f, g))
            else:
                skipped_by_group[gid].append(f.name)

    if not any(g['uploaded_files'] for g in groups):
        return False

    # グループごとの集計用（一致ファイルが0件のグループも含めて全グループ分を用意する。
    # 以前は一致ファイルがあるグループだけを初期化しており、Type B(auto)で片方の
    # グループの一致が0件の場合に KeyError で落ちる不具合があった）。
    group_results = {id(g): {'processed': 0, 'failed': []} for g in groups}

    if all_items:
        total_files = len(all_items)
        start_time = time.time()
        status_placeholder = st.empty()
        with st.spinner("ファイルを処理中..."):
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
                status_placeholder.text(
                    f"{idx}/{total_files}件の図番を抽出中...（経過 {elapsed:.1f} 秒）"
                )
            status_placeholder.empty()
        elapsed_total = time.time() - start_time
    else:
        elapsed_total = 0.0

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
            'elapsed': elapsed_total,
            'total_input': total_input_counts.get(gid, res['processed'] + len(res['failed'])),
            'skipped': skipped_by_group.get(gid, []),
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
    # type= を指定しない file_uploader（図番フォーマットフィルタで絞り込む方式、
    # 2026-08）のため、入力欄に渡されるファイルはDXFに限らない（xlsx等が混ざり
    # 得る）。「DXFファイル読み込み」と表現すると入力全体がDXFであるかのように
    # 誤解されるため、「入力ファイル読み込み」とし、入力総数のうち実際に
    # DXFファイルとして読み込まれた件数を分けて示す。入力件数（total_input）は
    # 不可視ファイル（`.DS_Store` 等）を最初から除いた件数（_is_hidden_upload_path
    # 参照、2026-09-16 ユーザー要求）。
    upload_summary = st.session_state.get(summary_key)
    if upload_summary:
        processed = upload_summary.get('processed', 0)
        failed = upload_summary.get('failed', 0)
        elapsed = upload_summary.get('elapsed', 0.0)
        total_input = upload_summary.get('total_input', processed + failed)
        skipped = upload_summary.get('skipped', [])
        if processed > 0:
            st.success(
                f"直近の入力ファイル読み込み: 入力{total_input}件中、"
                f"DXFファイルとして{processed}件を読み込みました"
                f"（経過 {elapsed:.1f} 秒, 失敗 {failed}件, スキップ {len(skipped)}件）"
            )
        elif failed > 0:
            st.warning(f"直近の入力ファイル読み込みは失敗しました（経過 {elapsed:.1f} 秒）")
        elif total_input == 0:
            # 入力されたファイルが不可視ファイル（.DS_Store等）のみだった場合。
            # 「0件中0件」という無意味な表示を避け、状況を明示する。
            st.info("入力されたファイルはすべて不可視ファイルでした（処理対象なし）。")
        elif skipped:
            # 図番フォーマットに一致するファイルが1件もなかった場合
            # （processed=0・failed=0だが入力自体はある）。以前はここで何の
            # フィードバックも出さず、入力が丸ごと消えたように見えていた。
            st.warning(
                f"直近の入力ファイル読み込み: 入力{total_input}件のうち、"
                f"図番フォーマットに一致するDXFファイルが見つかりませんでした。"
            )

        if skipped:
            with st.expander(
                f"図番フォーマットに一致せずスキップした{label}ファイル（{len(skipped)}件）",
                expanded=False,
            ):
                for name in skipped:
                    st.write(f"- {name}")

    if st.session_state.get(failures_key):
        with st.expander(f"アップロードできなかった{label}ファイル", expanded=False):
            for name in st.session_state[failures_key]:
                st.write(f"- {name}")


def render_accepted_files_table(files_dict, label, show_source=False):
    """
    受理された（＝図番フォーマットに一致しファイル読み込みに成功した）DXFファイルの
    一覧を折りたたみ式テーブルで表示する。

    st.file_uploader 自体のファイル一覧（ドロップ直後のプレビュー）は、選択された
    ファイルすべて（図番フォーマットに一致しないものも含む）を表示する仕様であり、
    これを絞り込む/非表示にする公開APIが無い（内部DOM依存のCSSハックはバージョン
    アップで壊れやすいため採用しない）。そのため「実際に読み込まれたDXFファイルだけ」
    を確認する手段として、この関数が「ファイルを読み込む」実行後の正式な一覧表示を担う。

    Args:
        files_dict: 図番をキーとしたファイル情報辞書
        label: 表示ラベル（「流用元（旧）図面」等）
        show_source: True の場合、DXFから抽出した流用元図番の列も表示する
    """
    if not files_dict:
        return
    st.info(f"読み込み済み{label}: {len(files_dict)}件")
    with st.expander(f"{label}一覧を表示", expanded=False):
        rows = []
        for drawing_number, info in sorted(files_dict.items()):
            row = {'図番': drawing_number, 'ファイル名': info.get('filename', '')}
            if show_source:
                row['流用元図番（抽出）'] = info.get('source_drawing_number') or ''
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')


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
        st.session_state.drawing_list_df = None
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
            st.session_state.drawing_list_df = None
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
                st.session_state.drawing_list_df = create_empty_drawing_list_df()
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
                    master_file.seek(0)
                    drawing_list_df, dl_error = load_drawing_list(master_file)
                    if dl_error:
                        st.warning(dl_error)
                    st.session_state.drawing_list_df = drawing_list_df
                    st.success(f"記録済み親子関係（{len(master_df)}件のレコード）")
            else:
                st.info(f"既存の親子関係に追加します（{len(st.session_state.master_df)}件のレコード）")
        else:
            if st.session_state.master_df is not None:
                st.session_state.master_df = None
                st.session_state.drawing_list_df = None
                st.session_state.master_file_name = None
                st.session_state.added_relationships_count = 0

    else:  # 'none'
        st.session_state.master_df = None
        st.session_state.drawing_list_df = None
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
    st.caption(
        "ファイル名（拡張子なし）が図番として使用されます。"
        "フォルダをドラッグ&ドロップすると、サブフォルダ内も含めて図番フォーマット"
        "（例: EE1234-567-89A / EE1234-567A）に一致するDXFファイルが自動的に抽出されます。"
        "複数のフォルダを読み込む場合は、フォルダを1つずつ順番にアップロードしてください"
        "（まとめてドロップすると一部が読み込まれないことがあります）。"
    )

    source_uploaded_files = st.file_uploader(
        "DXFファイル（流用元/旧、複数可・フォルダ可・複数回可）",
        accept_multiple_files=True,
        key=f"source_upload_{st.session_state.source_upload_key}",
        help="流用元となる旧図面をアップロードしてください"
    )

    render_upload_status('source_upload_summary', 'source_upload_failures', '流用元')

    source_count = len(st.session_state.source_files_dict)
    render_accepted_files_table(st.session_state.source_files_dict, "流用元（旧）図面")

    # Step 2-2: 流用先DXFファイルのアップロード
    st.subheader("Step 2-2: 流用先（新）DXFファイルのアップロード")
    st.caption(
        "ファイル名（拡張子なし）が図番として使用され、DXFファイルの内容から流用元図番も"
        "自動抽出されます。フォルダをドラッグ&ドロップすると、サブフォルダ内も含めて"
        "図番フォーマットに一致するDXFファイルが自動的に抽出されます。"
        "複数のフォルダを読み込む場合は、フォルダを1つずつ順番にアップロードしてください"
        "（まとめてドロップすると一部が読み込まれないことがあります）。"
    )

    dest_uploaded_files = st.file_uploader(
        "DXFファイル（流用先/新、複数可・フォルダ可・複数回可）",
        accept_multiple_files=True,
        key=f"dest_upload_{st.session_state.dest_upload_key}",
        help="新しく作成した図面をアップロードしてください"
    )

    render_upload_status('dest_upload_summary', 'dest_upload_failures', '流用先')

    dest_count = len(st.session_state.dest_files_dict)
    render_accepted_files_table(st.session_state.dest_files_dict, "流用先（新）図面", show_source=True)

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
    st.caption(
        "ファイル名（拡張子なし）が図番として使用されます。流用元と流用先のファイルをまとめてアップロードしてください。"
        "フォルダをドラッグ&ドロップすると、サブフォルダ内も含めて図番フォーマット"
        "（例: EE1234-567-89A / EE1234-567A）に一致するDXFファイルが自動的に抽出されます。"
        "複数のフォルダを読み込む場合は、フォルダを1つずつ順番にアップロードしてください"
        "（まとめてドロップすると一部が読み込まれないことがあります）。"
    )

    all_uploaded_files = st.file_uploader(
        "DXFファイル（複数可・フォルダ可・複数回可）",
        accept_multiple_files=True,
        key=f"all_upload_{st.session_state.all_upload_key}",
    )

    render_upload_status('all_upload_summary', 'all_upload_failures', 'DXF')

    all_count = len(st.session_state.all_files_dict)
    render_accepted_files_table(st.session_state.all_files_dict, "DXFファイル")

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
        "ファイル名（拡張子なし）が図番として使用され、DXFから抽出した流用元図番でペアを自動作成します。\n"
        "フォルダをドラッグ&ドロップすると、サブフォルダ内も含めて図番フォーマット"
        "（例: EE1234-567-89A / EE1234-567A）に一致するDXFファイルが自動的に抽出されます。"
        "複数のフォルダを読み込む場合は、フォルダを1つずつ順番にアップロードしてください"
        "（まとめてドロップすると一部が読み込まれないことがあります）。"
    )

    all_in_one_uploaded_files = st.file_uploader(
        "DXFファイル（複数可・フォルダ可・複数回可）",
        accept_multiple_files=True,
        key=f"all_in_one_upload_{st.session_state.all_in_one_upload_key}",
    )

    render_upload_status('all_in_one_upload_summary', 'all_in_one_upload_failures', 'DXF')

    all_in_one_count = len(st.session_state.all_in_one_files_dict)
    render_accepted_files_table(st.session_state.all_in_one_files_dict, "DXFファイル", show_source=True)

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
            status_placeholder = st.empty()
            with st.spinner("図面ペア・リスト作成中..."):
                def pairing_progress(progress, message, count, total):
                    elapsed = time.time() - pairing_start
                    text = message
                    if total and count is not None:
                        text += f" {count}/{total}件"
                    text += f"（経過 {elapsed:.1f} 秒）"
                    status_placeholder.text(text)

                try:
                    st.session_state.pairs = create_pair_list(
                        st.session_state.source_files_dict,
                        st.session_state.dest_files_dict,
                        progress_callback=pairing_progress
                    )
                finally:
                    status_placeholder.empty()

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
    # オプション設定（2026-09 に config.py へ移行。UI には表示しない——
    # ユーザーが実行するのはconfig.py側の値であり、Step4画面上での確認は不要という
    # ユーザー判断。値そのものは create_diff_zip() に渡すため変数としては残す）
    ignore_moved_labels = diff_config.IGNORE_MOVED_LABELS
    ignore_color_only_changes = diff_config.IGNORE_COLOR_ONLY_CHANGES
    tolerance = diff_config.DEFAULT_TOLERANCE
    deleted_color = diff_config.DEFAULT_DELETED_COLOR
    added_color = diff_config.DEFAULT_ADDED_COLOR
    unchanged_color = diff_config.DEFAULT_UNCHANGED_COLOR
    unchanged_offset_old_color = diff_config.DEFAULT_UNCHANGED_OFFSET_OLD_COLOR
    unchanged_offset_new_color = diff_config.DEFAULT_UNCHANGED_OFFSET_NEW_COLOR
    diff_label_patterns = label_filter_config.DIFF_LABEL_PREFIX_PATTERNS

    # ラベルのみ比較オプション（2026-09-16 ユーザー要求により、この1項目のみ
    # config.py ではなく Step4 の UI に置く。他のオプション同様 config.py 化する
    # 方針からは外れるが、ユーザーの明示的な選択）。diff_labels.xlsx のラベル
    # 比較のみに影響し、差分DXF（図形のADDED/DELETED判定）には影響しない。
    label_only = st.checkbox(
        "ラベルのみで比較する（座標を無視）",
        value=False,
        key="label_only_diff",
        help=(
            "ONにすると、diff_labels.xlsx のラベル比較で座標を使わず、ラベル文字列の"
            "個数だけで新旧を比較します。回路ブロックの移動を自動的に「変更なし」扱い"
            "できますが、同一座標での「名称変更」は検出できなくなり、全ての差分が"
            "追加のみ／削除のみとして出力されます（X/Y列は常に空欄になります）。"
        ),
    )

    # オフセット補正オプション（2026-09-18新設、DXF-visual-diffから移植。
    # 「ラベルのみで比較する」と同じ理由でconfig.pyではなくStep4のUIに置く——
    # ユーザーがペアごとに有効/無効を切り替えられるようにするため。既定ON。
    # しきい値（AUTO_OFFSET_*）自体はconfig.pyで管理する）。
    offset_compensation_enabled = st.checkbox(
        "オフセット補正を行う",
        value=True,
        key="offset_compensation_diff",
        help=(
            "ONにすると、変更がなく平行移動した一定の図形グループを「変化なし」と"
            "判断します。回路ブロックがまるごと別の位置に移動した場合、座標単位の"
            "比較では「削除＋追加」として検出されますが、この機能を有効にすると"
            "自動検出したオフセット（移動量）で一致する図形を OLD_UNCHANGED_OFFSET/"
            "NEW_UNCHANGED_OFFSET レイヤーに分類し、図面管理台帳の Unchanged Offset "
            "Entities 列にも記録します（diff_labels.xlsx のラベル比較には影響しません）。"
            "検出には1ペアあたり数秒の追加時間がかかることがあります。"
        ),
    )
    offset_detection = None
    if offset_compensation_enabled:
        offset_detection = OffsetDetectionConfig(
            min_matches=diff_config.AUTO_OFFSET_MIN_MATCHES,
            min_distinct_shapes=diff_config.AUTO_OFFSET_MIN_DISTINCT_SHAPES,
            max_offsets=diff_config.AUTO_OFFSET_MAX_OFFSETS,
            max_candidates=diff_config.AUTO_OFFSET_MAX_CANDIDATES,
            max_instances_per_shape=diff_config.AUTO_OFFSET_MAX_INSTANCES_PER_SHAPE,
            compact_min_matches=diff_config.AUTO_OFFSET_COMPACT_MIN_MATCHES,
            compact_min_distinct_shapes=diff_config.AUTO_OFFSET_COMPACT_MIN_DISTINCT_SHAPES,
            compact_max_span=diff_config.AUTO_OFFSET_COMPACT_MAX_SPAN,
        )

    # 比較開始ボタン
    # 「差分抽出可能なペア：N組」は表示しない（Step3の図面ペア・リストと同内容で
    # 既に確認済みのため、2026-09 ユーザー指摘）。
    if complete_pairs:
        has_results = bool(st.session_state.get('results'))
        if st.button("差分抽出開始", key="start_comparison", type="primary", disabled=has_results):
            total_pairs = len(complete_pairs)
            status_placeholder = st.empty()
            with st.spinner("差分抽出中..."):
                def diff_progress(current, total, message):
                    status_placeholder.text(f"{message}（{current}/{total}組）")

                try:
                    step1_mode = st.session_state.step1_mode
                    zip_data, results, diff_labels_excel, updated_master, updated_drawing_list = create_diff_zip(
                        st.session_state.pairs,
                        master_df=st.session_state.master_df,
                        master_filename=st.session_state.master_file_name,
                        tolerance=tolerance,
                        deleted_color=deleted_color,
                        added_color=added_color,
                        unchanged_color=unchanged_color,
                        unchanged_offset_old_color=unchanged_offset_old_color,
                        unchanged_offset_new_color=unchanged_offset_new_color,
                        diff_label_patterns=diff_label_patterns,
                        progress_callback=diff_progress,
                        on_error=st.error,
                        ignore_moved_labels=ignore_moved_labels,
                        ignore_color_only_changes=ignore_color_only_changes,
                        step1_mode=step1_mode,
                        source_drawing_numbers=set(st.session_state.source_files_dict.keys()),
                        dest_drawing_numbers=set(st.session_state.dest_files_dict.keys()),
                        drawing_list_df=st.session_state.drawing_list_df,
                        label_only=label_only,
                        offset_detection=offset_detection,
                    )

                    # セッション状態に保存
                    # diff_labels.xlsx は zip_data の中にも同内容が含まれるため、
                    # 二重に保持しない。プレビュー表示時に zip から読み出す
                    # （has_diff_labels フラグのみ保持し、実体のbytesはここでは持たない）。
                    st.session_state.zip_data = zip_data
                    st.session_state.results = results
                    st.session_state.has_diff_labels = bool(diff_labels_excel)
                    st.session_state.processing_settings = {
                        'tolerance': tolerance,
                        'deleted_color': deleted_color,
                        'added_color': added_color,
                        'unchanged_color': unchanged_color,
                        'unchanged_offset_old_color': unchanged_offset_old_color,
                        'unchanged_offset_new_color': unchanged_offset_new_color,
                        'offset_compensation_enabled': offset_compensation_enabled,
                    }
                    if updated_master is not None:
                        st.session_state.master_df = updated_master
                    if updated_drawing_list is not None:
                        st.session_state.drawing_list_df = updated_drawing_list

                    # メモリ解放
                    gc.collect()

                except Exception as e:
                    st.error(f"エラーが発生しました: {str(e)}")
                    st.error(traceback.format_exc())
                    gc.collect()
                finally:
                    status_placeholder.empty()

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

        # 結果詳細（流用先（新）のABC順に表示。results 自体（zip内のファイル生成順等
        # には影響しない）は変更せず、表示用にソート済みで走査するのみ。2026-08）
        result_data = []
        for result in sorted(results, key=lambda r: r['main_drawing'] or ''):
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
                # オフセット一致図形数（2026-09-18新設。オフセット補正が無効、または
                # 検出0件のペアでは0または未使用のキーになるため .get() で読む。
                # 完全新規図面はentity_countsにこのキー自体が無いため'-'のまま）
                offset_entities = entity_counts.get('unchanged_offset_entities')
                row['オフセット一致図形数'] = offset_entities if offset_entities else '-'
                row['総図形数'] = entity_counts.get('total_entities', '-')
            else:
                row['削除図形数'] = '-'
                row['追加図形数'] = '-'
                row['オフセット一致図形数'] = '-'
                row['総図形数'] = '-'
            row['変更ラベル数'] = result.get('change_label_count', '-')

            row['ステータス'] = status
            result_data.append(row)

        # 削除図形数・追加図形数・総図形数・変更ラベル数は、完全新規図面の行では
        # 比較対象が無いため '-' 文字列、通常ペアの行では整数という混在object列に
        # なる（完全新規図面はentity_counts自体にdeleted_entitiesキーが無いため）。
        # 図面管理台帳のDeleted Entities等と同じ混在パターンのため、同じ
        # make_dataframe_arrow_compatible() でArrow変換エラーを予防する
        # （2026-09-16、実データ確認で発覚: pyarrowが先頭値からint型と推測し、
        # 後続の'-'で変換失敗するログが出ていた。表示のみの問題でStreamlitが
        # 自動フォールバックするため機能自体は壊れていなかったが、ログを汚していた）。
        st.dataframe(
            make_dataframe_arrow_compatible(pd.DataFrame(result_data)),
            width='stretch', hide_index=True,
        )

        # 検出されたオフセットの一覧（2026-09-18新設、DXF-visual-diffのUIパターンを
        # 移植。オフセット補正が有効で、かつ1件以上検出されたペアがある場合のみ表示）
        pairs_with_offsets = [
            r for r in sorted(results, key=lambda r: r['main_drawing'] or '')
            if r.get('entity_counts') and r['entity_counts'].get('detected_offsets')
        ]
        if pairs_with_offsets:
            with st.expander(
                f"🔍 検出されたオフセット（{len(pairs_with_offsets)}ペア）", expanded=False
            ):
                for result in pairs_with_offsets:
                    entity_counts = result['entity_counts']
                    detected_offsets = entity_counts['detected_offsets']
                    rejected_count = entity_counts.get('rejected_offset_candidates', 0)
                    st.caption(f"**{result['main_drawing']} vs {result['source_drawing']}**（{len(detected_offsets)}個）")
                    for d in detected_offsets:
                        dx, dy = d['offset']
                        compact_note = "（コンパクト救済）" if d.get('compact') else ""
                        st.caption(
                            f"　({dx:.2f}, {dy:.2f}) ｜ 一致: {d['matches']}件 ｜ "
                            f"形状の種類: {d['shapes']}種類 ｜ "
                            f"広がり: {d.get('span', 0.0):.1f}{compact_note}"
                        )
                    if rejected_count > 0:
                        st.caption(f"　※ しきい値未満で不採用の候補: {rejected_count}個")

        # プレビューセクション
        # diff_labels.xlsx は zip_data 内から都度読み出す（二重保持しない）
        has_diff_labels = st.session_state.get('has_diff_labels', False)
        preview_available = has_diff_labels or st.session_state.master_df is not None

        if preview_available:
            st.subheader("出力内容プレビュー")

            preview_items = []
            if st.session_state.master_df is not None:
                preview_items.append("図面管理台帳")
            if has_diff_labels:
                preview_items.append("diff_labels.xlsx")
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

        # ダウンロードボタン
        if successful_count > 0:
            st.subheader("Step 5: 差分抽出ファイルのダウンロード")

            # ZIPファイル名（拡張子なしの基本名を編集可能にする。台帳モード・
            # ペアリング方式が変わったら初期値を再計算するが、ユーザーが一度でも
            # 自動生成値から編集した（＝入力欄の値が前回表示した自動生成値と異なる）
            # 場合は、以後シグネチャが変わっても上書きしない
            # （レビジョン等の手入力が rerun のたびに消えてしまう不具合の対策）。
            # 仕様（2026-08 確定）:
            #   - Step 5 に新しく入った時点（初回、またはデフォルト値が変わった時点）では、
            #     ファイル名がデフォルトのままであっても常に未確定の状態から始まる。
            #     「ファイル名を確定」ボタンを押すまで「ZIPでダウンロード」は表示しない。
            #   - 確定はボタンクリックでのみ行う。テキスト入力欄でのEnter/blurは
            #     確定として扱わない（Enterでも確定できてしまうと、ユーザーが確定した
            #     つもりがない状態でも古い/新しい値が紛れやすくなるため、確定手段を
            #     ボタン1つに一本化する——2026-08 ユーザー確認）。
            #   - 確定後にファイル名を再度編集すると、再び未確定の状態に戻り、
            #     ダウンロードするには改めてボタンを押す必要がある。
            # zip_basename_confirmed は「未確定」を表す番人として None を使う
            # （確定後は文字列になる）。
            default_zip_basename = compute_default_zip_basename(
                st.session_state.master_file_name, st.session_state.step1_mode
            )
            not_yet_initialized = 'zip_basename_input' not in st.session_state
            not_user_edited = st.session_state.get('zip_basename_input') == st.session_state.get('zip_basename_last_default')
            if not_yet_initialized or (not_user_edited and st.session_state.get('zip_basename_input') != default_zip_basename):
                st.session_state.zip_basename_input = default_zip_basename
                st.session_state.zip_basename_confirmed = None  # 未確定から開始
            st.session_state.zip_basename_last_default = default_zip_basename
            if 'zip_basename_confirmed' not in st.session_state:
                st.session_state.zip_basename_confirmed = None

            st.text_input(
                "ダウンロードするZIPファイル名（拡張子なし）",
                key='zip_basename_input',
            )

            st.caption(
                "ファイル名を変更してもしなくても、ファイル名を確定 ボタンを押してください。"
                "同じ 指番-モジュール-サイド で繰り返し実行する際に、"
                "個別に出力を保存しておきたい場合は、"
                '"_01" などの識別子をファイル名の最後に追加してください。'
            )

            # st.download_button はクリック時にサーバーへ再接続してファイル名を
            # 再計算しない——直前のrerunでレンダリングされた内容をブラウザが
            # そのままダウンロードするだけの仕組みのため、テキスト入力欄を編集した
            # 直後（Enter/blurで確定する前）にこのボタンを押すと、編集前の
            # ファイル名（既定の「..._01」）でダウンロードされてしまう不具合が
            # あった（2026-08 ユーザー報告）。「ファイル名を確定」ボタンを唯一の
            # 確定手段にすることで解消。通常の st.button はクリックのたびにその
            # 時点の全ウィジェットの値（未確定の編集中テキストも含む）を携えて
            # rerun するため、このボタンを経由させれば、その時点の最新入力値を
            # zip_basename_confirmed に確実に反映できる。st.download_button には
            # 常にこの確定済みの値だけを渡す。
            #
            # ボタン表示（streamlitスキル§11の動的ボタン色分けパターン。2026-08）:
            # 未確定の間（zip_basename_confirmed が None、または入力欄の現在値と
            # 一致しない間）は「ファイル名を確定」のみ青色（primary）で表示し、
            # 「ZIPでダウンロード」は非表示にする——誤って古い名前・未確定のまま
            # ダウンロードできてしまう余地を無くすため、グレーアウトではなく非表示を
            # 選んだ。確定済みになったら逆に「ファイル名を確定」を非表示にし、
            # 「ZIPでダウンロード」を青色で表示する。
            needs_confirmation = (
                st.session_state.zip_basename_confirmed is None
                or (st.session_state.zip_basename_input or '') != st.session_state.zip_basename_confirmed
            )

            if needs_confirmation:
                if st.button("ファイル名を確定", key="confirm_zip_basename", type="primary"):
                    st.session_state.zip_basename_confirmed = (
                        (st.session_state.zip_basename_input or '').strip() or default_zip_basename
                    )
                    st.rerun()  # ボタン表示をこの場で「ZIPでダウンロード」側に切り替えるため
                st.caption("「ファイル名を確定」を押すと、上記の内容でダウンロードできるようになります。")
            else:
                downloaded = st.session_state.get('downloaded', False)
                st.download_button(
                    label="ZIPでダウンロード",
                    data=st.session_state.zip_data,
                    file_name=f"{st.session_state.zip_basename_confirmed}.zip",
                    mime="application/zip",
                    key="download_zip",
                    type="primary",
                    disabled=downloaded,
                    on_click=lambda: st.session_state.update({'downloaded': True})
                )
                st.caption(f"ダウンロードされるファイル名: **{st.session_state.zip_basename_confirmed}.zip**")

            # オプション設定の情報を表示
            offset_note = (
                "有効（変更がなく平行移動した図形グループを「変化なし」と判定）"
                if settings.get('offset_compensation_enabled') else "無効"
            )
            st.info(f"""
                **生成されたファイルについて（7レイヤー構成）：**
                開いた直後は「NEW_ALL」「OLD_ALL」の2枚だけが表示され、詳細カテゴリ層
                （NEW_ADDED/OLD_DELETED/UNCHANGED/OLD_UNCHANGED_OFFSET/
                NEW_UNCHANGED_OFFSET）は既定で非表示です。必要に応じて手動でONにしてください。
                - NEW_ADDED: 流用先図面にのみ存在する要素（追加された図形。完全新規図面は全要素）
                - OLD_DELETED: 流用元図面にのみ存在する要素（削除された図形）
                - UNCHANGED: 両方の図面に存在し変更がない図形
                - OLD_UNCHANGED_OFFSET / NEW_UNCHANGED_OFFSET: オフセット補正で一致した図形
                （それぞれ流用元・流用先の座標で描画。検出内容は「🔍 検出されたオフセット」から確認できます）
                - OLD_ALL / NEW_ALL: 上記のうちそれぞれの図面の再現に必要なものを1枚に複製した合成レイヤー
                - diff_labels.xlsx: 各図面の変更ラベル一覧（シート名は新図面の図番）
                - 座標許容誤差: {settings.get('tolerance', 0.01)}
                - オフセット補正: {offset_note}
                """)

        # 新しい比較を開始するボタン。
        # ZIPダウンロード完了（st.session_state.downloaded）後は青色（primary）にし、
        # 「次に取るべき操作」であることを示す（streamlitスキル§11の動的ボタン色分け
        # パターン。2026-09 ユーザー指摘: ダウンロード後にこのボタンが白いままだった）。
        restart_button_type = "primary" if st.session_state.get('downloaded', False) else "secondary"
        if st.button("🔄 新しい差分抽出を開始", key="restart_button", type=restart_button_type):
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
                        'master_df', 'drawing_list_df', 'master_file_name', 'added_relationships_count',
                        'has_diff_labels',
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

    # 日本語の文字だけを英数字より小さく（94%）表示する。
    # @font-face の unicode-range で日本語グリフ範囲だけ別フォント定義にし、
    # size-adjust で縮小率を指定する（文字単位で自動的に使い分けられるため、
    # ウィジェットごとのフォントサイズ指定は不要）。st.dataframe/Plotly の
    # Canvas 描画部分には効かない場合がある。
    st.markdown("""
        <style>
        /* 日本語グリフ専用のフォント定義: 94%縮小（ユーザー調整済み） */
        @font-face {
            font-family: "AppMixedFont";
            src: local("Hiragino Kaku Gothic ProN"), local("Yu Gothic UI"),
                 local("Yu Gothic"), local("Meiryo");
            unicode-range: U+3000-303F,  /* 句読点・記号 */
                           U+3040-30FF,  /* ひらがな・カタカナ */
                           U+FF00-FFEF,  /* 全角英数・半角カナ */
                           U+4E00-9FFF, U+3400-4DBF;  /* 漢字 */
            size-adjust: 94%;
        }
        /* 上記範囲外（英数字）は通常サイズのフォントにフォールバック */
        @font-face {
            font-family: "AppMixedFont";
            src: local("Source Sans Pro"), local("Helvetica Neue"), local("Arial");
        }
        .stApp, .stApp p, .stApp li, .stApp label, .stApp td, .stApp th,
        .stApp h1, .stApp h2, .stApp h3, .stApp input, .stApp button {
            font-family: "AppMixedFont", sans-serif !important;
        }
        </style>
    """, unsafe_allow_html=True)

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
