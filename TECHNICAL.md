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
- 図面管理台帳（Excel）を自動更新し、図面間の流用関係を履歴管理する

### 技術スタック

| ライブラリ | バージョン要件 | 用途 |
|---|---|---|
| streamlit | ≥ 1.40.0 | WebUI |
| ezdxf | ≥ 1.4.2 | DXFファイルの読み書き |
| pandas | ≥ 2.0.0 | データ処理・Excel出力 |
| xlsxwriter | ≥ 3.0.0 | Excel生成 |
| openpyxl | ≥ 3.1.0 | .xlsx ファイル読み込み |
| xlrd | ≥ 2.0.1 | .xls ファイル読み込み |
| numpy | ≥ 1.24.0 | 座標変換（行列演算） |

---

## 2. ディレクトリ構成

```
DXF-diff-manager/
├── app.py                    # メインStreamlitアプリ（約1910行）
├── config.py                 # 設定クラス（UIConfig / DiffConfig / ExtractionConfig / HelpText）
├── prefix_config.txt         # 未変更ラベル抽出プレフィックスの初期値
├── requirements.txt          # Python依存ライブラリ
├── sync_utils.py             # DXF-visual-diff との utils 同期スクリプト
├── 図面管理台帳.xlsx          # サンプル図面管理台帳（開発・テスト用）
├── utils/
│   ├── __init__.py
│   ├── pairing.py            # ペアリングのコアロジック（流用/RevUp判定・UI非依存のモデル層）
│   ├── extract_labels.py     # DXFラベル・図番・タイトル抽出
│   ├── compare_dxf.py        # DXFエンティティ差分比較エンジン
│   ├── label_diff.py         # ラベル差分計算・Excelワークブック生成
│   └── common_utils.py       # 共通ユーティリティ（ファイル保存・エラー処理）
├── tests/
│   ├── unit/                 # モデル層のユニットテスト（test_pairing.py 等）
│   └── regression/           # 回帰テスト（RevUp/流用ペアリング）
└── .streamlit/
    └── config.toml           # Streamlit設定
```

### モデル層 `utils/pairing.py`

ペア生成ロジックは `streamlit` 非依存の純粋関数として `utils/pairing.py` に集約されている（モデル層）。`app.py` の `create_pair_list` / `create_pairs_from_single_pool` / `create_pairs_from_pair_list` は後方互換のための薄いシムで、実体は本モジュールを呼ぶ。

| 関数 | 役割 |
|------|------|
| `extract_base_drawing_number(dn)` | 末尾1英大文字（Revision識別子）を除いたベース図番を返す |
| `find_revup_pairs(source, target)` | 同一ベース図番・リビジョン差の RevUp ペアを生成（source×target） |
| `build_pairs(source, target, progress_callback=None)` | **流用判定と RevUp 判定を独立2パスで実行**。方式A は `(pool, pool)`、方式B は `(source, dest)` を渡す |
| `build_pairs_from_list(df, files)` | 明示ペアリスト（方式C）からペア生成。RevUp 自動補完なし |
| `primary_status_by_drawing(pairs)` | main_drawing ごとに `STATUS_DISPLAY_PRIORITY` 上で最優先のステータスを1つ決定（UI表示の二重計上防止、2026-06 追加） |
| `drawings_with_status(pairs, status)` | `primary_status_by_drawing()` の結果から、指定ステータスが最優先の main_drawing 集合を返す |

関係(relation)・ステータス(status)は本モジュールの定数（`RELATION_*` / `STATUS_*`）に一元定義。

---

## 3. 主な機能一覧

### 3.1 図面管理台帳

Step 1 で「既存の図面管理台帳のアップロード」「図面管理台帳の新規作成」「図面管理台帳を作成せず」のいずれかを選択して台帳を設定する（2026-06 改修）。新規作成時は台帳ファイル名を直接入力せず、**指番・モジュール・サイド**の3フィールドから自動生成する（4.「ステップ 1」参照）。処理完了後、台帳を作成した場合のみ更新したファイルをダウンロードZIPに含める（「作成せず」を選んだ場合は差分DXF・ラベルリストのみ出力）。

出力 Excel は **2シート構成**:
- **Summary**: 統計サマリー（エンティティ合計・図形変更率・図面統計・流用率）。ラベル・分母はペアリング方式（Type A/B/C）により異なる（12.4 参照）
- **Diff List**: 図面管理台帳データ（以下のカラム構成）

| カラム名 | 内容 |
|---|---|
| Child | 図番（新図面） |
| Parent | 流用元図番（旧図面） |
| Relation | `RevUp`、`流用`、または `ペアリスト` |
| Title | 図面タイトル |
| Subtitle | 図面サブタイトル |
| Recorded Date | 実行日時（自動記入） |
| Note | 備考 |
| Deleted Entities | 削除図形数 |
| Added Entities | 追加図形数 |
| Diff Entities | 差分図形数（削除＋追加） |
| Unchanged Entities | 変更なし図形数 |
| Total Entities | 総図形数 |

既存レコードは上書き更新（Child/Parent の一致で判定）。関係種別が変わった場合は `{relation}-changed` 形式で記録。

### 3.2 3種類のペアリングモード

ペアリング方式の選択は Step 1（図面管理台帳の設定）より前に表示される。選択は `st.session_state.step1_mode` に保存され、Step 3・Step 4 でも参照される。

| モード | キー | 概要 |
|---|---|---|
| 自動ペアリング | `auto` | 流用元と流用先を別々にアップロード。流用先DXFから流用元図番を抽出してペアを自動生成 |
| 一括アップロード | `all_in_one` | 全ファイルをまとめてアップロード。各DXFから流用元図番を抽出してプール内でペアを自動生成 |
| ペアリスト指定 | `pair_list` | ペアリストExcel/CSVと全DXFを一括アップロード。リストの内容でペアを作成 |

モードを切り替えると `st.session_state.pairs` がリセットされる。

### 3.3 自動ペアリングの判定（auto モード）

auto モードでも流用判定と RevUp 判定を**独立して**実行し、両方のペアを出力する（all_in_one モードと同じ方針。3.3.1 参照）。

1. **RevUpペア**: Revision識別子（末尾1英大文字）のみ異なる同一図面（**流用元×流用先**の間でのみマッチング）
   - 例: `DE5313-008-02A` (流用元) と `DE5313-008-02B` (流用先) → ペア
2. **流用ペア**: 流用先DXFファイルに記載された流用元図番が流用元グループに存在する場合

- 流用元図番が流用元グループに**完全一致で存在しなくても**、同一ベース図番の別リビジョン（流用元グループ側）があれば RevUp ペアとして検出される。
- 完全に同一の（流用先, 流用元）ペアのみ重複排除し、RevUp 側を残す。
- RevUp で対応済みの流用先でも、別の流用元図番を持つ場合は独立した流用ペアとして追加するため、**同一の流用先図番が RevUp ペア・流用ペアの双方に登場し得る**（意図的な仕様。auto / all_in_one 共通）。

> 旧仕様（〜2026-05）では auto モードのみ「RevUp ペアを優先して消費し、消費された流用先は流用判定の対象外」としていたが、all_in_one モードとの一貫性のため独立2パスに統一した（2026-06）。

#### 3.3.1 一括ペアリングの判定（all_in_one モード）

all_in_one モードでは単一プール内で次の2判定を**独立して**実行し、両方のペアを出力する。

1. **RevUp 判定**: プール内の同一ベース図番でリビジョン差のあるファイル同士をペア化（`relation='RevUp'`）。連続リビジョンが揃う場合は `A→B`, `B→C` のように連続ペアを生成する。
2. **流用判定**: 各ファイルの流用元図番（`source_drawing_number`）がプール内に完全一致で存在すれば `complete`、なければ `missing_source`（`relation='流用'`）。

- 流用元図番がプールに**完全一致で存在しなくても**、同一ベース図番の別リビジョン（RevUp 相手）がプールにあれば RevUp ペアとして検出される。
- 完全に同一の（流用先, 流用元）ペアは重複排除し、RevUp 側を残す。
- 同一の流用先図番が「RevUp ペア」と「流用ペア」の双方に登場し得る（意図的な仕様）。

