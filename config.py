"""
DXF Diff Manager - 設定ファイル

このファイルにはアプリケーション全体で使用される設定値が定義されています。
"""


class UIConfig:
    """UI関連の設定"""

    # カラー設定
    PRIMARY_COLOR = "#0066cc"      # 青色（ボタンの主色）
    HOVER_COLOR = "#0052a3"        # ホバー時の色（濃い青）
    FOCUS_SHADOW_COLOR = "rgba(0, 102, 204, 0.5)"  # フォーカス時の影

    # ファイルアップロード設定
    MASTER_FILE_TYPES = ["xlsx"]   # 親子関係台帳ファイルの拡張子
    DXF_FILE_TYPES = ["dxf"]       # DXFファイルの拡張子

    # メッセージ設定
    TITLE = "DXF Diff Manager - DXF差分管理ツール"
    SUBTITLE = "流用図面と元図面を自動的にペアリングし、差分をDXFフォーマットで出力します。親子関係台帳も更新します。"


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
    DRAWING_NUMBER_PATTERN = r'[A-Z]{2}\d{4}-\d{3}-\d{2}[A-Z]'  # 図番パターン

    # 距離設定（DXF単位）
    SOURCE_LABEL_PROXIMITY = 80     # 流用元図番ラベルからの検出距離
    DWG_NO_LABEL_PROXIMITY = 80     # DWG No.ラベルからの検出距離
    TITLE_PROXIMITY_X = 80          # TITLEラベルからの横方向検出距離

    # RevUpペア設定
    RIGHTMOST_DRAWING_TOLERANCE = 100.0  # 右端図面判定の許容範囲


class HelpText:
    """ヘルプテキスト"""

    USAGE_STEPS = [
        "このツールは、複数のDXFファイルから図面番号と流用元図番を自動抽出し、",
        "ペアごとに差分を比較してDXFファイルとして出力します。",
        "",
        "**使用手順：**",
        "1. （オプション）親子関係台帳をアップロードすると、新しい親子関係が自動的に追加されます",
        "2. DXFファイルを一括アップロードしてください（複数可）",
        "3. 自動的に図番と流用元図番が抽出され、ペアリストが表示されます",
        "4. 流用元図面が不足している場合は「追加アップロード」で追加できます",
        "5. 「差分比較を開始」ボタンをクリックして処理を実行します",
        "6. 完全なペアのみが処理され、ZIPファイルで一括ダウンロードできます",
        "7. ZIPには差分DXFファイルと更新された親子関係台帳（アップロードした場合）が含まれます",
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
        "- 親子関係台帳には、完全なペア（図番と流用元図番の両方が存在する）のみが追加されます"
    ]


# 設定クラスのインスタンスを作成（簡単にアクセスできるように）
ui_config = UIConfig()
diff_config = DiffConfig()
extraction_config = ExtractionConfig()
help_text = HelpText()
