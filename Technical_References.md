# DXF Diff Manager - 技術参照ドキュメント

> このドキュメントは、プロジェクトに存在した複数の技術文書（Technical_References.md / SYNC_STRATEGY.md / SYNC_SOLUTION_SUMMARY.md / DRAWING_NUMBER_FORMATS.md）を統合し、コードの詳細解説を加えた完全版リファレンスです。
> 機能追加・保守・バグ修正に必要なすべての情報を一か所に集約しています。

---

## 目次

1. [プロジェクト概要](#1-プロジェクト概要)
2. [ディレクトリ構成](#2-ディレクトリ構成)
3. [主な機能一覧](#3-主な機能一覧)
4. [使用方法（エンドユーザー向け）](#4-使用方法エンドユーザー向け)
5. [設定ファイル詳解 (config.py)](#5-設定ファイル詳解-configpy)
6. [app.py アーキテクチャ詳解](#6-apppy-アーキテクチャ詳解)
7. [utils/extract_labels.py 詳解](#7-utilsextract_labelspy-詳解)
8. [utils/compare_dxf.py 詳解](#8-utilscompare_dxfpy-詳解)
9. [utils/label_diff.py 詳解](#9-utilslabel_diffpy-詳解)
10. [utils/common_utils.py 詳解](#10-utilscommon_utilspy-詳解)
11. [図面番号フォーマット仕様](#11-図面番号フォーマット仕様)
12. [出力ファイル仕様](#12-出力ファイル仕様)
13. [プロジェクト間 utils 同期戦略](#13-プロジェクト間-utils-同期戦略)
14. [保守・拡張ガイド](#14-保守拡張ガイド)
15. [注意事項・既知の制約](#15-注意事項既知の制約)

---

## 1. プロジェクト概要

**DXF Diff Manager** は、電気設計図面（DXFファイル）の差分を自動管理するStreamlitアプリケーションです。

### 主な解決課題

- 複数のDXFファイルから図番・流用元図番を自動抽出し、ペアを自動生成する
- 各ペアに対してDXF差分ファイルとラベル差分リストを一括出力する
- 親子関係台帳（Excel）を自動更新し、図面間の流用関係を履歴管理する

### 技術スタック

| ライブラリ | バージョン要件 | 用途 |
|---|---|---|
| streamlit | ≥ 1.30.0 | WebUI |
| ezdxf | ≥ 1.4.2 | DXFファイルの読み書き |
| pandas | ≥ 2.0.0 | データ処理・Excel出力 |
| xlsxwriter | ≥ 3.0.0 | Excel生成 |
| numpy | 最新版 | 座標変換（行列演算） |

---

## 2. ディレクトリ構成

```
DXF-diff-manager/
├── app.py                    # メインStreamlitアプリ（約1980行）
├── config.py                 # 設定クラス（UIConfig / DiffConfig / ExtractionConfig / HelpText）
├── prefix_config.txt         # 未変更ラベル抽出プレフィックスの初期値
├── requirements.txt          # Python依存ライブラリ
├── sync_utils.py             # DXF-visual-diff との utils 同期スクリプト
├── 図番親子関係台帳.xlsx       # サンプル親子関係台帳（開発・テスト用）
├── utils/
│   ├── __init__.py
│   ├── extract_labels.py     # DXFラベル・図番・タイトル抽出
│   ├── compare_dxf.py        # DXFエンティティ差分比較エンジン
│   ├── label_diff.py         # ラベル差分計算・Excelワークブック生成
│   └── common_utils.py       # 共通ユーティリティ（ファイル保存・エラー処理）
└── .streamlit/
    └── config.toml           # Streamlit設定
```

---

## 3. 主な機能一覧

### 3.1 親子関係マスター管理

アップロードされた `Parent-Child_list.xlsx` を読み込み、処理完了後に更新したファイルをダウンロードZIPに含める。

| カラム名 | 内容 |
|---|---|
| Child | 図番（新図面） |
| Parent | 流用元図番（旧図面） |
| Relation | `RevUp`、`流用`、または `ペアリスト` |
| Title | 図面タイトル |
| Subtitle | 図面サブタイトル |
| Recorded Date | 実行日時（自動記入） |
| Deleted Entities | 削除図形数 |
| Added Entities | 追加図形数 |
| Diff Entities | 差分図形数（削除＋追加） |
| Unchanged Entities | 変更なし図形数 |
| Total Entities | 総図形数 |

既存レコードは上書き更新（Child/Parent の一致で判定）。関係種別が変わった場合は `{relation}-changed` 形式で記録。

### 3.2 3種類のペアリングモード

Step 1 の先頭でペアリング方式を選択する。選択は `st.session_state.step1_mode` に保存され、Step 2・Step 3 でも参照される。

| モード | キー | 概要 |
|---|---|---|
| 自動ペアリング | `auto` | 流用元と流用先を別々にアップロード。流用先DXFから流用元図番を抽出してペアを自動生成 |
| 一括アップロード | `all_in_one` | 全ファイルをまとめてアップロード。各DXFから流用元図番を抽出してプール内でペアを自動生成 |
| ペアリスト指定 | `pair_list` | ペアリストExcel/CSVと全DXFを一括アップロード。リストの内容でペアを作成 |

モードを切り替えると `st.session_state.pairs` がリセットされる。

### 3.3 自動ペアリングの優先順位（auto モード）

1. **RevUpペア（優先）**: Revision識別子（末尾1英大文字）のみ異なる同一図面（流用元×流用先の間でのみマッチング）
   - 例: `DE5313-008-02A` (流用元) と `DE5313-008-02B` (流用先) → ペア
2. **流用ペア**: 流用先DXFファイルに記載された流用元図番が流用元グループに存在する場合

### 3.4 差分比較処理

- 図番（新）= 比較対象A、流用元図番（旧）= 比較対象B として処理
- DXF差分エンジン（`compare_dxf.py`）によるエンティティ単位の高精度比較
- 3レイヤーの差分DXF出力（ADDED / DELETED / UNCHANGED）
- エンティティ数の自動計測（5種類）

### 3.5 ラベル比較機能

- `diff_labels.xlsx`: 座標ベースで変更されたラベル候補を出力
- `unchanged_labels.xlsx`: 指定プレフィックスに一致する未変更ラベルを出力

### 3.6 一括ダウンロード

処理結果をZIPファイルで一括ダウンロード（差分DXF ＋ Excelファイル ＋ 更新済み台帳）。

---

## 4. 使用方法（エンドユーザー向け）

### ステップ 0: 親子関係台帳のアップロード（オプション）

- `Parent-Child_list.xlsx` をドラッグ＆ドロップでアップロード
- アップロードと同時に自動読み込みされる（ボタン操作不要）
- スキップすると台帳管理機能は実行されない

### ペアリング方式の選択

プログラム説明の直後に表示されるラジオボタンで方式を選択する。

| 方式 | いつ使うか |
|---|---|
| auto | 流用元・流用先が明確に分かれており、流用先DXFに流用元図番が記載されている場合 |
| all_in_one | すべてのDXFが1つのフォルダにあり、各DXFに流用元図番が記載されている場合 |
| pair_list | ペアの対応関係を自分で制御したい場合、または図番がDXFに記載されていない場合 |

### ステップ 1（auto モード）: DXFファイルのアップロード

- Step 1-1: 流用元（旧）DXFファイルをアップロードし「ファイルを読み込む（流用元）」をクリック
  - ファイル名（拡張子なし）を図番として使用（DXF解析なし）
- Step 1-2: 流用先（新）DXFファイルをアップロードし「図番を抽出（流用先）」をクリック
  - ファイル名を図番として使用し、DXFから流用元図番のみ抽出

### ステップ 1（all_in_one モード）: DXFファイルの一括アップロード

- すべてのDXFファイルをまとめてアップロードし「図番を抽出（全ファイル）」をクリック
- ファイル名を図番として使用し、各DXFから流用元図番を抽出

### ステップ 1（pair_list モード）: ペアリストとDXFのアップロード

- Step 1-1: ペアリストファイル（Excel/CSV）をアップロード
  - 必須カラム: `比較元図番` / `比較先図番`（または英語名 `Reference` / `Target`）
- Step 1-2: 比較元・比較先のすべてのDXFファイルをまとめてアップロードし「ファイルを読み込む」をクリック
  - DXF解析なし（ファイル名のみを図番として使用）
- アップロード直後に不足DXFファイルの一覧が表示される

### ステップ 2: 図面ペア・リスト確認

- 「図面ペア・リスト作成」ボタンでペアを生成
  - auto: `create_pair_list()` でRevUp→流用の優先順位でペアを生成
  - all_in_one: `create_pairs_from_single_pool()` でプール内ペアを生成
  - pair_list: `create_pairs_from_pair_list()` でリスト通りにペアを生成
- ペアステータスの説明:

| status | 意味 |
|---|---|
| `complete` | 両ファイルが揃っており差分比較可能 |
| `missing_source` | 流用元（旧）ファイルが未アップロード |
| `missing_target` | 流用先（新）ファイルが未アップロード（pair_list のみ） |
| `missing_both` | 両ファイルが未アップロード（pair_list のみ） |
| `no_source_defined` | 流用元図番が未記載（差分比較スキップ） |

### ステップ 3: 差分比較の実行

- オプション設定（座標許容誤差・レイヤー色・未変更ラベルプレフィックス）を確認
- 「差分抽出開始」ボタンをクリック
- 処理完了後、ZIPファイルをダウンロード

---

## 5. 設定ファイル詳解 (config.py)

`config.py` はアプリ全体の設定を4つのクラスで集中管理する。

```python
class UIConfig:
    MASTER_FILE_TYPES = ["xlsx"]     # 台帳ファイルの許可拡張子
    DXF_FILE_TYPES = ["dxf"]        # DXFファイルの許可拡張子
    TITLE = "DXF Diff Manager - 図面差分管理ツール"
    SUBTITLE = "..."                 # UI上のサブタイトル文字列
```

```python
class DiffConfig:
    DEFAULT_TOLERANCE = 0.01         # 座標許容誤差（DXF差分・ラベル比較共通）
    DEFAULT_DELETED_COLOR = 6        # 削除エンティティ色（AutoCADカラー: マゼンタ）
    DEFAULT_ADDED_COLOR = 4          # 追加エンティティ色（シアン）
    DEFAULT_UNCHANGED_COLOR = 7      # 変更なしエンティティ色（白/黒）
    COLOR_OPTIONS = [...]            # UIの色選択肢リスト（label, value）ペア
    OUTPUT_ZIP_FILENAME = "dxf_diff_results.zip"
    MASTER_FILENAME = "Parent-Child_list.xlsx"
```

```python
class ExtractionConfig:
    # 図番正規表現パターン（長・短両フォーマット対応）
    DRAWING_NUMBER_PATTERN = r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]'
    SOURCE_LABEL_PROXIMITY = 80      # 流用元図番ラベルからの検出距離（DXF単位）
    DWG_NO_LABEL_PROXIMITY = 80      # DWG No.ラベルからの検出距離（DXF単位）
    TITLE_PROXIMITY_X = 80           # TITLEラベルからの横方向検出距離（DXF単位）
    RIGHTMOST_DRAWING_TOLERANCE = 100.0  # 右端図面判定の許容X幅
```

```python
class HelpText:
    USAGE_STEPS = [...]              # UIヘルプセクション用テキストリスト
```

各クラスのインスタンスが末尾でモジュールスコープに生成される（`ui_config` / `diff_config` / `extraction_config` / `help_text`）。

### 設定変更の影響範囲

| 設定項目 | 変更時の影響 |
|---|---|
| `DRAWING_NUMBER_PATTERN` | 図番抽出・RevUp検出・ペアリング全体に影響 |
| `SOURCE_LABEL_PROXIMITY` | 流用元図番の自動認識精度に影響 |
| `DEFAULT_TOLERANCE` | 差分比較の厳密さに影響（小さいほど厳密） |

---

## 6. app.py アーキテクチャ詳解

### 6.1 モジュール構成

`app.py` はおよそ1980行の単一ファイルで、Streamlit のセッション状態（`st.session_state`）を中心に状態管理を行う。

```python
# 主要インポート
from utils.extract_labels import extract_labels
from utils.compare_dxf import compare_dxf_files_and_generate_dxf
from utils.common_utils import save_uploadedfile, handle_error
from utils.label_diff import (
    compute_label_differences,
    filter_unchanged_by_prefix,
    build_diff_labels_workbook,
    build_unchanged_labels_workbook
)
from config import ui_config, diff_config, extraction_config, help_text
```

### 6.2 セッション状態のキー一覧

#### 共通キー

| キー | 型 | 内容 |
|---|---|---|
| `step1_mode` | str | ペアリングモード: `'auto'` / `'all_in_one'` / `'pair_list'` |
| `pairs` | list | 確定したペアリスト |
| `pairs_dirty` | bool | ファイル追加後・ペア生成前は True（ペア再生成が必要） |
| `master_df` | DataFrame | 読み込み済み親子関係台帳 |
| `master_file_name` | str | 台帳ファイル名 |
| `added_relationships_count` | int | 台帳に追加した関係の累計件数 |
| `drawing_info_cache` | dict | `{file_hash: 抽出情報}` のキャッシュ |
| `prefix_text_input` | str | テキストエリアのプレフィックス値 |
| `uploader_key` | int | ファイルアップローダーのリセット用カウンター |

#### auto モード専用キー

| キー | 型 | 内容 |
|---|---|---|
| `source_files_dict` | dict | 流用元ファイル辞書 `{図番: {filename, temp_path, ...}}` |
| `dest_files_dict` | dict | 流用先ファイル辞書（同上） |
| `source_upload_key` | int | 流用元アップローダーのリセット用カウンター |
| `dest_upload_key` | int | 流用先アップローダーのリセット用カウンター |
| `source_upload_failures` | list | アップロード失敗ファイル名リスト（流用元） |
| `dest_upload_failures` | list | アップロード失敗ファイル名リスト（流用先） |
| `source_upload_summary` | dict | 処理件数・失敗件数・経過時間のサマリー（流用元） |
| `dest_upload_summary` | dict | 処理件数・失敗件数・経過時間のサマリー（流用先） |

#### pair_list モード専用キー

| キー | 型 | 内容 |
|---|---|---|
| `pair_list_df` | DataFrame | 読み込み済みペアリスト（比較元図番/比較先図番カラム） |
| `pair_list_file_name` | str | ペアリストファイル名 |
| `all_files_dict` | dict | 全DXFファイル辞書 `{図番: {filename, temp_path}}` |
| `all_upload_key` | int | DXFアップローダーのリセット用カウンター |
| `all_upload_failures` | list | アップロード失敗ファイル名リスト |
| `all_upload_summary` | dict | 処理件数・失敗件数・経過時間のサマリー |

#### all_in_one モード専用キー

| キー | 型 | 内容 |
|---|---|---|
| `all_in_one_files_dict` | dict | 全DXFファイル辞書（流用元図番も抽出済み） |
| `all_in_one_upload_key` | int | DXFアップローダーのリセット用カウンター |
| `all_in_one_upload_failures` | list | アップロード失敗ファイル名リスト |
| `all_in_one_upload_summary` | dict | 処理件数・失敗件数・経過時間のサマリー |

### 6.3 主要関数の解説

#### `load_default_prefixes()`

```python
def load_default_prefixes():
    """prefix_config.txt から初期プレフィックスリストを読み込む"""
    if PREFIX_CONFIG_PATH.exists():
        with open(PREFIX_CONFIG_PATH, 'r', encoding='utf-8') as f:
            lines = [line.rstrip('\n') for line in f]
        return [line for line in lines if line.strip()]
    return []
```

`prefix_config.txt` の各行をプレフィックスとして読み込む。空行は除外。アプリ起動時に一度だけ呼ばれ、`DEFAULT_PREFIXES` に格納される。

---

#### `cleanup_temp_files()`

```python
def cleanup_temp_files():
    """セッション状態に保存された一時ファイルをクリーンアップする"""
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
```

4つのファイル辞書（auto 2種 + pair_list 1種 + all_in_one 1種）すべてをカバーする。`save_uploadedfile()` で作成された一時ファイル（`tempfile.NamedTemporaryFile`）を削除する。

---

#### `load_parent_child_master(uploaded_file)`

```python
def load_parent_child_master(uploaded_file):
    """親子関係台帳Excelを読み込み、必須カラムを検証して DataFrame を返す"""
    df = pd.read_excel(uploaded_file)
    required_columns = ['Child', 'Parent']
    for col in required_columns:
        if col not in df.columns:
            st.error(f"必須カラム '{col}' が見つかりません。")
            return None
    return df
```

`Child` / `Parent` の2カラムが必須。それ以外のカラム（Relation, Title, Subtitle 等）は存在しなければ後続処理で動的に追加される。

---

#### `update_parent_child_master(master_df, new_pairs)`

台帳の更新ロジックの中核。

```python
def update_parent_child_master(master_df, new_pairs):
    """
    台帳 DataFrame に新しいペアを追加 or 既存レコードを更新する。

    new_pairs の各要素（dict）に含むキー:
      - 'main_drawing'   → Child
      - 'source_drawing' → Parent
      - 'title' / 'subtitle'
      - 'relation'       → 'RevUp' or '流用' or 'ペアリスト'
      - 'entity_counts'  → {'deleted_entities', 'added_entities', ...}
    """
```

**処理フロー**:
1. `(Parent == parent) & (Child == child)` でレコードを検索
2. 存在する場合 → `Relation`, `Title`, `Subtitle`, `Recorded Date`, エンティティ数を上書き更新
   - Relation が前回と異なる場合は `{relation}-changed` 形式で記録
3. 存在しない場合 → 新規レコードを `new_records` リストに追加し、最後に一括で DataFrame に連結

**動的カラム追加の仕組み**: 古い台帳（`Date` カラム等）との後方互換性のため、カラムが存在しない場合は `pd.Series(dtype='object')` で動的追加する。エンティティ数カラムは `pd.Series(dtype='Int64')` （NULLを許容するInt64型）で追加。

---

#### `_extract_by_filename(uploaded_file)`

```python
def _extract_by_filename(uploaded_file):
    """ファイル名（拡張子なし）を図番として使用するシンプルな抽出関数"""
    drawing_number = Path(uploaded_file.name).stem
    temp_path = save_uploadedfile(uploaded_file)
    return {
        'filename': uploaded_file.name,
        'temp_path': temp_path,
        'main_drawing_number': drawing_number,
    }
```

DXF解析を一切行わない軽量抽出関数。pair_list モードの流用元・流用先DXFアップロード、および auto モードの流用元DXFアップロードで使用される。`process_all_uploaded_files` の `extractor` として渡す。

---

#### `extract_source_number_from_dest_file(uploaded_file)`

```python
def extract_source_number_from_dest_file(uploaded_file):
    """
    流用先DXFファイルを処理する。
    図番（main_drawing_number）はファイル名から取得し、
    DXFからは流用元図番（source_drawing_number）のみを抽出する。
    """
```

auto モードの流用先アップロードおよび all_in_one モードで使用される。

- `main_drawing_number` = ファイル名（拡張子なし）
- `source_drawing_number` = `extract_labels()` で DXF から抽出（`extract_title_option=False` で高速化）
- キャッシュキー: ファイルの SHA-256 ハッシュ。キャッシュには `source_drawing_number` のみ保存

返却値:

```python
{
    'filename': str,
    'temp_path': str,
    'main_drawing_number': str,   # ファイル名由来
    'source_drawing_number': str or None,  # DXF抽出
    'title': None,
    'subtitle': None,
}
```

---

#### `extract_drawing_info_from_file(uploaded_file)`

```python
def extract_drawing_info_from_file(uploaded_file):
    """
    アップロードされた DXF ファイルから図番情報を抽出し、
    セッションキャッシュを活用して同一ファイルの再処理を防ぐ。
    """
    file_hash = hashlib.sha256(uploaded_file.getbuffer()).hexdigest()
    temp_path = save_uploadedfile(uploaded_file)

    cache = st.session_state.get('drawing_info_cache', {})
    cached_info = cache.get(file_hash)
    if not cached_info:
        _, info = extract_labels(
            temp_path,
            extract_drawing_numbers_option=True,
            extract_title_option=True,
            original_filename=uploaded_file.name
        )
        ...
```

DXFから図番・流用元図番・タイトル・サブタイトルを完全抽出する。`process_all_uploaded_files` のデフォルト `extractor`。現在は直接呼ばれる箇所はなく、`extractor` パラメータが省略された場合のデフォルトとしてのみ使用される。

**キャッシュ戦略**: ファイルの SHA-256 ハッシュをキーとして、同一内容のファイルが再アップロードされた場合に `extract_labels()` の再呼び出しをスキップする。一時ファイルパスはキャッシュに含めない（パスはセッション固有のため）。

---

#### `load_pair_list(uploaded_file)`

```python
def load_pair_list(uploaded_file):
    """
    ペアリストファイルを読み込む（ExcelまたはCSV）

    必須カラム: 比較元図番, 比較先図番（または Reference, Target）

    Returns:
        DataFrame or None（カラム名は 比較元図番/比較先図番 に統一）
    """
```

**カラム名の正規化**: 英語名 `Reference` → `比較元図番`、`Target` → `比較先図番` に自動変換する。

**後処理**:
- 両カラムを文字列に変換してストリップ
- 空文字・`'nan'` 行を除外
- インデックスをリセット

---

#### `process_all_uploaded_files(groups)`

```python
def process_all_uploaded_files(groups):
    """
    複数グループのアップロードDXFファイルを単一の進捗バーで処理する

    Args:
        groups: 処理グループのリスト。各要素は dict:
            - uploaded_files: アップロードされたファイルのリスト
            - files_dict: 格納先の辞書（in-place更新される）
            - upload_key_name: st.session_state の upload_key キー名
            - failures_key: st.session_state の failures リスト キー名
            - summary_key: st.session_state の summary dict キー名
            - extractor: (省略可) ファイル情報抽出関数
                         省略時デフォルト: extract_drawing_info_from_file

    Returns:
        bool: いずれかのファイルが処理されたかどうか
    """
```

**extractor の使い分け**:

| 呼び出し箇所 | 渡す extractor | 処理内容 |
|---|---|---|
| auto 流用元 | `_extract_by_filename` | ファイル名のみ、DXF解析なし |
| auto 流用先 | `extract_source_number_from_dest_file` | ファイル名 + DXFから流用元図番のみ抽出 |
| pair_list 全DXF | `_extract_by_filename` | ファイル名のみ、DXF解析なし |
| all_in_one 全DXF | `extract_source_number_from_dest_file` | ファイル名 + DXFから流用元図番のみ抽出 |
| （デフォルト） | `extract_drawing_info_from_file` | 完全抽出（図番・タイトル等） |

全グループの合計ファイル数を先に集計し、単一の `st.progress` バーで進捗を表示する。ファイルごとに `extractor(uploaded_file)` を呼び、成功したら `files_dict[main_drawing_number] = file_info` に格納する。

---

#### `process_dxf_files_by_filename(uploaded_files, files_dict, upload_key_name, failures_key, summary_key)`

`process_all_uploaded_files` に `_extract_by_filename` を渡す薄いラッパー。後方互換性および可読性のために残している。

---

#### `create_pair_list(source_files_dict, dest_files_dict, progress_callback=None)`

auto モード用のペアリング関数。

**優先順位**:
1. RevUp ペア（`create_revup_pairs()` で生成）
2. 流用ペア（流用先の `source_drawing_number` が流用元グループに存在する場合）

ペアのステータス:
- `'complete'`: 両ファイルが揃っている
- `'missing_source'`: 流用元ファイルが未アップロード
- `'no_source_defined'`: 流用元図番がない、または図番が自分自身と同一

---

#### `create_pairs_from_pair_list(pair_list_df, all_files_dict)`

pair_list モード用のペアリング関数。`pair_list_df` の各行について `all_files_dict` を参照し、ファイルの有無でステータスを決定する。

```python
if ref_file_info and target_file_info:     status = 'complete'
elif not ref_file_info and target_file_info: status = 'missing_source'
elif ref_file_info and not target_file_info: status = 'missing_target'
else:                                        status = 'missing_both'
```

`relation = 'ペアリスト'` が設定される。

---

#### `create_pairs_from_single_pool(files_dict)`

all_in_one モード用のペアリング関数。単一のファイルプールから自己完結型のペアを生成する。

**ロジック**:
1. `source_drawing_number` がある（かつ自分自身と異なる）ファイルをペアの主図面（流用先）とする
2. 同じプール内に対応する `source_drawing_number` のファイルがあれば `status='complete'`、なければ `'missing_source'`
3. `source_drawing_number` がないファイルのうち、他のファイルから流用元として参照されていないものを `status='no_source_defined'` として追記

---

#### `update_master_if_needed(pairs)`

台帳が読み込まれている場合のみ `update_parent_child_master()` を呼ぶ薄いラッパー。

```python
def update_master_if_needed(pairs):
    if st.session_state.master_df is None:
        return 0
    complete_pairs = [p for p in pairs if p['status'] == 'complete']
    if not complete_pairs:
        return 0
    updated_master, added_count = update_parent_child_master(
        st.session_state.master_df, complete_pairs
    )
    st.session_state.master_df = updated_master
    return added_count
```

---

#### `extract_base_drawing_number(drawing_number)`

```python
def extract_base_drawing_number(drawing_number):
    """
    図番の末尾1文字（Revision識別子）を取り除いたベース図番を返す。
    例: 'DE5313-008-02B' → ('DE5313-008-02', 'B')
    """
```

RevUpペア生成に使用。末尾が英大文字1文字（半角・全角両対応）であればそれを除去し、そうでなければ `(None, None)` を返す。

---

#### RevUpペアリングのロジック (`create_revup_pairs`)

```python
# 流用元のベース図番マップを作成
source_base_map = defaultdict(list)
for drawing_number in source_files_dict.keys():
    base, revision = extract_base_drawing_number(drawing_number)
    if base and revision:
        source_base_map[base].append((drawing_number, revision))

# 共通ベース図番で流用元×流用先をマッチング
common_bases = set(source_base_map.keys()) & set(dest_base_map.keys())
for base in common_bases:
    for old_drawing, old_rev in source_drawings:
        for new_drawing, new_rev in dest_drawings:
            if new_rev > old_rev and new_drawing not in used_dest and old_drawing not in used_source:
                # RevUpペアとして登録
```

RevUpペアは **流用ペアより優先** され、RevUpとして検出された図番は流用ペアリングの対象外となる。

---

#### `render_pair_list()`

```python
def render_pair_list():
    """ペアリストを表示

    Returns:
        list: 差分抽出可能なペア（status='complete'）のリスト
    """
```

`st.session_state.pairs` の内容を5種類のステータス別に分類して表示する。戻り値は `complete_pairs` のみのリスト（タプルではない）。

---

#### `render_step2_pairing(source_count, dest_count)`

```python
def render_step2_pairing(source_count, dest_count):
    """Step 2: 図面ペア・リスト作成

    Args:
        source_count: 流用元件数（auto）またはDXFファイル件数（その他モード）
        dest_count:   流用先件数（auto）または 0（その他モード）

    Returns:
        tuple: (complete_pairs, pairs_ready)
    """
    mode = st.session_state.step1_mode  # 'auto' / 'pair_list' / 'all_in_one'
```

モードを `st.session_state.step1_mode` から直接読み取る（センチネル値なし）。「図面ペア・リスト作成」ボタン押下後は、モードに応じた関数でペアを生成し、`pairs_dirty=False`・台帳更新・`gc.collect()` の共通後処理を実行してから `st.rerun()` する。

**ペア生成関数の呼び分け**:

| mode | 呼ぶ関数 |
|---|---|
| `'pair_list'` | `create_pairs_from_pair_list(pair_list_df, all_files_dict)` |
| `'all_in_one'` | `create_pairs_from_single_pool(all_in_one_files_dict)` |
| `'auto'` | `create_pair_list(source_files_dict, dest_files_dict, progress_callback)` |

---

#### `_render_step1_auto_mode()`

```python
def _render_step1_auto_mode():
    """auto モードの Step 1 UI を描画

    Returns:
        tuple: (source_count, dest_count)  # 実際の件数（センチネル値なし）
    """
```

- Step 1-1: 流用元アップロード（`_extract_by_filename` でDXF解析なし）
- Step 1-2: 流用先アップロード（`extract_source_number_from_dest_file` で流用元図番のみ抽出）

---

#### `_render_step1_pair_list_mode()`

```python
def _render_step1_pair_list_mode():
    """pair_list モードの Step 1 UI を描画

    Returns:
        tuple: (all_count, 0)  # DXFファイル件数, 常に 0
    """
```

- Step 1-1: ペアリストファイル（Excel/CSV）アップロード → `load_pair_list()` で読み込み
- Step 1-2: 全DXFアップロード（`_extract_by_filename` でDXF解析なし）
- アップロード後に `_show_missing_drawings()` で不足ファイルを即時表示

---

#### `_render_step1_all_in_one_mode()`

```python
def _render_step1_all_in_one_mode():
    """all_in_one モードの Step 1 UI を描画

    Returns:
        tuple: (all_in_one_count, 0)  # DXFファイル件数, 常に 0
    """
```

全DXFを一括アップロード（`extract_source_number_from_dest_file` で流用元図番を抽出）。

---

#### `_show_missing_drawings(pair_list_df, all_files_dict)`

pair_list モードでアップロード後すぐに呼ばれ、ペアリストに記載されているが未アップロードのDXFファイルを警告表示する。

```python
def _show_missing_drawings(pair_list_df, all_files_dict):
    """ペアリストに記載されているが未アップロードの図番を表示する"""
    all_drawing_numbers = set()
    for _, row in pair_list_df.iterrows():
        all_drawing_numbers.add(str(row['比較元図番']).strip())
        all_drawing_numbers.add(str(row['比較先図番']).strip())

    missing = [dn for dn in sorted(all_drawing_numbers) if dn not in all_files_dict]
    if missing:
        st.warning(f"以下の図番のDXFファイルがアップロードされていません（{len(missing)}件）")
        ...
```

---

#### `render_step3_inactive(source_count, dest_count, pairs_available)`

Step 3 を非アクティブ状態（差分比較不可）で表示する。モードに応じたメッセージを `st.session_state.step1_mode` から判断して表示する。

---

## 7. utils/extract_labels.py 詳解

DXFファイルからテキストラベル・図番・タイトルを抽出するコアモジュール。

### 7.1 適応的設定読み込みパターン

```python
try:
    # DXF-diff-manager 環境: 外部 config.py から読み込み
    from config import extraction_config
except ImportError:
    # DXF-visual-diff 環境: 内部定義にフォールバック
    class ExtractionConfig:
        DRAWING_NUMBER_PATTERN = r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]'
        SOURCE_LABEL_PROXIMITY = 80
        DWG_NO_LABEL_PROXIMITY = 80
        TITLE_PROXIMITY_X = 80
        RIGHTMOST_DRAWING_TOLERANCE = 100.0
    extraction_config = ExtractionConfig()
```

このパターンにより、同一ファイルが `DXF-diff-manager` と `DXF-visual-diff` の両方で動作する（詳細は [Section 13](#13-プロジェクト間-utils-同期戦略) 参照）。

### 7.2 `clean_mtext_format_codes(text, debug=False)`

MTEXTエンティティの生テキストに含まれるフォーマット制御コードを除去する。

```python
def clean_mtext_format_codes(text: str, debug=False) -> str:
    """
    除去対象の制御コード:
      \f...;   フォント制御
      \H...;   文字高さ制御
      \W...;   文字幅制御
      \C...;   カラー制御
      \A...;   配置制御
      \T...;   追跡制御
    保持するもの:
      \P       段落区切り（スペースに変換）
    """
    normalized_text = text.replace('¥', '\\')  # 日本語環境の円マーク正規化
    # ... 各制御コードを re.sub で除去 ...
    cleaned = cleaned.replace('\\P', ' ')       # 段落区切りをスペースに変換
    result = re.sub(r'\s+', ' ', cleaned).strip()
    return result
```

**日本語環境対応**: 日本語OSでは AutoCAD が `\`（バックスラッシュ）の代わりに `¥`（円マーク, U+00A5）を使用する場合がある。冒頭で `¥ → \` の正規化を行う。

### 7.3 `extract_text_from_entity(entity, debug=False)`

```python
def extract_text_from_entity(entity, debug=False) -> Tuple[str, str, Tuple[float, float]]:
    """
    Returns: (生テキスト, クリーンテキスト, (X座標, Y座標))

    エンティティタイプ別の処理:
      TEXT  → dxf.insert または dxf.location から座標取得
              dxf.text からテキスト取得（そのままクリーンテキストとして使用）
      MTEXT → dxf.insert から座標取得
              dxf.text → entity.text → plain_text() の順でテキスト取得を試行
              clean_mtext_format_codes() でフォーマットコードを除去
    """
```

MTEXTは複数の方法でテキスト取得を試みる（ezdxfバージョン差吸収のため）。

### 7.4 `extract_drawing_numbers(text, debug=False)`

```python
def extract_drawing_numbers(text: str) -> List[str]:
    """
    config.py の DRAWING_NUMBER_PATTERN に一致する文字列をすべて抽出する。
    大文字に正規化して重複を除去して返す。
    """
    patterns = [extraction_config.DRAWING_NUMBER_PATTERN]
    drawing_numbers = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            if match.upper() not in [dn.upper() for dn in drawing_numbers]:
                drawing_numbers.append(match.upper())
    return drawing_numbers
```

### 7.5 `determine_drawing_number_types(drawing_numbers, all_labels, filename, debug)`

図面内から検出された複数の図番のうち、どれが「図番（新）」でどれが「流用元図番（旧）」かを判定する。

**判定の優先順位**:

1. **ファイル名照合**: ファイル名（拡張子なし）に含まれる図番を「図番」とする
2. **ラベル近傍検索（流用元）**: 「流用元図番」「流用元」テキストに最も近い図番を「流用元図番」とする（距離閾値: `SOURCE_LABEL_PROXIMITY = 80`）
3. **ラベル近傍検索（DWG No.）**: 「DWG No.」テキストに最も近い図番を「図番」として確認（距離閾値: `DWG_NO_LABEL_PROXIMITY = 80`）
4. **座標ベースフォールバック**: 複数図面がある場合、最も右側の図面のみを対象に選別。`X + Y` が最大の図番を「図番」、次点を「流用元図番」とする

```python
# 最も右側の図面群を抽出
max_x = max([coords[0] for _, coords in drawing_numbers])
rightmost_numbers = [(dn, coords) for dn, coords in drawing_numbers
                    if coords[0] >= max_x - extraction_config.RIGHTMOST_DRAWING_TOLERANCE]
# X+Y の合計値で右下を判定
sorted_numbers = sorted(rightmost_numbers, key=lambda x: (x[1][0] + x[1][1]), reverse=True)
main_drawing = sorted_numbers[0][0]   # 右下が図番
source_drawing = sorted_numbers[1][0] if len(sorted_numbers) > 1 else None
```

### 7.6 `extract_title_and_subtitle(all_labels, drawing_numbers, debug)`

「TITLE」ラベルを起点にタイトルとサブタイトルを抽出する。

**抽出アルゴリズム**:

1. 全ラベルから「TITLE」「REVISION」のラベル位置を特定（複数ある場合は最大X座標を採用）
2. TITLEラベルの右側 10〜80 DXF単位の範囲かつ REVISION より下方向にあるラベルをタイトル候補とする
3. 候補をY座標でグルーピング（Y差 ≤ 5.0 を同一行とみなす）
4. 最もY座標が高い行のグループをタイトル行とし、X順に連結
5. タイトル行より下のグループ（X範囲が重なるもの）の中で最もY座標が高いものをサブタイトルとする
6. サブタイトルの末尾が英大文字1文字（Revision識別子）の場合は除外

### 7.7 `extract_labels(dxf_file, ...)`

メインの抽出関数。すべての抽出処理のエントリポイント。

```python
def extract_labels(dxf_file, filter_non_parts=False, sort_order="asc", debug=False,
                  selected_layers=None, validate_ref_designators=False,
                  extract_drawing_numbers_option=False, extract_title_option=False,
                  include_coordinates=False, original_filename=None):
    """
    Returns: (ラベルリスト, info辞書)

    info辞書のキー:
      total_extracted, filtered_count, final_count
      processed_layers, total_layers, filename
      invalid_ref_designators, main_drawing_number, source_drawing_number
      all_drawing_numbers, title, subtitle
    """
```

**エンティティ収集の順序**:
1. `MODEL_SPACE` の TEXT / MTEXT エンティティ
2. `PAPER_SPACE`（Model 以外のレイアウト）の TEXT / MTEXT エンティティ
3. `BLOCKS` 内の TEXT / MTEXT エンティティ（INSERT として参照されているもの）

**ラベル座標付き返却**: `include_coordinates=True` の場合、`labels_with_coordinates` リスト（`(ラベル, X, Y)` タプルのリスト）を返す。`label_diff.py` からこのモードで呼ばれる。

---

## 8. utils/compare_dxf.py 詳解

DXFファイル間のエンティティ差分を計算し、差分DXFファイルを生成するエンジン。

### 8.1 クラス構成

```
ToleranceConfig          → エンティティタイプ別の許容誤差管理
CoordinateTransformer    → 座標正規化・変換行列演算
EntityExpander           → INSERTエンティティの展開（ブロック参照を絶対座標化）
SignatureGenerator       → エンティティの署名（ハッシュ）生成
```

### 8.2 `ToleranceConfig`

```python
class ToleranceConfig:
    def __init__(self, base_tolerance: float = 0.01):
        self.coordinate_tolerance = base_tolerance
        self.connection_tolerance = base_tolerance * 0.1   # 接続点は厳密に
        self.text_position_tolerance = base_tolerance * 2  # テキスト位置は緩く
        self.angle_tolerance = 0.1                         # 角度は 0.1° 固定

    def get_tolerance_for_entity(self, entity_type, attribute=None) -> float:
        """エンティティタイプに応じた許容誤差を返す"""
        if entity_type in ['TEXT', 'MTEXT', 'ATTRIB']:
            return self.text_position_tolerance
        elif entity_type == 'POINT' or (attribute and 'connection' in attribute.lower()):
            return self.connection_tolerance
        elif attribute and any(a in attribute for a in ['angle', 'rotation']):
            return self.angle_tolerance
        else:
            return self.coordinate_tolerance
```

### 8.3 `CoordinateTransformer`

高精度座標正規化に `Decimal` モジュールを使用（精度50桁設定）。

```python
def normalize_coordinate_precise(self, value: float, tolerance: float) -> float:
    """許容誤差単位で座標を丸める（高精度版）"""
    decimal_value = Decimal(str(value))
    decimal_tolerance = Decimal(str(tolerance))
    normalized = (decimal_value / decimal_tolerance).quantize(Decimal('1')) * decimal_tolerance
    return float(normalized)
```

`create_transformation_matrix(insert_entity)`: INSERTエンティティのスケール・回転・移動を4x4同次変換行列（numpy配列）で表現する。

```python
# 変換順序: 平行移動 @ 回転 @ スケール
return translation_matrix @ rotation_matrix @ scale_matrix
```

### 8.4 `EntityExpander`

INSERTエンティティ（ブロック参照）を展開し、ブロック内のエンティティを絶対座標に変換したフラットなリストを生成する。

```python
def expand_insert_entities(self, doc, doc_label: str) -> List[Dict]:
    """
    msp内の全エンティティを処理:
      - INSERT → ブロック内エンティティを変換行列で絶対座標化
              → ATTRIB（属性テキスト）は恒等行列で処理（既に絶対座標）
      - その他 → 恒等行列で絶対座標エンティティとして格納
    """
```

**対応エンティティタイプ**: TEXT, MTEXT, LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE, POINT, etc.

**LWPOLYLINE 特別処理**: 頂点データは3通りの方法で取得を試みる（`get_points()` / `entity.vertices` のx,y属性 / `entity.vertices` のインデックス）。

### 8.5 `SignatureGenerator`

各エンティティを一意に識別するための署名（SHA-256ハッシュ文字列）を生成する。

```python
def create_absolute_entity_signature(self, absolute_entity: Dict) -> str:
    """
    署名に含む情報:
      - エンティティタイプ
      - 主要位置情報（insert / center / start 等）→ 正規化後の座標
      - エンティティタイプ固有の属性（radius, angle, text_content 等）
    """
    signature_data = json.dumps(signature_parts, sort_keys=True, default=str)
    return hashlib.md5(signature_data.encode()).hexdigest()
```

### 8.6 `compare_dxf_files_and_generate_dxf(file_a, file_b, ...)`

メインの差分比較関数。

```python
def compare_dxf_files_and_generate_dxf(
    file_a: str,            # 新図面（図番）のパス
    file_b: str,            # 旧図面（流用元図番）のパス
    output_path: str,       # 出力DXFファイルパス
    tolerance: float = 0.01,
    deleted_color: int = 6,   # 削除エンティティの色（AutoCADカラーインデックス）
    added_color: int = 4,     # 追加エンティティの色
    unchanged_color: int = 7, # 変更なしエンティティの色
    selected_layers_a = None, # file_a の処理対象レイヤー
    selected_layers_b = None, # file_b の処理対象レイヤー
    debug: bool = False
) -> dict:
    """
    Returns: {
        'deleted_entities': int,   # Bにのみ存在（削除）
        'added_entities': int,     # Aにのみ存在（追加）
        'unchanged_entities': int, # 両方に存在
        'diff_entities': int,      # deleted + added
        'total_entities': int      # 全エンティティ数
    }
    """
```

**処理フロー**:
1. 両ファイルを `ezdxf.readfile()` で読み込み
2. `EntityExpander.expand_insert_entities()` で各ファイルのエンティティを絶対座標リストに展開
3. `SignatureGenerator` で各エンティティの署名を生成
4. 署名の集合差分（set difference）で ADDED / DELETED / UNCHANGED を分類
5. 新しい DXF ドキュメントを作成し、3つのレイヤー（ADDED / DELETED / UNCHANGED）に色付きでエンティティを書き出す

---

## 9. utils/label_diff.py 詳解

ラベルの差分計算とExcelワークブック生成を担当するモジュール。

### 9.1 `compute_label_differences(new_file, old_file, tolerance, label_cache)`

```python
def compute_label_differences(new_file, old_file, tolerance=0.01, label_cache=None):
    """
    Returns: (change_rows, unchanged_entries)

    change_rows: 変更候補のリスト（各要素は dict）
      {'Coordinate X': float, 'Coordinate Y': float,
       'Old Label': str or None, 'New Label': str or None}

    unchanged_entries: 未変更ラベルのリスト（各要素は dict）
      {'label': str, 'count': int, 'coordinate': (float, float)}
    """
```

**処理フロー**:
1. `_load_labels_with_cache()` で新旧ファイルのラベルを取得（`include_coordinates=True`）
2. `round_labels_with_coordinates()` で座標を許容誤差単位に丸める
3. `group_labels_by_coordinate()` で座標ごとにラベルをカウント集計
4. `find_label_change_pairs()` で座標単位で新旧を突き合わせ

### 9.2 `find_label_change_pairs(group_new, group_old)`

座標ごとのラベル差分計算のコアロジック。

```python
for coord in all_coords:
    counter_new = group_new.get(coord, Counter()).copy()
    counter_old = group_old.get(coord, Counter()).copy()

    # 1. 共通ラベルを unchanged として記録し、カウンターから除去
    shared_labels = set(counter_new.keys()) & set(counter_old.keys())
    for label in sorted(shared_labels):
        min_count = min(counter_new[label], counter_old[label])
        unchanged_entries.append({'label': label, 'count': min_count, 'coordinate': coord})
        counter_new[label] -= min_count
        counter_old[label] -= min_count

    # 2. 残ったラベルを change_rows に記録
    # 旧のみ残った → Old Label (削除候補)
    # 新のみ残った → New Label (追加候補)
    # 同数のペアは名称変更候補として組み合わせる
    pairable = min(len(old_only), len(new_only))
    for i in range(pairable):
        change_rows.append({'Old Label': old_only[i], 'New Label': new_only[i], ...})
```

### 9.3 `filter_unchanged_by_prefix(unchanged_entries, prefixes)`

```python
def filter_unchanged_by_prefix(unchanged_entries, prefixes: List[str]):
    """
    指定プレフィックスで始まる未変更ラベルのみを抽出し、
    同一(label, x, y)で集計して返す。
    """
    for entry in unchanged_entries:
        label = entry['label']
        if any(label.startswith(prefix) for prefix in prefixes):
            key = (label, coord[0], coord[1])
            aggregated[key] = aggregated.get(key, 0) + entry['count']
```

### 9.4 Excelワークブック生成関数

```python
def build_diff_labels_workbook(sheets: List[Dict]) -> bytes:
    """
    sheets の各要素:
      {'sheet_name': str, 'rows': list, 'old_label_name': str, 'new_label_name': str}
    列: Coordinate X / Coordinate Y / {old_label_name} / {new_label_name}
    """

def build_unchanged_labels_workbook(sheets: List[Dict]) -> bytes:
    """
    sheets の各要素:
      {'sheet_name': str, 'rows': list}
    列: Label / Count / Coordinate X / Coordinate Y
    """
```

どちらも `io.BytesIO` でバイト列として返す。`pd.ExcelWriter` に `xlsxwriter` エンジンを使用。シート名はExcelの31文字制限を考慮し、`ensure_unique_sheet_name()` で一意性を保証。

### 9.5 `format_sheet(writer, sheet_name, df)`

全シートに共通の書式設定を適用する。

| 列種別 | 幅 |
|---|---|
| Coordinate X / Y | 14 |
| Old Label / New Label / Label | 30 |
| その他（Count等） | 12 |

先頭行をフリーズ（`worksheet.freeze_panes(1, 0)`）。

---

## 10. utils/common_utils.py 詳解

```python
def save_uploadedfile(uploadedfile):
    """
    Streamlit の UploadedFile を一時ファイルに保存し、そのパスを返す。
    拡張子は元ファイルから継承（DXF → .dxf, xlsx → .xlsx）。
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploadedfile.name)[1]) as f:
        f.write(uploadedfile.getbuffer())
        return f.name

def handle_error(e, show_traceback=True):
    """
    Streamlit の st.error() でエラーを表示する。
    show_traceback=True の場合はスタックトレースも表示。
    """
    import streamlit as st
    st.error(f"エラーが発生しました: {str(e)}")
    if show_traceback:
        st.error(traceback.format_exc())
```

`delete=False` の一時ファイルは自動削除されない。`cleanup_temp_files()` が明示的に削除する必要がある。

---

## 11. 図面番号フォーマット仕様

### サポートするフォーマット

| フォーマット | パターン | 例 |
|---|---|---|
| 長形式（標準） | `XX0000-000-00X` | `EE6668-405-00A`, `DE5313-008-02B` |
| 短形式 | `XX0000-000X` | `EE6668-405A`, `DE5313-008B` |

### 正規表現パターン

```regex
[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]
```

| 部分 | 意味 |
|---|---|
| `[A-Z]{2}` | 英大文字2文字（プレフィックス: EE, DE, XX 等） |
| `\d{4}` | 数字4桁 |
| `-` | リテラルハイフン |
| `\d{3}` | 数字3桁 |
| `(?:-\d{2})?` | オプション: ハイフン + 数字2桁（長形式部分） |
| `[A-Z]` | 英大文字1文字（Revision識別子） |

### なぜ非キャプチャグループを使うか

```python
# 問題: キャプチャグループを使うと re.findall() がグループ内容を返す
re.findall(r'[A-Z]{2}\d{4}-\d{3}(-\d{2})?[A-Z]', "EE6668-405A")
# → ['']  ← 意図しない結果

# 解決: 非キャプチャグループ (?:...) を使用
re.findall(r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]', "EE6668-405A")
# → ['EE6668-405A']  ← 正しい結果
```

### RevUp検出でのベース図番抽出

```python
# 'DE5313-008-02B' → ベース図番 'DE5313-008-02'（末尾の 'B' を除去）
# 'EE6668-405A'    → ベース図番 'EE6668-405'（末尾の 'A' を除去）
base = drawing_number[:-1]  # 末尾1文字を除去
```

### 無効フォーマットの例（正しく却下される）

- `EE6668-405` — Revision識別子の英大文字なし
- `E6668-405A` — プレフィックスが1文字（2文字必要）
- `EE66-405A` — 数字部分が2桁（4桁必要）
- `EE6668405A` — ハイフンなし

### パターン変更時の更新箇所

図番パターンを変更する際は、**必ず両方を更新する**:

1. `DXF-diff-manager/config.py` → `ExtractionConfig.DRAWING_NUMBER_PATTERN`
2. `DXF-diff-manager/utils/extract_labels.py` → フォールバック `ExtractionConfig` クラス内の同名属性

更新後は同期スクリプトで `DXF-visual-diff` にも反映する:
```bash
cd DXF-diff-manager
python3 sync_utils.py
```

---

## 12. 出力ファイル仕様

### 12.1 差分DXFファイル

ファイル名: `{新図番}_vs_{旧図番}.dxf`

| レイヤー名 | 色（デフォルト） | 内容 |
|---|---|---|
| ADDED | シアン（4） | 新図面のみに存在する要素（追加） |
| DELETED | マゼンタ（6） | 旧図面のみに存在する要素（削除） |
| UNCHANGED | 白/黒（7） | 両方に存在し変更なしの要素 |

AutoCADカラーインデックス（ACI）: 1=赤, 2=黄, 3=緑, 4=シアン, 5=青, 6=マゼンタ, 7=白/黒

### 12.2 diff_labels.xlsx

各ペア（新図番）をシート名として1シートずつ作成。

| 列名 | 内容 |
|---|---|
| Coordinate X | ラベルのX座標（DXF単位） |
| Coordinate Y | ラベルのY座標（DXF単位） |
| Old: {旧図番} | 旧図面のラベル（削除候補または名称変更前） |
| New: {新図番} | 新図面のラベル（追加候補または名称変更後） |

### 12.3 unchanged_labels.xlsx

各ペア（新図番）をシート名として1シートずつ作成。プレフィックスに一致する未変更ラベルのみ掲載。

| 列名 | 内容 |
|---|---|
| Label | ラベル文字列 |
| Count | 同座標での出現回数 |
| Coordinate X | ラベルのX座標 |
| Coordinate Y | ラベルのY座標 |

### 12.4 Parent-Child_list.xlsx

`update_parent_child_master()` で更新された台帳。全カラム構成は [Section 3.1](#31-親子関係マスター管理) 参照。

---

## 13. プロジェクト間 utils 同期戦略

### 13.1 背景

`utils/` フォルダ内のモジュールは `DXF-diff-manager` と `DXF-visual-diff` で共有されているが、設定の取り込み方が異なる。

| プロジェクト | 設定の取り込み方 |
|---|---|
| DXF-diff-manager | 外部 `config.py` からインポート |
| DXF-visual-diff | モジュール内部で `ExtractionConfig` クラスを定義 |

### 13.2 採用した解決策: 適応的設定パターン

`extract_labels.py` の冒頭で `try/except ImportError` を使い、どちらの環境でも動作する単一ファイルを実現。

```python
try:
    from config import extraction_config  # DXF-diff-manager 環境
except ImportError:
    class ExtractionConfig:               # DXF-visual-diff 環境のフォールバック
        ...
    extraction_config = ExtractionConfig()
```

### 13.3 マスター管理

**プライマリマスター: DXF-diff-manager**

理由:
- より複雑な機能（約1980行 vs 約546行）
- `extract_labels.py` を最も広く活用している
- 親子関係管理・RevUp検出など先進機能を持つ

**同期方向**:
```
DXF-diff-manager/utils/ → (sync_utils.py) → DXF-visual-diff/utils/
```

### 13.4 同期ファイル一覧

| ファイル | 同期方式 | 備考 |
|---|---|---|
| `extract_labels.py` | 適応的同期 | try/except パターンを維持すること |
| `compare_dxf.py` | 直接同期 | config依存なし |
| `label_diff.py` | 直接同期 | config依存なし |
| `common_utils.py` | 直接同期 | 微小な差異あり（要注意） |

### 13.5 同期スクリプトの使用方法

```bash
# 変更プレビュー（実際には変更しない）
python3 sync_utils.py --dry-run

# 推奨マスター（DXF-diff-manager）から実行
python3 sync_utils.py

# マスターを強制指定
python3 sync_utils.py --diff-manager   # DXF-diff-manager を強制
python3 sync_utils.py --visual-diff    # DXF-visual-diff を強制
```

`sync_utils.py` は:
- ファイルのSHA-256ハッシュで変更を検出
- タイムスタンプでどちらが新しいか判断
- 同期後に Python 構文チェック（`py_compile`）を実行

### 13.6 config値が変わった場合の更新手順

1. `DXF-diff-manager/config.py` の `ExtractionConfig` を更新
2. `DXF-diff-manager/utils/extract_labels.py` 内のフォールバック `ExtractionConfig` クラスも**同じ値に**更新
3. `python3 sync_utils.py` で `DXF-visual-diff` に同期
4. 両プロジェクトで動作確認

---

## 14. 保守・拡張ガイド

### 14.1 図番パターンの追加・変更

```bash
# 1. config.py を編集
#    ExtractionConfig.DRAWING_NUMBER_PATTERN を更新

# 2. extract_labels.py のフォールバック設定も更新
#    （Section 11 参照）

# 3. 動作テスト
python3 -c "
from utils.extract_labels import extract_drawing_numbers
print(extract_drawing_numbers('EE6668-405-00A and EE6668-405A'))
"

# 4. DXF-visual-diff に同期
python3 sync_utils.py
```

### 14.2 新しいレイヤー色の追加

`config.py` の `DiffConfig.COLOR_OPTIONS` にタプル `(int, str)` を追加するだけでUIに反映される。

```python
COLOR_OPTIONS = [
    (1, "1 - 赤"),
    (2, "2 - 黄"),
    # ... 追加する色を以下に記述 ...
    (8, "8 - 灰色"),  # 例
]
```

### 14.3 親子関係台帳への新しいカラムの追加

`update_parent_child_master()` 内で動的カラム追加を行っているパターンを踏襲する。

```python
# 新カラム 'NewField' を追加する例
if 'NewField' not in updated_df.columns:
    updated_df['NewField'] = pd.Series(dtype='object')
updated_df.loc[mask, 'NewField'] = new_value
```

`new_records` 生成部分にも同じキーを追加することを忘れずに。

### 14.4 新しい差分比較エンティティタイプのサポート追加

`compare_dxf.py` の `EntityExpander.transform_entity_to_absolute()` と `_transform_coordinate_attributes()` にエンティティタイプ固有の処理を追加する。

署名計算は `SignatureGenerator.create_absolute_entity_signature()` で行われるため、新しいエンティティタイプの主要属性を署名に含めるよう確認すること。

### 14.5 未変更ラベルプレフィックスの変更

`prefix_config.txt` を直接編集する（1行1プレフィックス）。UIからも変更可能だが、再起動すると `prefix_config.txt` の値にリセットされる。

### 14.6 新しいペアリングモードの追加

1. `initialize_session_state()` に新モード専用のセッション状態キーを追加
2. `_render_step1_<モード名>_mode()` 関数を実装し、`render_step1_upload()` で呼び出す
3. ペアリストのラジオボタン（`st.radio` の `options` と `format_func`）にモードを追加
4. `render_step2_pairing()` に対応する `elif mode == '新モード名':` ブランチを追加
5. 対応するペア生成関数（`create_pairs_from_*()` パターン）を実装
6. `cleanup_temp_files()` のループ対象辞書キーを追加
7. `render_step3_inactive()` に新モードの表示ロジックを追加

### 14.7 アプリの起動

```bash
cd DXF-diff-manager
pip install -r requirements.txt
streamlit run app.py
```

Streamlit Cloud へのデプロイ時は `.streamlit/config.toml` でテーマ等を設定済み。

---

## 15. 注意事項・既知の制約

### 15.1 図番抽出の精度

- 図番が標準パターン（`[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]`）に従っていない場合、自動抽出できない
- auto / all_in_one モードでは `main_drawing_number` はファイル名から取得するため、上記の制約はペア作成精度に影響しない
- `source_drawing_number`（流用元図番）の抽出のみが正規表現パターンに依存する

### 15.2 RevUpペアの優先

- RevUpペアとして検出されたペアは、流用ペアリングから除外される
- 同一図面が RevUp と流用の両方に該当する場合、RevUp として処理される
- RevUp 検出は auto モードのみ（all_in_one・pair_list モードでは実施されない）

### 15.3 流用元図番の必須要件

- 流用ペア（auto・all_in_one モード）は流用元図番が図面内に記載されている必要がある
- RevUpペアは流用元図番の記載がなくても自動検出される（ベース図番の一致で判定）
- pair_list モードは流用元図番の記載に依存せず、リストの定義のみに基づく

### 15.4 エンティティ数の記録タイミング

- エンティティ数は差分比較が完了したペアにのみ記録される
- 比較に失敗したペアには記録されない（台帳の該当行はエンティティ数カラムが空欄のまま）

### 15.5 メモリ使用量

- 大量のDXFファイル（数十〜数百ファイル）を一度に処理する場合、メモリ消費に注意
- `gc.collect()` による明示的なガベージコレクションが各処理後に実行される

### 15.6 一時ファイルの残留

- `save_uploadedfile()` で作成される一時ファイルは `delete=False` のため自動削除されない
- `cleanup_temp_files()` が呼ばれるまで残留する（アプリ再起動時またはセッション終了時）
- 対象辞書: `source_files_dict`, `dest_files_dict`, `all_files_dict`, `all_in_one_files_dict`
- 異常終了した場合は OS の一時ディレクトリ（`/tmp` 等）に残留する可能性がある

### 15.7 Excelシート名の制限

- Excel のシート名は31文字以内という制約がある
- `ensure_unique_sheet_name()` で31文字を超える図番を自動切り詰め・重複回避する
- 長い図番で重複が生じた場合は `{先頭部分}_{連番}` の形式になる

### 15.8 同期スクリプトのパス

`sync_utils.py` は絶対パスでプロジェクトディレクトリを参照している:

```python
BASE_DIR = Path("/Users/ryozo/Dropbox/Client/ULVAC/ElectricDesignManagement/Tools")
```

別の環境で実行する場合はこのパスを変更する必要がある。

### 15.9 センチネル値廃止について

旧実装では `dest_count == -1`（pair_list モード）・`dest_count == -2`（all_in_one モード）というセンチネル値を `render_step2_pairing()` に渡してモード判定していたが、現在は廃止済み。モードは `st.session_state.step1_mode` を直接参照して判断する。Step 1 の各関数は常に実際のファイル件数を返す。

---

*最終更新: 2026-03-15*
