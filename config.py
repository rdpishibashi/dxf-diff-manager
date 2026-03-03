"""
DXF Diff Manager - 設定ファイル

このファイルにはアプリケーション全体で使用される設定値が定義されています。
"""


class UIConfig:
    """UI関連の設定"""

    # ファイルアップロード設定
    MASTER_FILE_TYPES = ["xlsx"]   # 親子関係台帳ファイルの拡張子
    DXF_FILE_TYPES = ["dxf"]       # DXFファイルの拡張子

    # メッセージ設定
    TITLE = "DXF Diff Manager - 図面差分管理ツール"
    SUBTITLE = "流用元（旧）と流用先（新）のDXFファイルを別々にアップロードし、ペアを自動抽出して差分DXF図面とラベル差分リストを出力します。親子関係台帳も更新できます。"


class DiffConfig:
    """差分比較関連の設定"""

    # デフォルト値
    DEFAULT_TOLERANCE = 0.01       # 座標許容誤差

    # DXFレイヤー色設定（AutoCADカラーインデックス）
    DEFAULT_DELETED_COLOR = 6      # 削除エンティティ（マゼンタ）
    DEFAULT_ADDED_COLOR = 4        # 追加エンティティ（シアン）
    DEFAULT_UNCHANGED_COLOR = 7    # 変更なしエンティティ（白/黒）

    # 色の選択肢（label, value）
    COLOR_OPTIONS = [
        (1, "1 - 赤"),
        (2, "2 - 黄"),
        (3, "3 - 緑"),
        (4, "4 - シアン"),
        (5, "5 - 青"),
        (6, "6 - マゼンタ"),
        (7, "7 - 白/黒"),
    ]

    # ZIPファイル名
    OUTPUT_ZIP_FILENAME = "dxf_diff_results.zip"
    MASTER_FILENAME = "Parent-Child_list.xlsx"


class ExtractionConfig:
    """DXF抽出関連の設定"""

    # 図番抽出設定
    # 両フォーマット対応: XX0000-000-00X（長）、XX0000-000X（短）
    DRAWING_NUMBER_PATTERN = r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]'  # 図番パターン

    # 距離設定（DXF単位）
    SOURCE_LABEL_PROXIMITY = 80     # 流用元図番ラベルからの検出距離
    DWG_NO_LABEL_PROXIMITY = 80     # DWG No.ラベルからの検出距離
    TITLE_PROXIMITY_X = 80          # TITLEラベルからの横方向検出距離

    # RevUpペア設定
    RIGHTMOST_DRAWING_TOLERANCE = 100.0  # 右端図面判定の許容範囲


class HelpText:
    """ヘルプテキスト"""

    USAGE_STEPS = [
        "このツールは、流用元（旧）と流用先（新）のDXFファイルを別々にアップロードし、",
        "ペアごとに差分を比較してDXFファイルとラベルリストを出力します。",
        "",
        "**使用手順：**",
        "1. （オプション）親子関係台帳をアップロードすると、新しい親子関係が自動的に追加されます",
        "2. Step 1-1: 流用元（旧）DXFファイルをアップロードし「図番を抽出（流用元）」を押してください",
        "3. Step 1-2: 流用先（新）DXFファイルをアップロードし「図番を抽出（流用先）」を押してください",
        "4. 「図面ペア・リスト作成」ボタンでペアを自動生成します（流用元と流用先の間でのみペアリング）",
        "5. 「差分抽出開始」ボタンをクリックして処理を実行します",
        "6. 完全なペアのみが処理され、ZIPファイルで一括ダウンロードできます",
        "7. ZIPには、差分DXFファイル、変更されたラベルリスト、変更していないラベルで指定の先頭文字列のラベルリスト、更新された親子関係台帳（アップロードした場合）が含まれます",
        "",
        "**出力DXFファイルの内容：**",
        "- ADDED (デフォルト色: シアン): 新図面にのみ存在する要素（追加された図形要素）",
        "- DELETED (デフォルト色: マゼンタ): 旧図面にのみ存在する要素（削除された図形要素）",
        "- UNCHANGED (デフォルト色: 白/黒): 両方の図面に存在し変更がない図形要素",
        "",
        "**注意事項：**",
        "- 図番が抽出できない場合はファイル名を図番として採用します",
        "- ペアリングは流用元と流用先の間でのみ行われます（同一グループ内ではペアリングしません）",
        "- 親子関係台帳には、有効なペア（図番と比較元図番の両方が存在する）のみが追加されます"
    ]


# 設定クラスのインスタンスを作成（簡単にアクセスできるように）
ui_config = UIConfig()
diff_config = DiffConfig()
extraction_config = ExtractionConfig()
help_text = HelpText()
