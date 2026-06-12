"""
図面ペアリングのコアロジック（UI 非依存のモデル層）。

「流用」と「RevUp」という 2 つのマッチング関係を一級概念として扱い、
方式 A（all_in_one・単一プール）/ 方式 B（auto・流用元×流用先）を
単一のコア関数 `build_pairs()` に統一する。方式 C（pair_list・明示ペア）は
`build_pairs_from_list()` で別途扱う。

streamlit には依存しないため、`tests/` から直接ユニットテストできる。

ペア dict のスキーマ（全モード共通）:
    main_drawing     : 比較先（新）図番 or None
    source_drawing   : 比較元（旧）図番 or None
    main_file_info   : 比較先ファイル情報 dict or None
    source_file_info : 比較元ファイル情報 dict or None
    status           : STATUS_* のいずれか
    relation         : RELATION_* のいずれか or None
    title / subtitle : 比較先の図面名（無ければ None）
"""
from collections import defaultdict

# --- 関係(relation) ---
RELATION_REVUP = 'RevUp'          # 同一ベース図番・リビジョン差
RELATION_DEPENDENCY = '流用'       # 流用元図番の完全一致
RELATION_PAIR_LIST = 'ペアリスト'   # 明示ペアリスト（方式 C）

# --- ステータス(status) ---
STATUS_COMPLETE = 'complete'                  # 両ファイル有・差分比較対象
STATUS_MISSING_SOURCE = 'missing_source'      # 比較元(旧)未アップロード
STATUS_MISSING_TARGET = 'missing_target'      # 比較先(新)未アップロード（C のみ）
STATUS_MISSING_BOTH = 'missing_both'          # 両方未アップロード（C のみ）
STATUS_ONE_SIDED = 'one_sided'                # 片側図番が空白（C のみ）
STATUS_IDENTICAL = 'identical'                # 比較元==比較先（比較対象外・C のみ）
STATUS_NO_SOURCE_DEFINED = 'no_source_defined'  # 流用元図番未記入


def extract_base_drawing_number(drawing_number):
    """
    図番から最後の1英大文字（Revision識別子）を除いたベース図番を抽出する。

    例: 'DE5313-008-02B' -> ('DE5313-008-02', 'B')

    Args:
        drawing_number: 図番文字列

    Returns:
        tuple: (ベース図番, Revision識別子) または (None, None)
    """
    if not drawing_number or len(drawing_number) < 2:
        return None, None

    last_char = drawing_number[-1]

    # 英大文字（半角）
    if last_char.isalpha() and last_char.isupper():
        return drawing_number[:-1], last_char

    # 全角英大文字（Ａ-Ｚ）
    if 'Ａ' <= last_char <= 'Ｚ':
        return drawing_number[:-1], last_char

    return None, None


def find_revup_pairs(source_files, target_files):
    """
    RevUpペア（Revision識別子のみ異なる同一図面のペア）を作成する。
    比較元は source_files、比較先は target_files から取り、比較先のリビジョンが
    比較元より新しいものをペアにする。

    方式 A では source_files と target_files に同一のプールを渡す。
    方式 B では流用元グループ・流用先グループをそれぞれ渡す。

    Args:
        source_files: 比較元（旧）の図番をキーとしたファイル情報の辞書
        target_files: 比較先（新）の図番をキーとしたファイル情報の辞書

    Returns:
        tuple: (RevUpペアのリスト, 使用された比較元図番のセット, 使用された比較先図番のセット)
    """
    source_base_map = defaultdict(list)
    for drawing_number in source_files.keys():
        base, revision = extract_base_drawing_number(drawing_number)
        if base and revision:
            source_base_map[base].append((drawing_number, revision))

    target_base_map = defaultdict(list)
    for drawing_number in target_files.keys():
        base, revision = extract_base_drawing_number(drawing_number)
        if base and revision:
            target_base_map[base].append((drawing_number, revision))

    revup_pairs = []
    used_source = set()
    used_target = set()

    common_bases = set(source_base_map.keys()) & set(target_base_map.keys())

    for base in common_bases:
        source_drawings = sorted(source_base_map[base], key=lambda x: x[1])
        target_drawings = sorted(target_base_map[base], key=lambda x: x[1])

        # 比較元（旧リビジョン）と比較先（新リビジョン）をマッチング
        for old_drawing, old_rev in source_drawings:
            for new_drawing, new_rev in target_drawings:
                if new_rev > old_rev and new_drawing not in used_target and old_drawing not in used_source:
                    old_file_info = source_files[old_drawing]
                    new_file_info = target_files[new_drawing]

                    revup_pairs.append({
                        'main_drawing': new_drawing,
                        'source_drawing': old_drawing,
                        'main_file_info': new_file_info,
                        'source_file_info': old_file_info,
                        'status': STATUS_COMPLETE,
                        'relation': RELATION_REVUP,
                        'title': new_file_info.get('title'),
                        'subtitle': new_file_info.get('subtitle'),
                    })
                    used_source.add(old_drawing)
                    used_target.add(new_drawing)
                    break  # この比較元は使用済み

    return revup_pairs, used_source, used_target


