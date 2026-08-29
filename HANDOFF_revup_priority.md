# 引き継ぎ書 — RevUpと流用が競合する場合はRevUpのみ採用

## 現在地
- プロジェクト: `/Users/ryozo/Dropbox/Client/ULVAC/ElectricDesignManagement/Tools/DXF-diff-manager`
- ブランチ: `feature/revup-priority-over-reuse`（`main` から作成）
- ベースラインタグ: `baseline-20260829`（base commit: `b009ae8`）
- ベースラインテスト結果: `python -m pytest tests/unit tests/regression -q -rs`
  → **131 passed, 0 skipped, 3.31s**（2026-08-29 取得）
- 直前に `git reset --hard origin/main` でローカルをリモートに同期済み（未コミットだった
  変更は全て origin と内容一致していたことを確認済み・別セッションで既にpush済みだった）。
  `TECHNICAL.md`・`Archive/*.md` はバックアップから復元済み（`.gitignore` 対象のローカル専用ファイル）。

## 目的・スコープ（ユーザー要求の原文要約）
Drawing-genealogy プロジェクトでは「流用と RevUp が競合する場合は、流用関係を削除して
RevUp だけを採用する」仕様が既に実装済み。DXF-diff-manager でも、**ペアリスト作成**
（Step3）と**差分抽出**（Step4）の両方に同じルールを適用してほしい、というユーザー要求。

## 参考実装（Drawing-genealogy、変更しない・読むだけ）
`Drawing-genealogy/utils/graph_builder.py`:
- `GraphBuilder._reuse_pairs_to_delete()`: あるノード（Child）が流用の入力エッジと
  RevUp の入力エッジ（明示行・推測破線いずれも）の両方を持つ場合、その Child への
  流用エッジを**全て**削除し、RevUp 側のみ残す。
- `get_edges()` / `get_display_data()` の両方に適用される。

## 確定した設計判断
1. **修正箇所は `model/pairing.py` の `build_pairs()` 一箇所に閉じる。**
   Step3 表示（`app.py` の `render_pair_list()`）・台帳更新（`update_parent_child_master()`）・
   差分抽出（`create_diff_zip()` の `complete_pairs` フィルタ）・Drawing List・
   Summary 集計（`save_master_to_bytes()` の `pair_count`/`流用率`）は、いずれも
   `build_pairs()` が返す `pairs` リストをそのまま消費するだけで、独自に
   RevUp/流用の重複判定をしていない。**そのため `build_pairs()` の出力から
   競合する流用ペアを除去すれば、下流は全て自動的に正しくなる**
   （`app.py`・`diff_export.py`・`master_ledger.py` は変更不要）。
2. **対象は Type A（all_in_one）・Type B（auto）のみ。** 両方とも `build_pairs()` を
   呼んでいる（`build_pairs(pool, pool)` / `build_pairs(source_files, dest_files)`）。
3. **Type C（pair_list、`build_pairs_from_list()`）はスコープ外。** TECHNICAL.md 3.3.1
   に明記の通り「Type Cはrelationが常に'ペアリスト'でRevUpという概念が無い」——
   RevUp自動推測を行わないため、この競合は原理的に発生しない。
4. **具体的な修正**: `build_pairs()` の「1. RevUp パス」で `find_revup_pairs()` が返す
   `used_target`（RevUp ペアが作られた main_drawing の集合）を、「2. 流用 パス」の
   ループ条件に追加する。
   ```python
   if source_drawing and source_drawing != main_drawing and main_drawing not in used_target:
       ...（流用ペアを作成）
   ```
   これにより、RevUp が既に存在する main_drawing については 流用 パスで一切ペアを
   追加しない（=削除ではなく「そもそも作らない」。Drawing-genealogy は一度作ってから
   削除するアプローチだが、DXF-diff-manager は生成順序を利用してより単純に実現できる）。
5. **`paired_drawings` セット・`pair_keys` セットへの追加処理は変更不要。**
   RevUp パスで既に `paired_drawings.add(main_drawing)` 済みのため、孤立(orphan)判定
   （3. 孤立パス）には影響しない。

## 影響範囲の組み合わせ表（検討済み）

| モード | RevUp あり | 流用 あり | 現行 | 変更後 |
|---|---|---|---|---|
| Type A/B | Yes | Yes | 両方が pairs に残る（重複処理） | 流用のみ除去、RevUp のみ残る |
| Type A/B | Yes | No | RevUp のみ（元々単一） | 変更なし |
| Type A/B | No | Yes | 流用のみ（元々単一） | 変更なし |
| Type A/B | No | No | no_source_defined（孤立） | 変更なし |
| Type C | — | — | RevUp概念なし | スコープ外・変更なし |

下流影響（全て `pairs` を受動的に消費するため自動的に解消）:
- Step3 表示: 同一 main_drawing が複数セクションに二重表示される問題が解消
- `create_diff_zip()`: 同一図面に対して2回差分抽出が走っていたケース（RevUp相手・
  流用元の両方のファイルがアップロード済みの場合）が解消され、1回のみに
- 台帳（Diff List）: 同一 Child に対し Parent違いで2行登録されていたケースが解消
- Summary「差分抽出ペア数」「流用率 [%]」: 上記の二重カウントが解消され正確になる

