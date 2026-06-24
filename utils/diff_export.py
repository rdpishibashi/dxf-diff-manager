"""
ペアリストに基づく差分DXF・ラベル差分Excel・図面管理台帳のZIP出力（UI 非依存のモデル層）。

streamlit には依存しないため、`tests/` から直接ユニットテストできる
（`utils/pairing.py` と同じ方針）。エラー通知・進捗表示は呼び出し元から渡される
コールバック（`on_error`/`progress_callback`）経由で行う。
"""
import os
import gc
import tempfile
import zipfile
from io import BytesIO
from collections import defaultdict, Counter

from utils.compare_dxf import compare_dxf_files_and_generate_dxf, count_entities_in_dxf_file, PairFileCache
from utils.extract_labels import extract_labels
from utils.label_diff import (
    compute_label_differences,
    filter_unchanged_by_prefix,
    build_diff_labels_workbook,
    build_unchanged_labels_workbook,
)
from utils.pairing import get_brand_new_drawing_pairs
from utils.master_ledger import update_parent_child_master, save_master_to_bytes
from config import diff_config

DIFF_LABELS_FILENAME = "diff_labels.xlsx"
UNCHANGED_LABELS_FILENAME = "unchanged_labels.xlsx"


def create_diff_zip(pairs, master_df=None, master_filename=None, tolerance=None,
                    deleted_color=None, added_color=None, unchanged_color=None,
                    prefixes=None, progress_callback=None, on_error=None,
                    filter_non_parts=False, validate_ref_designators=False,
                    step1_mode=None, total_drawings_count=None,
                    source_drawing_numbers=None, dest_drawing_numbers=None):
    """
    ペアリストに基づいて差分DXFファイルを作成し、ZIPアーカイブを生成

    Args:
        pairs: ペア情報のリスト
        master_df: 図面管理台帳DataFrame（Noneでない場合はZIPに含める）
        master_filename: 図面管理台帳のファイル名（Noneの場合はconfigのデフォルト名を使用）
        tolerance: 座標許容誤差（Noneの場合はconfigのデフォルト値を使用）
        deleted_color: 削除エンティティの色（Noneの場合はconfigのデフォルト値を使用）
        added_color: 追加エンティティの色（Noneの場合はconfigのデフォルト値を使用）
        unchanged_color: 変更なしエンティティの色（Noneの場合はconfigのデフォルト値を使用）
        progress_callback: (current, total, message) を受け取る進捗関数（任意）
        on_error: (message) を受け取るエラー通知関数（任意。streamlit非依存のため
            st.error() を直接呼ばず、呼び出し元から渡してもらう）
        step1_mode: ペアリング方式（Summaryシートのラベル・分母の算出、完全新規図面の
            判定に使用）
        total_drawings_count: Summaryシートの図面統計の分母件数（呼び出し側で算出）
        source_drawing_numbers/dest_drawing_numbers: 完全新規図面判定
            （get_brand_new_drawing_pairs、mode='auto'時のみ使用）に渡す図番集合

    Returns:
        tuple: (zip_data, results, diff_labels_excel, unchanged_labels_excel, master_df)
    """
    def report_error(message):
        if on_error:
            on_error(message)

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

    # 同じファイルが複数ペアの基準/比較対象として再利用される場合（RevUp/流用
    # チェーンで同じ親図面が複数の子の流用元になる等）の再解析を避けるキャッシュ。
    # offset_b は常に None（このバッチ全体で固定値）なのでキーに含めて一致させる。
    pair_cache_keys = (
        [(p['main_file_info']['temp_path'], None) for p in complete_pairs] +
        [(p['source_file_info']['temp_path'], None) for p in complete_pairs]
    )
    pair_cache = PairFileCache(pair_cache_keys)

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
                report_error(f"ラベル比較中にエラーが発生しました ({main_drawing}): {str(e)}")
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
                    offset_b=None,
                    pair_cache=pair_cache
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
                report_error(f"ペア {main_drawing} vs {source_drawing} の図面作成中にエラーが発生しました: {str(e)}")
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
                except Exception:
                    pass

            if progress_callback:
                progress_callback(index, total_pairs, f"{main_drawing} vs {source_drawing} 処理完了")

        # 図面管理台帳を結果で更新（エンティティ数を含む）
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

            # 完全新規図面（流用元の参照がない図面）のエンティティ数を算出して台帳に反映。
            # diff抽出（上記の complete_pairs ループ）の対象外のため、ここで単独ファイルの
            # エンティティ数を数えて Added=Total として登録する（2026-06 追加）。
            brand_new_pairs = get_brand_new_drawing_pairs(
                pairs, step1_mode,
                source_drawing_numbers=source_drawing_numbers,
                dest_drawing_numbers=dest_drawing_numbers,
            ) if step1_mode else []
            brand_new_with_counts = []
            for pair in brand_new_pairs:
                file_info = pair.get('main_file_info')
                if not file_info or not file_info.get('temp_path'):
                    continue  # ファイル未アップロードのため算出不可
                count = count_entities_in_dxf_file(file_info['temp_path'], tolerance=tolerance)
                if count is None:
                    continue
                pair_with_counts = dict(pair, relation='完全新規図面')
                pair_with_counts['entity_counts'] = {'added_entities': count, 'total_entities': count}
                # 方式C（pair_list）はファイル名のみで図番を識別し DXF 解析を行わない
                # （_extract_by_filename）ため、main_file_info に title/subtitle が
                # 入っていない。complete ペアは差分抽出時に extra_info から取得する
                # 一方、完全新規図面は差分抽出を行わないため、ここで個別に抽出する
                # （2026-06 追加）。方式A/Bは元々 title/subtitle 取得済みのためスキップ。
                if not pair_with_counts.get('title'):
                    try:
                        _, title_info = extract_labels(
                            file_info['temp_path'],
                            filter_non_parts=False,
                            sort_order="none",
                            debug=False,
                            selected_layers=None,
                            validate_ref_designators=False,
                            extract_drawing_numbers_option=False,
                            extract_title_option=True,
                            original_filename=file_info.get('filename'),
                        )
                        pair_with_counts['title'] = title_info.get('title')
                        pair_with_counts['subtitle'] = title_info.get('subtitle')
                    except Exception:
                        pass
                brand_new_with_counts.append(pair_with_counts)

            if brand_new_with_counts:
                master_df, _ = update_parent_child_master(master_df, brand_new_with_counts)

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
            master_excel_data = save_master_to_bytes(
                master_df, pairs=pairs, mode=step1_mode, total_drawings_count=total_drawings_count
            )
            output_master_filename = master_filename if master_filename else diff_config.MASTER_FILENAME
            zip_file.writestr(output_master_filename, master_excel_data)

    zip_buffer.seek(0)
    zip_data = zip_buffer.getvalue()

    # メモリ解放: 大きなデータ構造を削除
    del diff_label_sheets
    del unchanged_label_sheets
    gc.collect()

    return zip_data, results, diff_labels_excel, unchanged_labels_excel, master_df
