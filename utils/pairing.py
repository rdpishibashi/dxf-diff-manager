"""
図面ペアリングのコアロジック（UI 非依存のモデル層）。

「流用」と「RevUp」という 2 つのマッチング関係を一級概念として扱い、
方式 A（all_in_one・単一プール）/ 方式 B（auto・流用元×流用先）を
単一のコア関数 `build_pairs()` に統一する。方式 C（pair_list・明示ペア）は
`build_pairs_from_list()` で別途扱う。

streamlit には依存しないため、`tests/` から直接ユニットテストできる。

ペア dict のスキーマ（全モード共通）:
    main_drawing     : 流用先（新）図番 or None
    source_drawing   : 流用元（旧）図番 or None
    main_file_info   : 流用先ファイル情報 dict or None
    source_file_info : 流用元ファイル情報 dict or None
    status           : STATUS_* のいずれか
    relation         : RELATION_* のいずれか or None
    title / subtitle : 流用先の図面名（無ければ None）
"""
from collections import defaultdict

# --- 関係(relation) ---
RELATION_REVUP = 'RevUp'          # 同一ベース図番・リビジョン差
RELATION_DEPENDENCY = '流用'       # 流用元図番の完全一致
RELATION_PAIR_LIST = 'ペアリスト'   # 明示ペアリスト（方式 C）

# --- ステータス(status) ---
STATUS_COMPLETE = 'complete'                  # 両ファイル有・差分比較対象
STATUS_MISSING_SOURCE = 'missing_source'      # 流用元(旧)未アップロード
STATUS_MISSING_TARGET = 'missing_target'      # 流用先(新)未アップロード（C のみ）
STATUS_MISSING_BOTH = 'missing_both'          # 両方未アップロード（C のみ）
STATUS_ONE_SIDED = 'one_sided'                # 片側図番が空白（C のみ）
STATUS_IDENTICAL = 'identical'                # 流用元==流用先（比較対象外・C のみ）
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
    流用元は source_files、流用先は target_files から取り、流用先のリビジョンが
    流用元より新しいものをペアにする。

    方式 A では source_files と target_files に同一のプールを渡す。
    方式 B では流用元グループ・流用先グループをそれぞれ渡す。

    Args:
        source_files: 流用元（旧）の図番をキーとしたファイル情報の辞書
        target_files: 流用先（新）の図番をキーとしたファイル情報の辞書

    Returns:
        tuple: (RevUpペアのリスト, 使用された流用元図番のセット, 使用された流用先図番のセット)
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

        # 流用元（旧リビジョン）と流用先（新リビジョン）をマッチング
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
                    break  # この流用元は使用済み

    return revup_pairs, used_source, used_target