### 3.4 差分比較処理

- 図番（新）= 比較対象A、流用元図番（旧）= 比較対象B として処理
- DXF差分エンジン（`compare_dxf.py`）によるエンティティ単位の高精度比較
- 3レイヤーの差分DXF出力（ADDED / DELETED / UNCHANGED）
- エンティティ数の自動計測（5種類）

### 3.5 ラベル比較機能

- `diff_labels.xlsx`: 座標ベースで変更されたラベル候補を出力（Summary / Total / Invalid シートを含む）
- `unchanged_labels.xlsx`: 指定プレフィックスに一致する未変更ラベルを出力

「**機器符号妥当性チェック**」オプション（オプション設定内）を有効にすると、機器符号パターン（英字・数字の組み合わせ）に一致するラベルのみを比較対象とし（filter_non_parts=True）、標準フォーマット非適合の機器符号を Invalid シートに出力する。

### 3.6 一括ダウンロード

処理結果をZIPファイルで一括ダウンロード（差分DXF ＋ Excelファイル ＋ 更新済み台帳）。

---

## 4. 使用方法（エンドユーザー向け）

### ステップ 1: 図面管理台帳の設定（2026-06 改修）

ラジオボタンで利用方法を選択する（`st.session_state.step0_mode`: `upload` / `new` / `none`）。

| 選択肢 | 動作 |
|---|---|
| **既存の図面管理台帳のアップロード** | 既存の台帳 Excel をアップロードすると自動読み込みされる。新しく見つかった親子関係が追加される。 |
| **図面管理台帳の新規作成**（デフォルト） | 「指番を入力」「モジュールを入力」「サイド」の3フィールドを入力し、空の台帳を自動初期化する。台帳ファイル名は自由入力ではなく、3フィールドから自動生成され「図面管理台帳」欄に表示される。 |
| **図面管理台帳を作成せず** | 台帳を作成・更新しない。差分DXF・ラベルリストのみをZIPで出力する（`master_df` は常に `None`）。 |

**新規作成時のフォーマット検証（`SHIBAN_PATTERN` / `MODULE_PATTERN` / `SIDE_PATTERN`、app.py）:**

| フィールド | フォーマット | 必須/任意 | 未入力時のファイル名 |
|---|---|---|---|
| 指番 | `AA11-1111-1`（英大文字2桁-数字4桁-数字1桁） | 必須 | — |
| モジュール | `XXXX`（英大文字または数字4桁） | 任意 | `na` |
| サイド | `XXX`（英大文字または数字3桁） | 任意 | `na` |

台帳ファイル名 = `{指番}_{モジュール or na}_{サイド or na}.xlsx`。指番が未入力、またはいずれかのフィールドがフォーマット不正の場合は `st.error` でエラーを表示し、`master_df` / `master_file_name` を `None` のままにして後続（台帳の作成）をブロックする（差分抽出自体は台帳なしでも続行可能）。

- 台帳を作成した場合（upload/new）のみ処理後の台帳がダウンロードZIPに含まれる
- モードを切り替えると `master_df` はリセットされる

### ペアリング方式の選択

プログラム説明の直後に表示されるラジオボタンで方式を選択する。

| 方式 | いつ使うか |
|---|---|
| auto | 流用元・流用先が明確に分かれており、流用先DXFに流用元図番が記載されている場合 |
| all_in_one | すべてのDXFが1つのフォルダにあり、各DXFに流用元図番が記載されている場合 |
| pair_list | ペアの対応関係を自分で制御したい場合、または図番がDXFに記載されていない場合 |

### ステップ 2（auto モード）: DXFファイルのアップロード

- Step 2-1: 流用元（旧）DXFファイルをアップロードし「ファイルを読み込む（流用元）」をクリック
  - ファイル名（拡張子なし）を図番として使用（DXF解析なし）
- Step 2-2: 流用先（新）DXFファイルをアップロードし「図番を抽出（流用先）」をクリック
  - ファイル名を図番として使用し、DXFから流用元図番のみ抽出

### ステップ 2（all_in_one モード）: DXFファイルの一括アップロード

- すべてのDXFファイルをまとめてアップロードし「図番を抽出（全ファイル）」をクリック
- ファイル名を図番として使用し、各DXFから流用元図番を抽出

### ステップ 2（pair_list モード）: ペアリストとDXFのアップロード

- Step 2-1: ペアリストファイル（Excel/CSV）をアップロード
  - 必須カラム: `流用元図番` / `流用先図番`（2026-06 改称。旧カラム名 `比較元図番` / `比較先図番`、または英語名 `Reference` / `Target` も `load_pair_list()` で後方互換として受け付ける）
- Step 2-2: 流用元・流用先のすべてのDXFファイルをまとめてアップロードし「ファイルを読み込む」をクリック
  - DXF解析なし（ファイル名のみを図番として使用）
- アップロード直後に不足DXFファイルの一覧が表示される（`_show_missing_drawings`）
  - 流用元と流用先が同一図番の行は比較対象外のため、未アップロード判定からも除外される
  - アップロード済みファイルのキーは前後空白を除去（`strip`）して照合する

### ステップ 3: 図面ペア・リスト確認

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
| `one_sided` | 流用元・流用先の片側が空白（相手図番が存在しない、pair_list のみ） |
| `identical` | 流用元・流用先が同一図番（差分なしのため比較対象外、pair_list のみ） |
| `no_source_defined` | 流用元図番が未記載（差分比較スキップ） |

> **流用元と流用先が同一図番の行**（`流用元図番 == 流用先図番`）は `status='identical'` として分類される。差分が存在しないため `complete_pairs`（差分比較対象）には含まれない。

**表示セクション（`render_pair_list()`、2026-06 改修）:**

全セクションのタイトル末尾は「：N件」形式で件数を表示する。

| セクション | 対象ステータス | 備考 |
|---|---|---|
| 差分抽出が可能なペア | `complete` | |
| ⚠️ 流用元図番の図面がない図面 | `missing_source` | 旧名「比較元のDXFファイルが未アップロード」。流用先側が起点 |
| ⚠️ 流用先のDXFファイルが未アップロード | `missing_target`（pair_list のみ） | |
| ⚠️ 流用元・流用先ともに未アップロード | `missing_both`（pair_list のみ） | |
| ➖ 片側のみのペア | `one_sided`（pair_list のみ） | |
| 完全新規図面（流用元図番なし） | `no_source_defined` のうち、変更していない図面（後述）に該当しない図面 | 旧名「流用元図番の記載がない図面（比較対象外）」。`関係`列は固定で「完全新規図面」、`ステータス`列は固定で「流用元図番なし」（⚠️マークなし） |
| 変更していない図面（流用元と流用先とで共通） | 下記参照 | **Type A（all_in_one）では表示しない** |

**`変更していない図面` の算出方法（2026-06 修正。b+c+d が流用先総数(a)に一致するよう b・c・d を排他化）:**

- Type B（auto）: `(source_files_dict.keys() & dest_files_dict.keys())`（流用元・流用先の両グループに同一図番が存在するもの）の**うち、さらに `no_source_defined` 状態のものに限定**する（`common_drawings & {no_source_pairs の main_drawing}`）。単純な集合の積をそのまま使うと、別の流用元図番に対して `complete`/`missing_source` 判定済みの図面まで「変更していない」に二重計上され、`差分抽出が可能なペア(b)` + `流用元図番の図面がない図面(c)` + `変更していない図面(d)` の合計が流用先総数(a)を超えてしまう不整合があった（修正前のバグ）。修正後は b・c・d（+ 完全新規図面）が流用先図面を排他的に分割し、合計が必ず a に一致する。
- Type C（pair_list）: `status='identical'` のペアの `main_drawing` 集合（ペアリストの行は ref/target の組ごとに1ステータスのみ持つため、元から complete/missing_source と排他的）

`no_source_defined` のうち、上記の「変更していない図面」の対象図番に該当するものは「完全新規図面」セクションから除外され、「変更していない図面」側にのみ表示される。

### ステップ 4: 差分比較の実行

