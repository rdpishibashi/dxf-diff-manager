"""
ペアリストに基づく差分DXF・ラベル差分Excel・図面管理台帳のZIP出力（UI 非依存のモデル層）。

streamlit には依存しないため、`tests/` から直接ユニットテストできる
（`model/pairing.py` と同じ方針）。エラー通知・進捗表示は呼び出し元から渡される
コールバック（`on_error`/`progress_callback`）経由で行う。
"""
import os
import gc
import tempfile
import zipfile
from io import BytesIO

from .compare_dxf import (
    compare_dxf_files_and_generate_dxf, generate_all_added_dxf, PairFileCache,
)
from .extract_labels import extract_labels, get_title_and_subtitle
from .label_diff import (
    compute_label_differences,
    filter_change_rows_by_patterns,
    round_labels_with_coordinates,
    build_diff_labels_workbook,
)
from .pairing import get_brand_new_drawing_pairs
from .master_ledger import (
    update_parent_child_master, save_master_to_bytes,
    update_drawing_list, parse_master_filename,
)
from config import diff_config, label_filter_config

DIFF_LABELS_FILENAME = "diff_labels.xlsx"


def create_diff_zip(pairs, master_df=None, master_filename=None, tolerance=None,
                    deleted_color=None, added_color=None, unchanged_color=None,
                    diff_label_patterns=None, progress_callback=None, on_error=None,
                    ignore_moved_labels=False, ignore_color_only_changes=False,
                    step1_mode=None,
                    source_drawing_numbers=None, dest_drawing_numbers=None,
                    drawing_list_df=None):
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
        diff_label_patterns: diff_labels.xlsx の差分行を絞り込む先頭一致の正規表現リスト
            （Noneの場合は config.label_filter_config.DIFF_LABEL_PREFIX_PATTERNS を使用。
            filter_change_rows_by_patterns 参照。空リストなら絞り込みなし）
        progress_callback: (current, total, message) を受け取る進捗関数（任意）
        on_error: (message) を受け取るエラー通知関数（任意。streamlit非依存のため
            st.error() を直接呼ばず、呼び出し元から渡してもらう）
        ignore_moved_labels: True の場合、diff_labels.xlsx で同一ラベルの削除件数・
            追加件数が一致する分を「移動しただけ」とみなし変更候補から除外する
            （compute_label_differences 参照。差分DXFのエンティティ比較には影響しない）
        ignore_color_only_changes: True の場合、差分DXFで座標・形状が一致し color
            だけが異なるエンティティを UNCHANGED として扱う
            （compare_dxf_files_and_generate_dxf/generate_all_added_dxf 参照。
            diff_labels.xlsx のラベル比較には影響しない）
        step1_mode: ペアリング方式（完全新規図面の判定に使用）
        source_drawing_numbers/dest_drawing_numbers: 完全新規図面判定
            （get_brand_new_drawing_pairs、mode='auto'時のみ使用）に渡す図番集合
        drawing_list_df: Drawing List DataFrame（Noneの場合は空から開始）。
            master_df と異なり、既存の Child Drawing Number は上書きされず、
            新規の Child Drawing Number のみが追加される（update_drawing_list 参照）

    Returns:
        tuple: (zip_data, results, diff_labels_excel, master_df, drawing_list_df)
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
    if diff_label_patterns is None:
        diff_label_patterns = label_filter_config.DIFF_LABEL_PREFIX_PATTERNS

    results = []
    diff_label_sheets = []
    summary_data = []
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
            change_label_count = 0

            extra_info = {'labels_new': [], 'invalid_ref_designators': []}
            try:
                change_rows, unchanged_entries, extra_info = compute_label_differences(
                    main_file_path,
                    source_file_path,
                    tolerance=tolerance,
                    label_cache=label_cache,
                    ignore_moved_labels=ignore_moved_labels,
                    new_file_original_name=pair.get('main_file_info', {}).get('filename'),
                )
                change_rows = filter_change_rows_by_patterns(change_rows, diff_label_patterns)
                change_label_count = len(change_rows)
            except Exception as e:
                report_error(f"ラベル比較中にエラーが発生しました ({main_drawing}): {str(e)}")
                change_rows = []

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

            diff_label_sheets.append({
                'sheet_name': main_drawing,
                'rows': change_rows,
                'old_label_name': f"Old: {source_drawing}",
                'new_label_name': f"New: {main_drawing}"
            })

            try:
                if progress_callback:
                    progress_callback(index - 1, total_pairs, f"{main_drawing} vs {source_drawing} 処理中")

                # DXF比較処理。compare_dxf_files_and_generate_dxf() は file_a のみに
                # 存在するエンティティを DELETED、file_b のみに存在するエンティティを
                # ADDED として出力する（標準的な diff の慣習: file_a=旧基準、file_b=新
                # 比較対象）。そのため流用元図番（旧）を file_a、図番（新）を file_b に
                # 渡す（2026-07 修正: 以前は新旧が逆で ADDED/DELETED レイヤーの内容が
                # 入れ替わっていた不具合があった）。
                success, entity_counts = compare_dxf_files_and_generate_dxf(
                    source_file_path,      # 基準ファイルA (旧) → DELETED の判定基準
                    main_file_path,        # 比較対象ファイルB (新) → ADDED の判定基準
                    temp_output,
                    tolerance=tolerance,
                    deleted_color=deleted_color,
                    added_color=added_color,
                    unchanged_color=unchanged_color,
                    offset_b=None,
                    pair_cache=pair_cache,
                    ignore_color_only_changes=ignore_color_only_changes,
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
                        'change_label_count': change_label_count
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
                        'change_label_count': change_label_count
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
                    'change_label_count': change_label_count
                })
            finally:
                try:
                    os.unlink(temp_output)
                except Exception:
                    pass

            if progress_callback:
                progress_callback(index, total_pairs, f"{main_drawing} vs {source_drawing} 処理完了")

        # 完全新規図面（流用元の参照がない図面）のDXFファイル（全要素ADDED）を出力する。
        # diff抽出（上記の complete_pairs ループ）の対象外のため、ここで単独ファイルから
        # 生成する。図面管理台帳の作成有無に関わらず出力する（2026-09 変更。以前は
        # master_df is not None のブロック内でのみエンティティ数を算出していたため、
        # 「台帳を作成しない」を選んだ場合にDXFが出力されなかった）。
        # エンティティ数は generate_all_added_dxf() が返す値をそのまま使う
        # （count_entities_in_dxf_file() と同じ抽出経路・重複排除のため、台帳の
        # Added/Total Entities の値は変わらない）。
        brand_new_pairs = get_brand_new_drawing_pairs(
            pairs, step1_mode,
            source_drawing_numbers=source_drawing_numbers,
            dest_drawing_numbers=dest_drawing_numbers,
        ) if step1_mode else []
        brand_new_with_counts = []
        for pair in brand_new_pairs:
            main_drawing = pair.get('main_drawing')
            file_info = pair.get('main_file_info')
            if not file_info or not file_info.get('temp_path'):
                continue  # ファイル未アップロードのため算出不可

            output_filename = f"{main_drawing}_vs_none.dxf"
            temp_output = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf").name
            try:
                success, count = generate_all_added_dxf(
                    file_info['temp_path'], temp_output,
                    tolerance=tolerance,
                    deleted_color=deleted_color,
                    added_color=added_color,
                    unchanged_color=unchanged_color,
                    ignore_color_only_changes=ignore_color_only_changes,
                )
                if not success or count is None:
                    continue
                zip_file.write(temp_output, arcname=output_filename)
            except Exception as e:
                report_error(f"完全新規図面のDXF作成中にエラーが発生しました ({main_drawing}): {str(e)}")
                continue
            finally:
                try:
                    os.unlink(temp_output)
                except Exception:
                    pass

            results.append({
                'pair_name': f"{main_drawing} vs none",
                'main_drawing': main_drawing,
                'source_drawing': 'none',
                'output_filename': output_filename,
                'success': True,
                'entity_counts': {'added_entities': count, 'total_entities': count},
                'relation': '完全新規図面',
            })

            pair_with_counts = dict(pair, relation='完全新規図面')
            pair_with_counts['entity_counts'] = {'added_entities': count, 'total_entities': count}

            # ラベル一覧とタイトル/サブタイトルをまとめて抽出する（1回のDXF解析で
            # 両方まかなう。get_title_and_subtitle() 単独呼び出しと比べ二重解析を
            # 避けられる）。完全新規図面は比較対象が無いため、diff_labels.xlsx には
            # New側のみのラベル一覧をシートとして出力する（2026-09 追加。Old側は
            # 常に空になる仕様）。
            try:
                labels, info_new = extract_labels(
                    file_info['temp_path'],
                    include_coordinates=True,
                    extract_title_option=True,
                    extract_drawing_numbers_option=True,
                    original_filename=file_info.get('filename'),
                )
            except Exception:
                labels, info_new = [], {}

            # 方式C（pair_list）はファイル名のみで図番を識別し DXF 解析を行わない
            # （_extract_by_filename）ため、main_file_info に title/subtitle が
            # 入っていない。complete ペアは差分抽出時に extra_info から取得する
            # 一方、完全新規図面は差分抽出を行わないため、ここで個別に抽出する
            # （2026-06 追加）。方式A/Bは元々 title/subtitle 取得済みのためスキップ。
            if not pair_with_counts.get('title'):
                pair_with_counts['title'] = info_new.get('title')
                pair_with_counts['subtitle'] = info_new.get('subtitle')

            resolved_title = pair_with_counts.get('title')
            resolved_subtitle = pair_with_counts.get('subtitle')
            pair_extracted_info[main_drawing] = {'title': resolved_title, 'subtitle': resolved_subtitle}

            rounded_labels = round_labels_with_coordinates(labels, tolerance)
            brand_new_change_rows = [
                {'X': x, 'Y': y, 'Old Label': None, 'New Label': label}
                for label, x, y in rounded_labels
            ]
            brand_new_change_rows = filter_change_rows_by_patterns(brand_new_change_rows, diff_label_patterns)
            brand_new_change_rows.sort(key=lambda r: r['New Label'] or '')

            diff_label_sheets.append({
                'sheet_name': main_drawing,
                'rows': brand_new_change_rows,
                'old_label_name': 'Old: none',
                'new_label_name': f'New: {main_drawing}',
            })
            summary_data.append({
                '図番': main_drawing,
                '流用元図番': 'none',
                '追加ラベル数': len(brand_new_change_rows),
                '削除ラベル数': 0,
                '変更ラベル数': 0,
                'タイトル': resolved_title,
                'サブタイトル': resolved_subtitle,
            })

            brand_new_with_counts.append(pair_with_counts)

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

            if brand_new_with_counts:
                master_df, _ = update_parent_child_master(master_df, brand_new_with_counts)

            # Drawing List を更新（Child Drawing Number でユニーク、新規のみ追加）。
            # Master/master_df とは異なり、対象は「差分抽出が成功したペア」に限らず
            # pairs 全件（Type A: プール内の全ファイル、Type B: 流用先のすべて、
            # Type C: ペアリストの全行）——build_pairs()/build_pairs_from_list() は
            # いずれもステータスを問わず main_drawing を1件も欠かさず含むため、
            # pairs をそのまま走査すれば各方式の「Step2でアップロードした対象すべて」
            # を過不足なくカバーできる（model/pairing.py 参照）。
            shiban, module, side = parse_master_filename(master_filename)
            drawing_list_entries = []
            seen_children = set()
            for pair in pairs:
                child = pair.get('main_drawing')
                if not child or child in seen_children:
                    continue
                seen_children.add(child)

                extracted = pair_extracted_info.get(child)
                if extracted:
                    title = extracted.get('title')
                    subtitle = extracted.get('subtitle')
                else:
                    title = pair.get('title')
                    subtitle = pair.get('subtitle')
                    file_info = pair.get('main_file_info')
                    # complete 以外（missing_source等）やdiff未実行のファイルは
                    # title/subtitle 未抽出のことが多いため、実ファイルがあれば
                    # ここで個別に抽出を試みる（完全新規図面と同じフォールバック）。
                    if not title and file_info and file_info.get('temp_path'):
                        try:
                            title, subtitle = get_title_and_subtitle(
                                file_info['temp_path'],
                                original_filename=file_info.get('filename'),
                            )
                        except Exception:
                            pass

                drawing_list_entries.append({
                    'main_drawing': child,
                    'source_drawing': pair.get('source_drawing'),
                    'title': title,
                    'subtitle': subtitle,
                })

            if drawing_list_entries:
                drawing_list_df, _ = update_drawing_list(
                    drawing_list_df, drawing_list_entries, shiban, module, side
                )

        # Summary シートの「図番」欄・ペアシートの並び順を図番のABC順にする。
        # summary_data と diff_label_sheets は上のループで1ペアにつき1件ずつ同じ順序で
        # 追加されているため（同一図番が複数ペアに登場する場合は元の順序を保つ = 安定ソート）、
        # インデックスベースで両方を同じ並びに揃える。
        if summary_data:
            sort_order = sorted(range(len(summary_data)), key=lambda i: summary_data[i].get('図番') or '')
            summary_data = [summary_data[i] for i in sort_order]
            diff_label_sheets = [diff_label_sheets[i] for i in sort_order]

        diff_labels_excel = build_diff_labels_workbook(
            diff_label_sheets,
            summary_data=summary_data if summary_data else None,
        )

        if diff_labels_excel:
            zip_file.writestr(DIFF_LABELS_FILENAME, diff_labels_excel)

        if master_df is not None:
            master_excel_data = save_master_to_bytes(
                master_df, mode=step1_mode,
                drawing_list_df=drawing_list_df,
            )
            output_master_filename = master_filename if master_filename else diff_config.MASTER_FILENAME
            zip_file.writestr(output_master_filename, master_excel_data)

    zip_buffer.seek(0)
    zip_data = zip_buffer.getvalue()

    # メモリ解放: 大きなデータ構造を削除
    del diff_label_sheets
    gc.collect()

    return zip_data, results, diff_labels_excel, master_df, drawing_list_df