def build_pairs(source_files, target_files, progress_callback=None):
    """
    流用判定と RevUp 判定を独立した2パスで実行し、全ペアを生成する。

    - 方式 A（all_in_one）: build_pairs(pool, pool)（source==target）
    - 方式 B（auto）       : build_pairs(source_files, dest_files)

    判定方式:
      1. RevUpパス : find_revup_pairs() で同一ベース図番・リビジョン差のペアを
                     status=complete, relation=RevUp で生成。
      2. 流用パス  : 流用先ファイルの source_drawing_number を source_files から検索。
                     RevUp で生成済みの同一(流用先,流用元)ペアは重複させない。
                     未生成なら、対応する流用元ファイルがあれば complete、
                     なければ missing_source（relation=流用）。
      3. 孤立パス  : いずれの役割でもペアに登場せず、流用元図番も未記入（または
                     自分自身）の流用先を no_source_defined として追記。

    RevUp で対応済みの流用先でも別の流用元図番を持つ場合は独立した流用ペアを
    追加するため、同一の流用先図番が双方に登場し得る。

    Args:
        source_files: 流用元（旧）の図番をキーとしたファイル情報の辞書
        target_files: 流用先（新）の図番をキーとしたファイル情報の辞書
        progress_callback: (progress, message, count, total) を受け取る進捗関数（任意）

    Returns:
        list: ペア情報のリスト
    """
    pairs = []
    pair_keys = set()        # 重複ペア排除用 (流用先, 流用元)
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

    pair_list_df の各行（流用元図番・流用先図番）について all_files_dict を参照し、
    図番の有無・ファイルの有無でステータスを決定する。RevUp の自動補完は行わず、
    リストの内容をそのまま尊重する。

    Args:
        pair_list_df: 流用元図番・流用先図番カラムを持つ DataFrame
        all_files_dict: 図番をキーとしたファイル情報の辞書

    Returns:
        list: ペア情報のリスト
    """
    pairs = []
    for _, row in pair_list_df.iterrows():
        ref_drawing = str(row['流用元図番']).strip()
        target_drawing = str(row['流用先図番']).strip()

        ref_file_info = all_files_dict.get(ref_drawing) if ref_drawing else None
        target_file_info = all_files_dict.get(target_drawing) if target_drawing else None

        if ref_drawing and target_drawing and ref_drawing == target_drawing:
            # 流用元と流用先が同一図番のため比較対象外
            status = STATUS_IDENTICAL
        elif not target_drawing:
            # 流用先図番が空白（比較対象の新図面が存在しない行）
            status = STATUS_ONE_SIDED
        elif not ref_drawing:
            # 流用先図番はあるが流用元図番が空白 → 完全新規図面（流用元の参照なし）。
            # 方式A/B（孤立パス）の no_source_defined と同じ意味合いで扱う。
            status = STATUS_NO_SOURCE_DEFINED
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
            # 流用先（main_drawing）のファイルから抽出済みのタイトルを使う。
            # complete ペアは差分抽出時に extra_info から再取得して上書きするため
            # 影響しないが、完全新規図面（no_source_defined）は差分抽出を行わない
            # ため、ここで設定しないと台帳の Title/Subtitle が常に空欄になる
            # （2026-06 修正）。
            'title': target_file_info.get('title') if target_file_info else None,
            'subtitle': target_file_info.get('subtitle') if target_file_info else None,
        })

    return pairs


# 同一の流用先図番（main_drawing）が複数ステータスのペアに登場した場合、
# どのステータスを「その図面の代表的な状態」として優先するかの順位（先頭ほど優先）。
#
# 同じ main_drawing が複数のペアを持ち得るケース（2026-06 確認済み）:
#   - 方式 A/B（build_pairs）: RevUp パスと流用パスが、同一の比較先に対して
#     異なる流用元図番でそれぞれ別のペアを生成する場合（例: RevUpで complete、
#     その図面自身が埋め込む別の流用元参照が未アップロードで missing_source）。
#   - 方式 C（build_pairs_from_list）: ペアリストに同一の流用先図番が複数行
#     記載されている場合（流用元図番や行内容が行ごとに異なる）。
# これを考慮せず単純にステータス別の件数を表示すると、同じ図面が複数セクションに
# 二重計上され、セクション件数の合計が流用先総数と一致しなくなる（2026-06 に
# 実データで確認したバグ）。complete を最優先することで、「別の流用元に対しては
# 解決済みの図面」が missing_source 等にも二重計上されることを防ぐ。
STATUS_DISPLAY_PRIORITY = [
    STATUS_COMPLETE, STATUS_MISSING_SOURCE, STATUS_MISSING_TARGET, STATUS_MISSING_BOTH,
    STATUS_ONE_SIDED, STATUS_IDENTICAL, STATUS_NO_SOURCE_DEFINED,
]
_STATUS_DISPLAY_RANK = {s: i for i, s in enumerate(STATUS_DISPLAY_PRIORITY)}


def primary_status_by_drawing(pairs):
    """main_drawing ごとに、STATUS_DISPLAY_PRIORITY 上で最も優先度の高いステータスを1つ決める。

    UI 表示（セクション分類・件数集計）を main_drawing 単位で排他的にするための
    前処理。main_drawing が空（片側のみのペアで流用先が空白の行等）は対象外。

    Returns:
        dict: {main_drawing: status}
    """
    primary = {}
    for p in pairs:
        md = p.get('main_drawing')
        if not md:
            continue
        cur = primary.get(md)
        if cur is None or _STATUS_DISPLAY_RANK[p['status']] < _STATUS_DISPLAY_RANK[cur]:
            primary[md] = p['status']
    return primary


def drawings_with_status(pairs, status):
    """primary_status_by_drawing() の結果から、指定ステータスが最優先の main_drawing 集合を返す。"""
    primary = primary_status_by_drawing(pairs)
    return {md for md, s in primary.items() if s == status}


def compute_unchanged_drawings(all_pairs, mode, source_drawing_numbers=None, dest_drawing_numbers=None):
    """「変更していない図面（流用元と流用先とで共通）」の対象図番集合を返す。

    Step3表示（render_pair_list）と図面管理台帳への完全新規図面登録
    （get_brand_new_drawing_pairs）の双方から呼ばれる共通ロジック。
    Type A（all_in_one）では対象なし。

    Args:
        all_pairs: ペア情報のリスト
        mode: ペアリング方式（'all_in_one'/'auto'/'pair_list'）
        source_drawing_numbers: 流用元（旧）プールの図番集合（mode='auto'でのみ使用）
        dest_drawing_numbers: 流用先（新）プールの図番集合（mode='auto'でのみ使用）
    """
    primary_status = primary_status_by_drawing(all_pairs)
    if mode == 'auto':
        no_source_drawings = {
            p['main_drawing'] for p in all_pairs
            if p['status'] == STATUS_NO_SOURCE_DEFINED
            and primary_status.get(p['main_drawing']) == STATUS_NO_SOURCE_DEFINED
        }
        common_drawings = (source_drawing_numbers or set()) & (dest_drawing_numbers or set())
        return common_drawings & no_source_drawings
    elif mode == 'pair_list':
        # 流用先のDXFファイルが実在する図番のみを対象とする（2026-06修正）。
        # ペアリスト上は「流用元==流用先」と宣言されていても、肝心のファイルが
        # 未アップロードな場合、「流用先図面総数(a)」（ファイル実在のみで算出）との
        # 整合（差分抽出が可能なペア+完全新規図面+変更していない図面=a）が崩れる
        # ため、main_file_info（実ファイル）がある図番のみに限定する。ファイルが
        # 無い図番は「未アップロードの図番」セクションで別途警告表示される
        # （_show_missing_drawings は identical 行も対象に含むよう修正済み）。
        return {
            p['main_drawing'] for p in all_pairs
            if p['status'] == STATUS_IDENTICAL
            and primary_status.get(p['main_drawing']) == STATUS_IDENTICAL
            and p.get('main_file_info')
        }
    return set()


def get_brand_new_drawing_pairs(all_pairs, mode, source_drawing_numbers=None, dest_drawing_numbers=None):
    """完全新規図面（流用元の参照がない図面）のペアを返す。

    main_drawing 単位で優先度フィルタ済み（他ステータスで既に分類されている図面は
    含まない）かつ「変更していない図面」に該当する図番は除外する。さらに、
    main_file_info（流用先のDXFファイル）が無い図番は除外する（2026-06 追加）。
    方式C（pair_list）ではペアリストの行が実際のアップロード状況と無関係に
    存在し得るため、流用元図番が空白でも流用先のファイル自体が未アップロードの
    場合がある（例: 引当前後リスト_ME25-9606-0 の DE3527-556-01B）。この場合、
    図面ファイルが存在しない以上「完全新規図面」として扱う（Step3表示・台帳登録の
    いずれにも含める）べきではない——別途「未アップロードの図番」セクション
    （_show_missing_drawings 等）で警告表示される。

    Step3表示（render_pair_list）と図面管理台帳への登録（update_master_if_needed /
    create_diff_zip）の両方で同じ集合を使うための共通ロジック。

    Args:
        all_pairs: ペア情報のリスト
        mode: ペアリング方式（'all_in_one'/'auto'/'pair_list'）
        source_drawing_numbers: compute_unchanged_drawings() に渡す（mode='auto'でのみ使用）
        dest_drawing_numbers: compute_unchanged_drawings() に渡す（mode='auto'でのみ使用）
    """
    primary_status = primary_status_by_drawing(all_pairs)
    no_source_pairs = [
        p for p in all_pairs
        if p['status'] == STATUS_NO_SOURCE_DEFINED
        and primary_status.get(p['main_drawing']) == STATUS_NO_SOURCE_DEFINED
        and p.get('main_file_info')
    ]
    unchanged_drawings = compute_unchanged_drawings(all_pairs, mode, source_drawing_numbers, dest_drawing_numbers)
    return [p for p in no_source_pairs if p['main_drawing'] not in unchanged_drawings]


def compute_total_drawings_count(mode, all_in_one_count=0, dest_count=0,
                                  pair_list_df=None, uploaded_drawing_numbers=None):
    """Summaryシート「図面統計」の分母件数を、ペアリング方式に応じて算出する。

    - Type A（all_in_one）: アップロードした全DXFファイル件数（アップロード図面総数）
    - Type B（auto）      : 流用先（新）DXFファイル件数（流用先図面総数）
    - Type C（pair_list） : ペアリスト中の流用先図番のうち、実際にDXFファイルが
                            アップロード済みのもののユニーク件数（流用先図面総数）
    """
    if mode == 'all_in_one':
        return all_in_one_count
    elif mode == 'auto':
        return dest_count
    elif mode == 'pair_list':
        if pair_list_df is None:
            return 0
        targets = {str(v).strip() for v in pair_list_df['流用先図番'] if str(v).strip()}
        uploaded = uploaded_drawing_numbers or set()
        return len(targets & uploaded)
    return 0


def normalize_pair_list_columns(df):
    """
    ペアリストDataFrameのカラム名・値を正規化する（読み込み元のExcel/CSV I/Oは
    呼び出し元の責務。本関数は純粋なDataFrame変換のみを行う）。

    必須カラム: 流用元図番, 流用先図番
    （旧カラム名 比較元図番/比較先図番、または Reference/Target も後方互換で受け付ける）

    Returns:
        tuple: (DataFrame または None, エラーメッセージ または None)
    """
    column_aliases = {
        'Reference': '流用元図番',
        'Target': '流用先図番',
        '比較元図番': '流用元図番',
        '比較先図番': '流用先図番',
    }
    df = df.rename(columns=column_aliases)

    required_columns = ['流用元図番', '流用先図番']
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        return None, (
            f"必須カラムが見つかりません: {missing}\n"
            f"実際のカラム: {list(df.columns)}\n"
            f"「流用元図番」「流用先図番」（旧名「比較元図番」「比較先図番」、"
            f"または「Reference」「Target」）のカラム名が必要です。"
        )

    df = df[required_columns].copy()
    # 文字列化し、空セル(NaN→'nan')や空白は空文字に正規化
    for col in required_columns:
        df[col] = df[col].astype(str).str.strip()
        df[col] = df[col].where(df[col].str.lower() != 'nan', '')
    # 両方が空白の行のみ除外（片側だけ空白の行は「片側のみペア」として残す）
    df = df[(df['流用元図番'] != '') | (df['流用先図番'] != '')]
    return df.reset_index(drop=True), None