- オプション設定（機器符号妥当性チェック・座標マージン・レイヤー色・未変更ラベルプレフィックス）を確認
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
| `step0_mode` | str | 台帳設定モード: `'upload'`（既存アップロード）/ `'new'`（新規作成）/ `'none'`（作成せず） |
| `new_master_shiban_input` | str | 新規作成時の指番入力値（例: `AA11-1111-1`） |
| `new_master_module_input` | str | 新規作成時のモジュール入力値（例: `XXXX`、空可） |
| `new_master_side_input` | str | 新規作成時のサイド入力値（例: `XXX`、空可） |
| `step1_mode` | str | ペアリングモード: `'auto'` / `'all_in_one'` / `'pair_list'` |
| `pairs` | list | 確定したペアリスト |
| `pairs_dirty` | bool | ファイル追加後・ペア生成前は True（ペア再生成が必要） |
| `master_df` | DataFrame | 図面管理台帳（新規作成時は空DataFrame、アップロード時は読み込み済みデータ） |
| `master_file_name` | str | 台帳ファイル名（出力ZIPに使用） |
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
| `pair_list_df` | DataFrame | 読み込み済みペアリスト（流用元図番/流用先図番カラム） |
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

#### 差分抽出結果・ダウンロード関連キー（2026-06 メモリ最適化で整理）

| キー | 型 | 内容 |
|---|---|---|
| `zip_data` | bytes | 差分抽出結果ZIP本体。`diff_labels.xlsx` / `unchanged_labels.xlsx` / 各差分DXF / 台帳Excelを内包する**唯一の実体**（後述の理由により他キーには複製しない） |
| `results` | list | ペアごとの処理結果（成否・エンティティ数・ラベル数等） |
| `has_diff_labels` | bool | `diff_labels.xlsx` が生成されたか（プレビュー表示可否の判定用。実バイト列は持たない） |
| `has_unchanged_labels` | bool | `unchanged_labels.xlsx` が生成されたか（同上） |
| `processing_settings` | dict | 差分抽出時の設定（tolerance・色設定等）。結果表示時の注記に使用 |
| `downloaded` | bool | ZIPダウンロードボタンを押したか（二重ダウンロード防止用） |
| `diff_preview_expanded` | bool | `diff_labels.xlsx` プレビューexpanderを一度開いたら開いたままにするための状態 |

**`diff_labels_excel_data` / `unchanged_labels_excel_data` を session_state に保持しない理由**: これらのExcelバイト列は `zip_data` の中にも同一内容で書き込まれている（`create_diff_zip()` 内で `zip_file.writestr()` 済み）。以前は両方を別々に session_state に保持していたため、出力データが実質二重に保持されメモリを圧迫していた（Streamlit Community Cloud のリソース制限超過の一因）。現在はプレビュー表示時に `read_zip_member(zip_data, filename)` で `zip_data` から都度読み出す方式に変更し、`has_diff_labels` / `has_unchanged_labels` の bool フラグのみ保持する。

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