def build_pairs(source_files, target_files, progress_callback=None):
    """
    流用判定と RevUp 判定を独立した2パスで実行し、全ペアを生成する。

    - 方式 A（all_in_one）: build_pairs(pool, pool)（source==target）
    - 方式 B（auto）       : build_pairs(source_files, dest_files)

    判定方式:
      1. RevUpパス : find_revup_pairs() で同一ベース図番・リビジョン差のペアを
                     status=complete, relation=RevUp で生成。
      2. 流用パス  : 比較先ファイルの source_drawing_number を source_files から検索。
                     RevUp で生成済みの同一(比較先,比較元)ペアは重複させない。
                     未生成なら、対応する比較元ファイルがあれば complete、
                     なければ missing_source（relation=流用）。
      3. 孤立パス  : いずれの役割でもペアに登場せず、流用元図番も未記入（または
                     自分自身）の比較先を no_source_defined として追記。

    RevUp で対応済みの比較先でも別の流用元図番を持つ場合は独立した流用ペアを
    追加するため、同一の比較先図番が双方に登場し得る。

    Args:
        source_files: 比較元（旧）の図番をキーとしたファイル情報の辞書
        target_files: 比較先（新）の図番をキーとしたファイル情報の辞書
        progress_callback: (progress, message, count, total) を受け取る進捗関数（任意）

    Returns:
        list: ペア情報のリスト
    """
    pairs = []
    pair_keys = set()        # 重複ペア排除用 (比較先, 比較元)
    paired_drawings = set()  # いずれかの役割でペアに登場した図番（孤立判定用）

    def report_progress(progress, message, count=None, total=None):
        if progress_callback:
            progress_callback(progress, message, count, total)

    total_files = len(source_files) + len(target_files)
    report_progress(0.0, "RevUpペアを解析中...", 0, total_files)

    # 1. RevUp パス（流用判定とは独立して出力する）
    revup_pairs, _, used_target = find_revup_pairs(source_files, target_files)
    for pair in revup_pairs:
        pair_keys.add((pair['main_drawing'], pair['source_drawing']))
        paired_drawings.add(pair['main_drawing'])
        paired_drawings.add(pair['source_drawing'])
        pairs.append(pair)
    report_progress(0.3, "RevUpペアの解析が完了しました", len(used_target), total_files)

    # 2. 流用 パス
    total_targets = len(target_files)
    processed_targets = 0
    for main_drawing, file_info in target_files.items():
        source_drawing = file_info.get('source_drawing_number')

        if source_drawing and source_drawing != main_drawing:
            key = (main_drawing, source_drawing)
            if key not in pair_keys:
                source_file_info = source_files.get(source_drawing)
                pairs.append({
                    'main_drawing': main_drawing,
                    'source_drawing': source_drawing,
                    'main_file_info': file_info,
                    'source_file_info': source_file_info,
                    'status': STATUS_COMPLETE if source_file_info else STATUS_MISSING_SOURCE,
                    'relation': RELATION_DEPENDENCY,
                    'title': file_info.get('title'),
                    'subtitle': file_info.get('subtitle'),
                })
                pair_keys.add(key)
            paired_drawings.add(main_drawing)
            if source_drawing in source_files:
                paired_drawings.add(source_drawing)

        processed_targets += 1
        progress_fraction = 0.3 + 0.7 * (processed_targets / total_targets) if total_targets else 1.0
        report_progress(min(progress_fraction, 1.0), "流用ペアを作成中...", processed_targets, total_targets)

    # 3. 孤立 パス
    for main_drawing, file_info in target_files.items():
        source_drawing = file_info.get('source_drawing_number')
        if (not source_drawing or source_drawing == main_drawing) \
                and main_drawing not in paired_drawings:
            pairs.append({
                'main_drawing': main_drawing,
                'source_drawing': None,
                'main_file_info': file_info,
                'source_file_info': None,
                'status': STATUS_NO_SOURCE_DEFINED,
                'relation': None,
                'title': file_info.get('title'),
                'subtitle': file_info.get('subtitle'),
            })

    final_total = total_targets if total_targets else total_files
    report_progress(1.0, "図面ペア・リストの作成が完了しました", processed_targets, final_total)

    return pairs


def build_pairs_from_list(pair_list_df, all_files_dict):
    """
    明示ペアリスト（方式 C）からペアを作成する。

    pair_list_df の各行（比較元図番・比較先図番）について all_files_dict を参照し、
    図番の有無・ファイルの有無でステータスを決定する。RevUp の自動補完は行わず、
    リストの内容をそのまま尊重する。

    Args:
        pair_list_df: 比較元図番・比較先図番カラムを持つ DataFrame
        all_files_dict: 図番をキーとしたファイル情報の辞書

    Returns:
        list: ペア情報のリスト
    """
    pairs = []
    for _, row in pair_list_df.iterrows():
        ref_drawing = str(row['比較元図番']).strip()
        target_drawing = str(row['比較先図番']).strip()

        ref_file_info = all_files_dict.get(ref_drawing) if ref_drawing else None
        target_file_info = all_files_dict.get(target_drawing) if target_drawing else None

        if ref_drawing and target_drawing and ref_drawing == target_drawing:
            # 比較元と比較先が同一図番のため比較対象外
            status = STATUS_IDENTICAL
        elif not ref_drawing or not target_drawing:
            # 相手図番がそもそも存在しない（片側を空白にしたケース）
            status = STATUS_ONE_SIDED
        elif ref_file_info and target_file_info:
            status = STATUS_COMPLETE
        elif not ref_file_info and target_file_info:
            status = STATUS_MISSING_SOURCE
        elif ref_file_info and not target_file_info:
            status = STATUS_MISSING_TARGET
        else:
            status = STATUS_MISSING_BOTH

        pairs.append({
            'main_drawing': target_drawing,
            'source_drawing': ref_drawing,
            'main_file_info': target_file_info,
            'source_file_info': ref_file_info,
            'status': status,
            'relation': RELATION_PAIR_LIST,
            'title': None,
            'subtitle': None,
        })

    return pairs