## 触ってはいけないもの
- Drawing-genealogy 側のコード・テストは一切変更しない（参考にするだけ）。
- Type C（`build_pairs_from_list()`）のロジックは変更しない。
- `app.py`・`model/diff_export.py`・`model/master_ledger.py` は変更不要（想定通りなら）。
  ただし念のため、修正後に Step3/Step4 のブラックボックス確認は行うこと。

## 更新が必要なテスト（ファイル別・具体的に）

### `tests/unit/test_pairing.py`
- `test_build_pairs_single_pool_same_target_twice`（102行目付近）:
  `assert rels == {RELATION_REVUP, RELATION_DEPENDENCY}` →
  `assert rels == {RELATION_REVUP}` に変更（流用ペアは生成されなくなる）。
- `test_build_pairs_single_pool_revup_when_source_missing`（92行目付近）:
  2つ目の assert `('EE6333-365-61C', 'EE6331-365-61A') in _keys(pairs, status=STATUS_MISSING_SOURCE)`
  を「存在しない」ことを確認するアサーションに変更。関数名・docstring も実態に
  合わせて更新を検討（例: `test_build_pairs_single_pool_revup_wins_over_missing_source`）。
- `test_build_pairs_auto_independent_passes`（128行目付近）: 同上のパターン
  （mode B 版）。関数名も見直しを検討。
- `test_primary_status_prefers_complete_over_missing_source_revup_case`（194行目付近）:
  アサーション自体（`primary == {'X002B': STATUS_COMPLETE}`）は変更後も成立するはずだが、
  「重複ペアの優先順位付け」という docstring の意図と実態がずれる
  （変更後は重複自体が発生しないため）。docstring を更新するか、このテストの意図を
  duplicate-target-row（Type C側、203行目）のみに絞ることを検討。

### `tests/regression/test_auto_revup.py`
- `test_revup_detected_independently`（54行目）: 2つ目の assert（missing_source が
  残ることを確認）を「残らない（流用ペアが作られない）」ことを確認するよう変更。
  ファイル冒頭のdocstring（背景説明、1-8行目）も「流用判定とRevUp判定を独立実行し
  両方出力する」という記述が実態と変わるため更新が必要。
- `test_same_target_appears_in_both`（67行目）: `rels == {'RevUp', '流用'}` は
  もう成立しない。`rels == {'RevUp'}` に変更し、テスト名・docstringも
  「競合時はRevUpのみ採用されることを確認する」意図に変更
  （L2の不具合再発防止テストとして残す）。

### `tests/regression/test_single_pool_revup.py`
- `test_revup_detected_when_source_missing`（41行目）: 同上パターン（Type A版）。
  2つ目の assert を変更。
- `test_same_target_can_appear_multiple_times`（61行目）: 同上（`{'RevUp', '流用'}` →
  `{'RevUp'}`）。

**新規テストの追加を検討**: 「流用元・RevUp相手の両方のファイルが揃っている場合
（両方 complete）に、流用側が完全に消え、diff抽出も1回だけになる」という、今回の
主眼（二重の差分抽出防止）を直接検証するテストケースが現状の一覧には無い
（既存テストは主に片方が missing_source のケース）。`build_pairs()` に対して
`test_build_pairs_single_pool_same_target_twice` の派生として、両方 complete な
pool を使い `len([p for p in pairs if main_drawing==X])==1` を確認するテストを
1本追加することを推奨（2-5「既存テストの拡張で足りないか」を優先しつつ、
守る対象が異なるため新規関数が妥当）。

## テスト方針
- 単体テスト: `tests/unit/test_pairing.py` を優先的に実行・拡張。
- 回帰テスト: `tests/regression/test_auto_revup.py` / `test_single_pool_revup.py` を
  修正後、ユーザーに実行許可を確認してから実行（dev-workflowスキルの方針通り）。
- ブラックボックス確認: 可能であれば `sample-dxf` 内の実データで Step3/Step4を
  一通り操作し、二重処理が解消されていることを目視確認。

## 更新が必要な文書（`ls *.md docs/*.md` 実行結果）
```
LICENSE (対象外)
TECHNICAL.md
HANDOFF_revup_priority.md（このファイル自身。作業完了後は削除する）
```
`docs/` フォルダはこのプロジェクトに存在しない。

### TECHNICAL.md の更新箇所（該当行、実装後に正確な行番号を再確認すること）
- 3.3「自動ペアリングの判定（auto モード）」: 「同一の流用先図番が RevUp ペア・
  流用ペアの双方に登場し得る（意図的な仕様）」という記述を、新仕様
  （RevUp優先・流用削除）に書き換える。
- 3.3.1「一括ペアリングの判定（all_in_one モード）」: 同上。
- `model/pairing.py` の関数一覧（`build_pairs`）の説明も更新。
- 末尾の更新履歴に追記。

## 守るべきゲート
- 2-5: 組み合わせ表の「変更後」列に対応するテストケースが揃っているか確認してから
  完了扱いにする。
- 回帰テスト実行前に必ずユーザーに確認する。
- 3-2: `TECHNICAL.md` を **feature ブランチ上で** 更新してから 3-3（main マージ）に進む。
- 3-3 直前に `git fetch origin` して diverged していないか再確認する
  （このプロジェクトは 21 コミット遅れていた実績があるため特に注意）。
- 3-4: 作業完了後、このファイル（`HANDOFF_revup_priority.md`）自体を削除すること
  （中間生成物）。

## 未解決の疑問点
特になし。設計判断は全て上記の通り確定している。