**注意**: これは「🔄 新しい差分抽出を開始」ボタン押下時にのみ呼ばれる。ユーザーがタブを閉じる・セッションがタイムアウトする等で離脱した場合はこの関数が呼ばれず一時ファイルが残留する。そのケースは `cleanup_stale_temp_files()`（[Section 10](#10-utilscommon_utilspy-詳解) 参照）が新規セッション開始時にセーフティネットとして回収する。

---

#### `read_zip_member(zip_data, member_name)`（2026-06 追加）

```python
def read_zip_member(zip_data, member_name):
    """zip_data（bytes）からメンバーを読み出す。存在しない場合は None。"""
    if not zip_data:
        return None
    try:
        with zipfile.ZipFile(BytesIO(zip_data)) as zf:
            if member_name in zf.namelist():
                return zf.read(member_name)
    except Exception:
        pass
    return None
```

`diff_labels.xlsx` / `unchanged_labels.xlsx` プレビュー表示時に `st.session_state.zip_data` から都度読み出すために使う。session_state に同じバイト列を複製保持しないためのヘルパー（[6.2 差分抽出結果・ダウンロード関連キー](#62-セッション状態のキー一覧)参照）。

---

#### `load_parent_child_master(uploaded_file)`

```python
def load_parent_child_master(uploaded_file):
    """図面管理台帳Excelを読み込み、必須カラムを検証して DataFrame を返す"""
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

    必須カラム: 流用元図番, 流用先図番（または Reference, Target）

    Returns:
        DataFrame or None（カラム名は 流用元図番/流用先図番 に統一）
    """
```

**ファイル形式の判定とエンジン選択**:

```python
if uploaded_file.name.lower().endswith('.csv'):
    df = pd.read_csv(uploaded_file)
elif uploaded_file.name.lower().endswith('.xls'):
    df = pd.read_excel(uploaded_file, engine='xlrd')  # xlrd>=2.0.1 が必要
else:
    df = pd.read_excel(uploaded_file)                 # .xlsx は openpyxl（デフォルト）
```

pandas 2.x では `.xls` 読み込みに `engine='xlrd'` の明示指定が必要。`openpyxl` は `.xls` 非対応のため指定しない。

**カラム名の正規化**: 英語名 `Reference` → `流用元図番`、`Target` → `流用先図番` に自動変換する。

**後処理**:
- 両カラムを文字列に変換してストリップ。空セル（`NaN`）・`'nan'` は空文字に正規化
- **両方が空白の行のみ除外**（片側だけ空白の行は「片側のみペア」として残す → `status='one_sided'`）
- インデックスをリセット

> 空セル（`NaN` は float 型）が図番文字列と混在すると後段の `sorted()` が `TypeError` になるため、必ず文字列化してから扱う。

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

**一時ファイルの上書き漏れ対策（2026-06）**: 同じ図番に再アップロードすると `files_dict[main_drawing]` が新しい `file_info` で上書きされるが、古い `file_info['temp_path']` の一時ファイルはそのままでは孤立する（`cleanup_temp_files()` は最終状態の辞書しか見ないため）。`files_dict[main_drawing] = file_info` で上書きする**前**に、既存エントリがあればその `temp_path` を `os.unlink()` してから上書きするようにした。

---

#### `process_dxf_files_by_filename(uploaded_files, files_dict, upload_key_name, failures_key, summary_key)`

`process_all_uploaded_files` に `_extract_by_filename` を渡す薄いラッパー。後方互換性および可読性のために残している。

---

#### `create_pair_list(source_files_dict, dest_files_dict, progress_callback=None)`

auto モード用ペアリング。**実体は `utils.pairing.build_pairs(source, dest)`**（`app.py` 側は薄いシム）。流用判定と RevUp 判定を**独立した2パス**で実行し、両方のペアを出力する（方式A `create_pairs_from_single_pool` と共通コア。流用元は流用元グループ、流用先は流用先グループに限定される）。

**`build_pairs` のロジック**:
1. **RevUp パス**: `find_revup_pairs(source, target)` を実行し、`status='complete'`, `relation='RevUp'` のペアを生成。キー `(流用先, 流用元)` を記録する。
2. **流用パス**: 全流用先ファイルについて、同一キーが RevUp で生成済みなら重複させずスキップ（流用先は登場済みとして記録）。未生成なら、流用元図番が流用元グループにあれば `complete`、なければ `missing_source`（`relation='流用'`）。
3. **孤立**: いずれの役割でもペアに登場せず、`source_drawing_number` も未記入（または自分自身）の流用先を `no_source_defined` として追記する。RevUp 対応済みの流用先は孤立扱いしない。

ペアのステータス:
- `'complete'`: 両ファイルが揃っている
- `'missing_source'`: 流用元ファイルが未アップロード
- `'no_source_defined'`: 流用元図番がない、または図番が自分自身と同一

> RevUp で対応済みの流用先でも別の流用元図番を持つ場合は独立した流用ペアを追加するため、同一流用先が双方に登場し得る。回帰テスト: `tests/regression/test_auto_revup.py`

---

#### `create_pairs_from_pair_list(pair_list_df, all_files_dict)`

pair_list モード用ペアリング。**実体は `utils.pairing.build_pairs_from_list()`**（`app.py` 側は薄いシム）。`pair_list_df` の各行について `all_files_dict` を参照し、図番の有無・ファイルの有無でステータスを決定する。RevUp 自動補完は行わない。

```python
if ref_drawing and target_drawing and ref_drawing == target_drawing:
                                             status = 'identical'      # 同一図番 → 比較対象外
elif not ref_drawing or not target_drawing:  status = 'one_sided'      # 片側空白
elif ref_file_info and target_file_info:     status = 'complete'
elif not ref_file_info and target_file_info: status = 'missing_source'
elif ref_file_info and not target_file_info: status = 'missing_target'
else:                                        status = 'missing_both'
```

`relation = 'ペアリスト'` が設定される。

- **`identical`**（流用元 == 流用先）: 差分が無いため `complete_pairs` に含めず、一覧にも表示しない。
- **`one_sided`**（片側空白）: 相手図番が存在しないため差分比較は行わないが、「片側のみのペア」一覧に表示する。

---

#### `create_pairs_from_single_pool(files_dict)`

all_in_one モード用ペアリング。**実体は `utils.pairing.build_pairs(files_dict, files_dict)`**（単一プールを source と target に渡す薄いシム）。auto モードと同一コアで、流用判定と RevUp 判定を**独立した2パス**で実行する。

**`build_pairs` のロジック（source==target の場合）**:
1. **RevUp パス**: `find_revup_pairs(pool, pool)` で同一ベース図番・リビジョン差のペアを `status='complete'`, `relation='RevUp'` で生成する（連続リビジョンは `A→B`, `B→C` の連続ペア）。生成したペアのキー `(流用先, 流用元)` を記録する。
2. **流用パス**: `source_drawing_number` がある（かつ自分自身と異なる）ファイルについて、同じキーが RevUp パスで生成済みなら重複させずスキップする。未生成なら、対応する流用元ファイルがプールにあれば `status='complete'`、なければ `'missing_source'`（`relation='流用'`）。
3. **孤立ファイル**: いずれの役割でもペアに登場せず、`source_drawing_number` も未記入（または自分自身）のファイルを `status='no_source_defined'` として追記する。RevUp 相手として使われた旧リビジョンや RevUp 流用先は孤立扱いしない。

> **流用元図番がプールに完全一致で存在しなくても**、同一ベース図番の別リビジョンがプールにあれば RevUp ペアとして `complete` で検出される。同一の流用先図番が流用ペア・RevUp ペアの双方に登場し得る。
>
> 回帰テスト: `tests/regression/test_single_pool_revup.py`

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

#### `extract_base_drawing_number(drawing_number)`（`utils/pairing.py`）

```python
def extract_base_drawing_number(drawing_number):
    """
    図番の末尾1文字（Revision識別子）を取り除いたベース図番を返す。
    例: 'DE5313-008-02B' → ('DE5313-008-02', 'B')
    """
```

RevUpペア生成に使用。末尾が英大文字1文字（半角・全角両対応）であればそれを除去し、そうでなければ `(None, None)` を返す。

---

#### RevUpペアリングのロジック (`find_revup_pairs`、`utils/pairing.py`）

```python
# 流用元(source)・流用先(target)それぞれのベース図番マップを作成
source_base_map = defaultdict(list)
for drawing_number in source_files.keys():
    base, revision = extract_base_drawing_number(drawing_number)
    if base and revision:
        source_base_map[base].append((drawing_number, revision))

# 共通ベース図番で source×target をマッチング
common_bases = set(source_base_map.keys()) & set(target_base_map.keys())
for base in common_bases:
    for old_drawing, old_rev in source_drawings:
        for new_drawing, new_rev in target_drawings:
            if new_rev > old_rev and new_drawing not in used_target and old_drawing not in used_source:
                # RevUpペアとして登録
```

`find_revup_pairs()`（旧 `create_revup_pairs`）は source×target で 1:1 の RevUp ペアを生成する（同一ベースに複数版がある場合は連続ペア）。方式A は `(pool, pool)`、方式B は `(source, dest)` で呼ぶ。呼び出し元の `build_pairs()` は **RevUp 判定と流用判定を独立して実行**し、完全に同一の（流用先, 流用元）ペアのみ重複排除して両方を出力する（RevUp を優先消費して流用判定から除外する旧仕様は 2026-06 に廃止）。

---

#### `render_pair_list()`

```python
def render_pair_list():
    """ペアリストを表示

    Returns:
        list: 差分抽出可能なペア（status='complete'）のリスト
    """
```

`st.session_state.pairs` の内容をステータス別に分類して表示する。戻り値は `complete_pairs` のみのリスト（タプルではない）。

- 表示する区分: `complete`（差分抽出が可能なペア）/ `missing_source`（流用元図番の図面がない図面）/ `missing_target` / `missing_both` / `one_sided`（片側のみのペア）/ `no_source_defined`（完全新規図面）/ 流用元・流用先共通図番（変更していない図面）
- `identical`（同一図番）は分類テーブルとしては表示しない（main_drawing は「変更していない図面」セクションの集合に取り込まれる。Type C 参照）
- 全セクションのタイトル末尾は「：N件」形式で件数を表示する（2026-06 統一）
- 「差分抽出が可能なペア」「片側のみのペア」の表では、値が常に一定となる「ステータス」列は出力しない（前者は `流用先（新）`/`流用元（旧）`/`関係`、後者は `流用先（新）`/`流用元（旧）` のみ）
- **`missing_source`（流用元図番の図面がない図面）の表では、同じ流用先に RevUp の `complete` ペアがある場合**、ステータス列を `⚠️ 流用元のDXFなし・RevUpあり（<RevUp流用元図番>）` と表示し、RevUp による差分抽出が可能であることを示す。RevUp が無ければ `⚠️ 流用元のDXFなし`。判定は `complete` かつ `relation='RevUp'` のペアを `流用先 → 流用元` で引く辞書で行う。
- `missing_source` の `st.expander` は `expanded=False`。`missing_target` / `missing_both`（pair_list モード用）は `expanded=True`。
- **完全新規図面（`no_source_defined`、2026-06 改修）**: `関係` 列は固定で「完全新規図面」、`ステータス` 列は固定で「流用元図番なし」（⚠️マークなし）。`unchanged_drawings`（後述）に含まれる図番はこのセクションから除外される。
- **変更していない図面（流用元と流用先とで共通）（2026-06 追加）**: `mode = st.session_state.step1_mode` に応じて対象図番集合 `unchanged_drawings` を算出する（Type A では表示しない）。

#### main_drawing 単位の排他化（`utils.pairing.primary_status_by_drawing()`、2026-06 確認済みバグの修正）

各セクションの「：N件」表記とユーザーから「`差分抽出が可能なペア` + `流用元図番の図面がない図面` + `変更していない図面` の合計が流用先総数と一致するはず」という指摘を受けて検証した結果、**同一の流用先図番（main_drawing）が複数ステータスのペアに登場し、複数セクションに二重計上される実バグ**が2種類見つかった（いずれも実データで確認済み）。

1. **方式 A/B（`build_pairs`）**: RevUp パスと流用パスが、同一の流用先に対して**異なる流用元図番**でそれぞれ別のペアを生成する場合（例: RevUpで `complete`、その図面自身が DXF 内に埋め込む別の流用元参照が未アップロードで `missing_source`）。`sample-dxf/` の基本サンプル（16ファイル）で実際に2件発生することを確認（`EE6666-365-61B`・`EE6331-370-51B` がそれぞれ RevUp と流用の両方で `complete` ペアに登場）。
2. **方式 C（`build_pairs_from_list`）**: ペアリストに同一の流用先図番が複数行記載されている場合（流用元図番が異なる、または一方が `流用元==流用先` の `identical` 行）。

**対策**: `utils/pairing.py` の `STATUS_DISPLAY_PRIORITY`（`complete` > `missing_source` > `missing_target` > `missing_both` > `one_sided` > `identical` > `no_source_defined`）と `primary_status_by_drawing(pairs)` で、main_drawing ごとに「最も優先度の高いステータス」を1つだけ決定する。`render_pair_list()` はこれを使い、各ステータス別の表示行（missing_source/missing_target/missing_both/identical/no_source_defined）を、その図面の優先ステータスと一致する行のみに絞り込む（`complete` と判定された図面は他のどのセクションにも現れない）。

- `差分抽出が可能なペア`（complete）の表は実際に生成される全ペアをそのまま表示する（同一図面が複数の流用元と比較される場合、表の行数はタイトルの件数より多くなることがある＝意図的な仕様）。タイトルの「：N件」は **main_drawing のユニーク数**（表の行数とは限らない）。
- `missing_source` / `missing_target` / `missing_both` / `no_source_defined` のタイトルの「：N件」も同様に main_drawing のユニーク数。
- `one_sided`（片側のみのペア）は流用先が空白（main_drawing なし）の行を含むため、`primary_status_by_drawing()` の対象外（行は常にそのまま表示・件数も行数のまま）。

**Type 別の `unchanged_drawings`（変更していない図面）の算出**:

- Type A（`all_in_one`）: 流用元・流用先の区別がないため、このセクション自体を表示しない（`unchanged_drawings = set()`）
- Type B（`auto`）: `common_drawings = source_files_dict.keys() & dest_files_dict.keys()` と、上記の排他化済み `no_source_pairs`（`no_source_defined` が優先ステータスの図面のみ）の `main_drawing` 集合との積を取る。
- Type C（`pair_list`）: 排他化済み `identical_pairs`（`identical` が優先ステータスの図面のみ）の `main_drawing` 集合。ただし `identical` 判定は流用元図番・流用先図番の文字列が一致するだけで決まり、実際にDXFファイルがアップロードされていない図番も含み得るため、流用先図面総数(a)の定義（実ファイルがある図番のみ）と揃えるよう `all_files_dict.keys()` との積でさらに絞り込む。

**検証方法**: 実データ（`sample-dxf/ME24-1001-0/`、流用先232件）で `差分抽出が可能なペア(73, ユニーク)` + `流用元図番の図面がない図面(154)` + `変更していない図面(5)` + `完全新規図面(0)` = 232 と一致することを確認。回帰テスト: `tests/unit/test_pairing.py` の `test_primary_status_prefers_complete_over_*`。

---

#### `render_step2_pairing(source_count, dest_count)`

```python
def render_step2_pairing(source_count, dest_count):
    """Step 3: 図面ペア・リスト作成

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
    """auto モードの Step 2 UI を描画

    Returns:
        tuple: (source_count, dest_count)  # 実際の件数（センチネル値なし）
    """
```

- Step 2-1: 流用元アップロード（`_extract_by_filename` でDXF解析なし）
- Step 2-2: 流用先アップロード（`extract_source_number_from_dest_file` で流用元図番のみ抽出）

---

#### `_render_step1_pair_list_mode()`

```python
def _render_step1_pair_list_mode():
    """pair_list モードの Step 2 UI を描画

    Returns:
        tuple: (all_count, 0)  # DXFファイル件数, 常に 0
    """
```

- Step 2-1: ペアリストファイル（Excel/CSV）アップロード → `load_pair_list()` で読み込み
- Step 2-2: 全DXFアップロード（`_extract_by_filename` でDXF解析なし）
- アップロード後に `_show_missing_drawings()` で不足ファイルを即時表示

---

#### `_render_step1_all_in_one_mode()`

```python
def _render_step1_all_in_one_mode():
    """all_in_one モードの Step 2 UI を描画

    Returns:
        tuple: (all_in_one_count, 0)  # DXFファイル件数, 常に 0
    """
```

全DXFを一括アップロード（`extract_source_number_from_dest_file` で流用元図番を抽出）。

---

#### `_show_missing_drawings(pair_list_df, all_files_dict)`

pair_list モードでアップロード後すぐに呼ばれ、ペアリストに記載されているが未アップロードのDXFファイルを流用元・流用先別に警告表示する。

```python
def _show_missing_drawings(pair_list_df, all_files_dict):
    """ペアリストにあるがアップロードされていない図番を表示"""
    def _norm(value):                          # NaN(float)対策で文字列化＋strip
        s = str(value).strip()
        return '' if s.lower() == 'nan' else s

    ref_drawings, target_drawings = set(), set()
    for _, row in pair_list_df.iterrows():
        ref = _norm(row['流用元図番'])
        target = _norm(row['流用先図番'])
        if ref and target and ref == target:   # 同一図番は比較対象外 → 除外
            continue
        if ref:    ref_drawings.add(ref)
        if target: target_drawings.add(target)

    uploaded = {str(k).strip() for k in all_files_dict.keys()}
    missing_ref = sorted(ref_drawings - uploaded)
    missing_target = sorted(target_drawings - uploaded)
    ...
```

**ポイント**:
- 値はすべて文字列化してから扱う（空セル `NaN` と文字列が混在した状態で `sorted()` すると `TypeError` になるため）
- 流用元と流用先が同一図番の行は未アップロード判定から除外する
- アップロード済みキーも `strip` して照合の取りこぼしを防ぐ
- 流用元・流用先それぞれの未アップロード一覧を別々に表示する

---

#### `render_step3_inactive(source_count, dest_count, pairs_available)`

Step 4 を非アクティブ状態（差分比較不可）で表示する。モードに応じたメッセージを `st.session_state.step1_mode` から判断して表示する。

---

## 7. utils/extract_labels.py 詳解

DXFファイルからテキストラベル・図番・タイトルを抽出するコアモジュール。

> **DXF-extract-labels との共通化（2026-05）**: `DXF-diff-manager/utils/extract_labels.py` と `DXF-extract-labels/utils/extract_labels.py` は同一ファイルに統一されている。変更は必ず両プロジェクトのファイルを同一内容に保つこと（`diff` コマンドで確認）。適応的設定読み込みパターン（Section 7.1）により、`config.py` の有無にかかわらず両環境で動作する。

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

**INSERT展開のスキップ最適化（`_block_has_text_content()`、2026-06 追加）**:

INSERT エンティティの展開は `e.virtual_entities()`（変換・複製を伴う重い処理）で行うが、
手描き回路図ではテキストを持たないブロック（コネクタ等の記号）の INSERT が非常に多い。
`_block_has_text_content(doc, block_name, cache)` で「ブロックが TEXT/MTEXT を含むか
（ネストINSERTを再帰的にたどった先も含む）」をブロック名単位でメモ化し、含まない
INSERT は `virtual_entities()` を呼ぶ前にスキップする。判定不能時は安全側（展開する）
に倒すため出力結果は変わらない。サンプル161ファイルで最適化前後の抽出結果が
完全一致することを確認済み（処理時間は計測環境で約10%短縮）。Step 2「ファイルを
読み込む」の高速化対策。`DXF-extract-labels`（primary）で実装し、本プロジェクトへ
伝播済み（バイト一致）。

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
      - INSERT → _expand_insert_recursive() に委譲（ネストINSERTも再帰展開）
      - その他 → 恒等行列で絶対座標エンティティとして格納
    """
```

**対応エンティティタイプ**: TEXT, MTEXT, LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE, POINT, etc.

**LWPOLYLINE / LEADER 特別処理**: 頂点データはDXF属性ではなく専用APIから取得する。
`_extract_polyline_like_vertices()` が3通りの方法で取得を試みる（`get_points()` /
`entity.vertices` のx,y属性 / `entity.vertices` のインデックス）。LEADER は
`get_points()` を持たないため3番目の方法（インデックスアクセス）でフォールバックする。

**LEADER対応（2026-06 追加）**:

以前は LEADER の頂点情報（`entity.vertices`）を抽出していなかったため、署名に位置情報が
一切含まれず、**同一図面内の複数のLEADERが全て同一ハッシュに畳まれてしまう**問題と、
`OutputGenerator.create_entity_from_absolute()` に LEADER 用の分岐が無く未対応エンティティの
フォールバックで `"[LEADER]"` というTEXTが出力される問題があった。
`safe_get_dxf_attributes()` で LWPOLYLINE と同様に `vertices` を抽出し、
`SignatureGenerator._add_geometry_details()` で LWPOLYLINE と共通の頂点ベース署名
（`leader_vertices_...` / `lwpolyline_vertices_...`）を生成するようにし、
`create_entity_from_absolute()` には `target_space.add_leader(vertices=..., dimstyle='Standard', ...)`
で実際の矢印線（LEADER）として出力する分岐を追加した。
`dimstyle='Standard'` は `ezdxf.new(..., setup=True)` で生成する出力ドキュメントに
常に存在するため追加のセットアップは不要。

**ネストINSERTの再帰展開（`_expand_insert_recursive`、2026-06 追加）**:

ブロック内にさらに INSERT（ブロック参照）が含まれる「ネストINSERT」（ブロック内ブロック）に対応する。
手書き回路図等では、ブロックの中に別のブロック（例: 接続用サブシンボル）が INSERT として
ネストされているケースがあり、これを展開しないと展開結果に `dxftype: 'INSERT'` のエントリが
残ってしまう。これは `OutputGenerator.create_entity_from_absolute()` の対応エンティティ一覧に
`INSERT` が無いため、未対応エンティティのフォールバック処理に落ち、出力DXFに
`"[INSERT]"` という **TEXT** が書き出される（DXF-viewer 等で「変換できなかった表示」に見える）
という不具合の原因になっていた。

```python
def _expand_insert_recursive(self, doc, insert_entity, transform_matrix, expanded_entities,
                              depth=0, max_depth=20):
    """
    block_entity が INSERT の場合:
      - 親の transform_matrix と、ネストINSERT自身の変換行列を合成（matmul）
      - 合成行列を渡して再帰呼び出し（深さ制限 max_depth=20 で循環参照ガード）
    block_entity がそれ以外の場合:
      - これまでと同様に transform_entity_to_absolute() で絶対座標化
    """
```

これにより、ネストブロック内の実体（LINE等）が UNCHANGED/ADDED/DELETED の各レイヤーに
正しく展開・分類されるようになった。

**ローカル属性キャッシュ（`safe_get_dxf_attributes()`、2026-06 追加、Step 4 高速化）**:

同じブロックが多数の INSERT から参照される手描き回路図（記号の繰り返し配置）では、
`transform_entity_to_absolute()` が呼ぶ `safe_get_dxf_attributes()`（座標変換**前**の
ローカル属性取得）がINSERTの数だけ再計算されていた。座標変換行列はINSERTごとに
異なるが、変換前のローカル属性自体はブロック内エンティティ単位で不変なので、
`EntityExpander._local_attrs_cache`（`id(entity)` をキーとする dict、インスタンス単位）
でメモ化する。`transform_entity_to_absolute()` は必ず `clean_attrs.copy()` してから
座標変換するため、このキャッシュの中身が書き換わることはない（`_transform_*_attributes()`
はいずれも新しい dict/list に結果を書き込む実装で、`clean_attrs` 側を in-place 変更しない
ことを確認済み）。`EntityExpander` インスタンスは1回の `compare_dxf_files_and_generate_dxf()`
呼び出し内でのみ生成・破棄されるため、`id(entity)` の再利用によるキャッシュ衝突は発生しない。

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

**`pair_cache: Optional[PairFileCache]`（2026-06 追加、Step 4 高速化）**:

バッチ内で同じファイルが複数ペアの main/source として再利用される場合
（RevUp/流用チェーンで同じ親図面が複数の子の比較対象になる等）、従来は
ペアごとに `ezdxf.readfile()` から再パース＋再展開していた。`PairFileCache`
（クラス定義は本関数の直前）は呼び出し元（`create_diff_zip()`、app.py）が
バッチ単位で1つ生成し、全ペアの呼び出しに渡す。

```python
class PairFileCache:
    """バッチ内での使用予定回数を事前に数え、最後の使用が終わったエントリは
    その場で破棄する。1回しか使われないファイルはそもそもキャッシュしない
    ため、実際に再利用される分だけピークメモリが増える（無条件に全ファイルを
    保持するわけではない）。"""

    def get_or_compute(self, key, compute_fn):
        # key = (file_path, global_offset) — A は常に offset=None、
        # B は呼び出し時の offset_b（現状は常に None）
        ...
```

`entities_a`/`data_a`/`locations_a` 等はいずれも読み取り専用（`create_diff_dxf()` /
`create_entity_from_absolute()` は新しい dict にコピーしてから書き込むのみで、
キャッシュされた構造を in-place 変更しない）ため、複数ペアで安全に共有できる。
関数末尾の `del entities_a` 等は、このローカル名を関数スコープから外すだけで、
`pair_cache` 側がまだ参照を保持していれば実体は維持される（キャッシュ側の
`get_or_compute()` が最後の使用後に自分で破棄する）。`pair_cache=None`（デフォルト）
の場合は従来通りキャッシュなしで毎回読み込む。サンプルファイルで同一ファイルが
5ペアに再利用されるケースで実測約30%短縮、有無での diff 結果完全一致を確認済み。

---

## 9. utils/label_diff.py 詳解

ラベルの差分計算とExcelワークブック生成を担当するモジュール。

### 9.1 `compute_label_differences(new_file, old_file, tolerance, label_cache, filter_non_parts, validate_ref_designators)`

```python
def compute_label_differences(new_file, old_file, tolerance=0.01, label_cache=None,
                               filter_non_parts=False, validate_ref_designators=False):
    """
    Returns: (change_rows, unchanged_entries, extra_info)

    change_rows: 変更候補のリスト（各要素は dict）
      {'Coordinate X': float, 'Coordinate Y': float,
       'Old Label': str or None, 'New Label': str or None}

    unchanged_entries: 未変更ラベルのリスト（各要素は dict）
      {'label': str, 'count': int, 'coordinate': (float, float)}

    extra_info: 追加情報 dict
      {'labels_new': list,                  # (label, x, y) タプルのリスト（Total シート用）
       'invalid_ref_designators': list,     # 標準フォーマット非適合ラベル（Invalid シート用）
       'title': str or None,
       'subtitle': str or None}
    """
```

新図面（new_file）には `filter_non_parts` と `validate_ref_designators` を両方適用し、旧図面（old_file）には `filter_non_parts` のみ適用する（旧図面の Invalid チェックは不要なため）。

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
def build_diff_labels_workbook(
    sheets: List[Dict],
    summary_data: Optional[List[Dict]] = None,
    total_data: Optional[List[Dict]] = None,
    invalid_data: Optional[List[Dict]] = None,
) -> bytes:
    """
    シート順: Summary → Total（任意）→ ペアシート × N → Invalid（任意）

    sheets の各要素:
      {'sheet_name': str, 'rows': list, 'old_label_name': str, 'new_label_name': str}

    summary_data: [{'図番', '流用元図番', '追加ラベル数', '削除ラベル数', '変更ラベル数', 'タイトル', 'サブタイトル'}]
    total_data:   [{'ラベル': str, '個数': int}]（filter_non_parts=True 時のみ渡す）
    invalid_data: [{'機器符号': str, '個数': int, 'ファイル名': str}]（validate_ref_designators=True 時のみ渡す）
    """

def build_unchanged_labels_workbook(sheets: List[Dict]) -> bytes:
    """
    sheets の各要素:
      {'sheet_name': str, 'rows': list}
    列: Label / Count / Coordinate X / Coordinate Y
    """
```

どちらも `io.BytesIO` でバイト列として返す。`pd.ExcelWriter` に `xlsxwriter` エンジンを使用。シート名はExcelの31文字制限を考慮し、`ensure_unique_sheet_name()` で一意性を保証。

Summary シートは `workbook.add_worksheet('Summary')` で手書き生成し、「図番」セルに `worksheet.write_url()` で対応ペアシートへの内部ハイパーリンクを設定する。ペアシート名は Summary 書き込みより前に事前確定させる（`pair_sheet_names` リスト）。

### 9.5 `format_sheet(writer, sheet_name, df)`

全シートに共通の書式設定を適用する。

| 列種別 | 幅 |
|---|---|
| Coordinate X / Y | 14 |
| Old Label / New Label / Label | 100 |
| ラベル / 機器符号 | 20 |
| ファイル名 | 40 |
| その他（Count等） | 12 |

先頭行をフリーズ（`worksheet.freeze_panes(1, 0)`）。

---

## 10. utils/common_utils.py 詳解

```python
TEMP_FILE_PREFIX = "dxfdm_"

def save_uploadedfile(uploadedfile):
    """
    Streamlit の UploadedFile を一時ファイルに保存し、そのパスを返す。
    拡張子は元ファイルから継承（DXF → .dxf, xlsx → .xlsx）。
    ファイル名には TEMP_FILE_PREFIX を付与する（孤立ファイルの安全な掃除のための識別用）。
    """
    with tempfile.NamedTemporaryFile(delete=False, prefix=TEMP_FILE_PREFIX,
                                      suffix=os.path.splitext(uploadedfile.name)[1]) as f:
        f.write(uploadedfile.getbuffer())
        return f.name


def cleanup_stale_temp_files(max_age_seconds=3 * 60 * 60):
    """
    タブを閉じる等でセッションが正常終了せず孤立した本アプリの一時ファイルを掃除する（2026-06 追加）。

    cleanup_temp_files()（app.py、リスタートボタン押下時のみ）では回収できない、
    離脱したセッションの一時ファイルに対するセーフティネット。OSの一時ディレクトリを
    TEMP_FILE_PREFIX で絞り込み、十分古い（既定3時間超）ファイルのみ削除する。
    新規セッション開始時（initialize_session_state()）に一度だけ呼ばれる。
    """
    tmp_dir = tempfile.gettempdir()
    now = time.time()
    for name in os.listdir(tmp_dir):
        if not name.startswith(TEMP_FILE_PREFIX):
            continue
        path = os.path.join(tmp_dir, name)
        if os.path.isfile(path) and (now - os.path.getmtime(path)) > max_age_seconds:
            os.unlink(path)  # 例外は内部で握り潰す（他プロセス使用中等）


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

`delete=False` の一時ファイルは自動削除されない。通常は `cleanup_temp_files()`（app.py、リスタートボタン押下時）が削除するが、それでも回収できない孤立ファイルは `cleanup_stale_temp_files()` が新規セッション開始時に掃除する。3時間という閾値は「他の同時接続セッションがまだ使用中の一時ファイルを誤って削除しない」ための保守的な値。閾値を短くするほど早く回収できるが、長時間（3時間超）処理を開いたままにしているセッションを誤って壊すリスクが上がる。

機器符号フィルタリング・バリデーション関連の3関数も提供する:

```python
def filter_non_circuit_symbols(labels, debug=False):
    """機器符号パターン（英字+数字の組み合わせ等）に一致しないラベルを除外。
    Returns: (filtered_labels, excluded_count)"""

def validate_circuit_symbols(labels):
    """CB*, ELB*, MCCB*, M*, K* 等の標準電気記号パターン非適合ラベルを返す。
    Returns: invalid_symbols リスト"""

def process_circuit_symbol_labels(labels, filter_non_parts=False, validate_ref_designators=False, debug=False):
    """上記2関数を統合して呼び出す薄いラッパー。
    Returns: {'labels': list, 'filtered_count': int, 'invalid_ref_designators': list}"""
```

機器符号フィルタ3関数は DXF-extract-labels の `common_utils.py` と同一内容。一方 `save_uploadedfile()` / `TEMP_FILE_PREFIX` / `cleanup_stale_temp_files()` は本プロジェクト固有のメモリ最適化対応（2026-06）であり、他プロジェクトへの伝播時はこの差分を踏まえて個別判断すること。

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

シート順: **Summary → Total（任意）→ ペアシート × N → Invalid（任意）**

#### Summary シート（常に出力）

| 列名 | 内容 |
|---|---|
| 図番 | 新図面の図番（対応ペアシートへのハイパーリンク付き） |
| 流用元図番 | 旧図面の図番 |
| 追加ラベル数 | Old=None かつ New!=None の行数 |
| 削除ラベル数 | Old!=None かつ New=None の行数 |
| 変更ラベル数 | Old!=None かつ New!=None の行数 |
| タイトル | DXFから抽出したタイトル |
| サブタイトル | DXFから抽出したサブタイトル |

#### Total シート（「機器符号妥当性チェック」ON 時のみ）

全ペアの新図面ラベルを合算した機器符号集計。

| 列名 | 内容 |
|---|---|
| ラベル | 機器符号文字列 |
| 個数 | 全ペア横断での出現回数 |

#### ペアシート（各ペアに1シート）

| 列名 | 内容 |
|---|---|
| Coordinate X | ラベルのX座標（DXF単位） |
| Coordinate Y | ラベルのY座標（DXF単位） |
| Old: {旧図番} | 旧図面のラベル（削除候補または名称変更前） |
| New: {新図番} | 新図面のラベル（追加候補または名称変更後） |

#### Invalid シート（「機器符号妥当性チェック」ON かつ非適合ラベルが存在する場合）

| 列名 | 内容 |
|---|---|
| 機器符号 | 標準フォーマット非適合のラベル文字列 |
| 個数 | 全ペア横断での出現回数 |
| ファイル名 | 検出された図番のカンマ区切りリスト |

### 12.3 unchanged_labels.xlsx

各ペア（新図番）をシート名として1シートずつ作成。プレフィックスに一致する未変更ラベルのみ掲載。

| 列名 | 内容 |
|---|---|
| Label | ラベル文字列 |
| Count | 同座標での出現回数 |
| Coordinate X | ラベルのX座標 |
| Coordinate Y | ラベルのY座標 |

### 12.4 図面管理台帳 Excel（ファイル名は Step 1 で指定）

`update_parent_child_master()` で更新された台帳。**2シート構成**で出力される。

#### Summary シート（2026-06 改修：ラベル・分母がペアリング方式により異なる）

`save_master_to_bytes(master_df, pairs, mode, total_drawings_count)` の `mode`（`st.session_state.step1_mode`）により、「総図形数」「図面統計」のラベル・分母が切り替わる。`total_drawings_count` は呼び出し側の `compute_total_drawings_count(mode)`（app.py）で算出する。

| 行グループ | 項目 | 計算式 |
|---|---|---|
| エンティティ統計 | 削除図形 総数 | Diff List の `Deleted Entities` 合計 |
| | 追加図形 総数 | Diff List の `Added Entities` 合計 |
| | 変更（追加+削除）図形 総数 | Diff List の `Diff Entities` 合計 |
| | 変更なし図形 総数 | Diff List の `Unchanged Entities` 合計 |
| | **Type A**: アップロード図面 図形総数 / **Type B・C**: 流用先図面 図形総数 | Diff List の `Total Entities` 合計 |
| | 図形変更率 [%] | `変更（追加+削除）図形 総数 ÷ 総図形数` |
| 図面統計 | **Type A**: アップロード図面総数 / **Type B・C**: 流用先図面総数 | `compute_total_drawings_count(mode)`（下表） |
| | 差分抽出ペア数 | `status == 'complete'` のペア数 |
| | 流用率 [%] | `差分抽出ペア数 ÷ 上記の図面総数` |

**`compute_total_drawings_count(mode)` の算出方法（app.py）:**

| mode (Type) | 算出方法 |
|---|---|
| `all_in_one`（A） | アップロード済み全DXFファイル数（`all_in_one_files_dict` の件数） |
| `auto`（B） | 流用先（新）DXFファイル数（`dest_files_dict` の件数） |
| `pair_list`（C） | ペアリスト中のユニークな流用先図番のうち、実際にDXFファイルがアップロード済みのもの（`{流用先図番} & all_files_dict.keys()` の件数） |

#### Diff List シート

図面管理台帳データ。全カラム構成は [Section 3.1](#31-図面管理台帳) 参照。

---

## 13. プロジェクト間 utils 同期戦略

### 13.1 背景

`utils/extract_labels.py` は複数のプロジェクトで共有されており、各プロジェクトの設定ファイル構成が異なる。

| プロジェクト | 設定の取り込み方 | 同期方式 |
|---|---|---|
| DXF-diff-manager | 外部 `config.py` からインポート | プライマリマスター |
| DXF-visual-diff | モジュール内部で `ExtractionConfig` クラスを定義 | `sync_utils.py` で同期 |
| DXF-extract-labels | `config.py` なし → フォールバック設定を使用 | **ファイル同一化**（手動コピー） |

**DXF-extract-labels との同一化（2026-05）**: `DXF-diff-manager/utils/extract_labels.py` と `DXF-extract-labels/utils/extract_labels.py` は同一ファイルに統一した。`try/except ImportError` パターンにより `config.py` のない環境でもフォールバック設定で正常動作する。同様に `common_utils.py` も両プロジェクトで同一内容にしている。変更時は両プロジェクトのファイルを同一内容に保つこと（`diff` コマンドで確認）。

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
- より複雑な機能（約1910行 vs 約546行）
- `extract_labels.py` を最も広く活用している
- 親子関係管理・RevUp検出など先進機能を持つ

**同期方向**:
```
DXF-diff-manager/utils/ → (sync_utils.py) → DXF-visual-diff/utils/
```

### 13.4 同期ファイル一覧

**DXF-diff-manager → DXF-visual-diff（sync_utils.py 使用）**

| ファイル | 同期方式 | 備考 |
|---|---|---|
| `extract_labels.py` | 適応的同期 | try/except パターンを維持すること |
| `compare_dxf.py` | 直接同期 | config依存なし |
| `label_diff.py` | 直接同期 | config依存なし |
| `common_utils.py` | 直接同期 | 微小な差異あり（要注意） |

**DXF-diff-manager → DXF-extract-labels（手動コピー）**

| ファイル | 同期方式 | 備考 |
|---|---|---|
| `extract_labels.py` | 手動コピー（ファイル同一化） | 変更後は `diff` で一致確認 |
| `common_utils.py` | 手動コピー（ファイル同一化） | 内容は完全同一 |

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

### 14.3 図面管理台帳への新しいカラムの追加

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

### 15.2 RevUpペアと流用ペアの共存

- RevUp 判定と流用判定は**独立した2パス**で実行される（auto / all_in_one モード共通）
- 完全に同一の（流用先, 流用元）ペアのみ重複排除し RevUp 側を残す
- 同一の流用先図番が RevUp ペアと流用ペアの**両方に登場し得る**（意図的な仕様）
- pair_list モードでは RevUp 自動検出は行わない（リスト定義のみに基づく）

### 15.3 流用元図番の必須要件

- 流用ペア（auto・all_in_one モード）は流用元図番が図面内に記載されている必要がある
- RevUpペアは流用元図番の記載がなくても自動検出される（ベース図番の一致で判定）
- pair_list モードは流用元図番の記載に依存せず、リストの定義のみに基づく

### 15.4 エンティティ数の記録タイミング

- エンティティ数は差分比較が完了したペアにのみ記録される
- 比較に失敗したペアには記録されない（台帳の該当行はエンティティ数カラムが空欄のまま）

### 15.5 メモリ使用量（Streamlit Community Cloud リソース制限対策、2026-06）

Streamlit Community Cloud の「This app has gone over its resource limits」warning は**アプリ（コンテナ）単位**でメモリ上限（690MB〜2.7GB）を共有する仕組みのため、同時接続セッションの合計使用量で発生し得る。本プロジェクトでの対策:

- 大量のDXFファイル（数十〜数百ファイル）を一度に処理する場合、メモリ消費に注意
- `compare_dxf_files_and_generate_dxf()` / `create_diff_zip()` では `del` + `gc.collect()` による明示的なガベージコレクションを各処理後に実行する
- **出力データの二重保持を解消**（[6.2](#62-セッション状態のキー一覧)参照）: `diff_labels.xlsx` / `unchanged_labels.xlsx` は `zip_data` 内にも同内容が含まれるため、session_state には複製を持たず `has_diff_labels` / `has_unchanged_labels` の bool フラグのみ保持し、プレビュー表示時に `read_zip_member()` で `zip_data` から都度読み出す
- それでも `zip_data` 自体（差分DXF・Excel一式を含む）は結果表示中ずっと1セッションぶん保持される。バッチが大きい（数十ペア×数MB〜十数MBのDXF）場合はそれだけでも相当量になるため、定期的なアプリ再起動（Streamlit Cloud の「Manage app」→ Reboot）や、利用規模が常態的に大きい場合は有料/自前ホスティングへの移行も検討対象

### 15.6 一時ファイルの残留

- `save_uploadedfile()` で作成される一時ファイルは `delete=False` のため自動削除されない（ファイル名には `TEMP_FILE_PREFIX="dxfdm_"` を付与、[Section 10](#10-utilscommon_utilspy-詳解)参照）
- `cleanup_temp_files()` が呼ばれるまで残留する（「🔄 新しい差分抽出を開始」ボタン押下時のみ）
- 対象辞書: `source_files_dict`, `dest_files_dict`, `all_files_dict`, `all_in_one_files_dict`
- 同じ図番への再アップロードで辞書エントリが上書きされる際、古い一時ファイルを `os.unlink()` してから上書きする（2026-06 修正。`process_all_uploaded_files()` 参照）
- リスタートを押さずに離脱した場合（タブを閉じる・タイムアウト等）は上記いずれでも回収されず OS の一時ディレクトリに残留する。これは `cleanup_stale_temp_files()`（新規セッション開始時に一度だけ実行、既定3時間超のファイルを削除）がセーフティネットとして回収する

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

旧実装では `dest_count == -1`（pair_list モード）・`dest_count == -2`（all_in_one モード）というセンチネル値を `render_step2_pairing()` に渡してモード判定していたが、現在は廃止済み。モードは `st.session_state.step1_mode` を直接参照して判断する。Step 2 の各関数は常に実際のファイル件数を返す。

### 15.10 LWPOLYLINE の closed 状態の保持（2026-06-17 修正）

`compare_dxf.py` の `OutputGenerator.create_entity_from_absolute()` では以前、3頂点以上の LWPOLYLINE に対して**無条件に `close()` を呼んでいた**。

**問題**: L 字型など 3 頂点の open な LWPOLYLINE（`flags=0`）を差分 DXF に書き出す際に `close()` が呼ばれ、始点と終点を結ぶ斜めの線分が追加されてしまっていた。元ファイルでは直角に折れ曲がった形状が差分ファイルでは三角形に変形して見える。

**修正**: `if len(vertex_points) >= 3: new_entity.close()` を `if attrs.get('flags', 0) & 1: new_entity.close()` に変更。`dxf.flags` の bit 0（= 1 で closed、0 で open）を元ファイルから `all_existing_dxf_attribs()` 経由で取得し、元の closed 状態を正確に引き継ぐ。

**副次修正**: LWPOLYLINE の `lineweight` も `attrs` から取得して保持するよう追加。

---

*最終更新: 2026-06-24（`utils.pairing.primary_status_by_drawing()` を追加し、同一の流用先図番が複数ステータスのペアに登場する場合（RevUp+流用の併存、ペアリストの重複行）の二重計上を排除。Step3の全セクション集計が main_drawing 単位で排他的になり、`差分抽出が可能なペア`+`流用元図番の図面がない図面`+`変更していない図面`(+完全新規図面) の合計が流用先総数と必ず一致するようにした（実データの sample-dxf/ME24-1001-0 でも確認）。モジュールのフォーマットを `1111`(数字4桁) から `XXXX`(英大文字または数字4桁) に修正）*

*過去の更新: 2026-06-24（用語統一: 「比較元/比較先」→「流用元/流用先」（ペアリストの旧カラム名は後方互換）。Step 1 を「既存アップロード/新規作成（指番・モジュール・サイドから台帳ファイル名を自動生成）/作成せず」の3択に再設計。Step 3 のペアリスト表示を全セクション「：N件」表記に統一し、「完全新規図面」「変更していない図面（流用元と流用先とで共通）」セクションを追加。Summaryシートの図面統計・総図形数ラベルをペアリング方式（Type A/B/C）別に変更）*

*過去の更新: 2026-06-18（Step 2/Step 4 高速化: INSERT展開スキップ・ローカル属性キャッシュ・バッチ内ファイル再利用キャッシュを追加。Streamlit Community Cloud リソース制限対策: 出力Excelの二重保持解消・一時ファイルの孤立防止と掃除も追加）*
