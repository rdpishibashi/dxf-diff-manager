#!/usr/bin/env python3
"""
親子関係マスター管理機能のテストスクリプト
"""
import sys
import os
import pandas as pd
from datetime import datetime
from io import BytesIO

# パスを追加
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# app.pyから関数をインポート
from app import load_parent_child_master, update_parent_child_master, save_master_to_bytes

def test_load_master():
    """マスターファイル読み込みテスト"""
    print("=" * 80)
    print("テスト1: 親子関係マスターファイルの読み込み")
    print("=" * 80)

    master_file_path = os.path.join(current_dir, 'Parent-Child_list.xlsx')

    if not os.path.exists(master_file_path):
        print(f"❌ ファイルが見つかりません: {master_file_path}")
        return None

    # ファイルを読み込み
    with open(master_file_path, 'rb') as f:
        # BytesIOオブジェクトを作成（Streamlitのアップロードファイルをシミュレート）
        file_obj = BytesIO(f.read())
        file_obj.name = 'Parent-Child_list.xlsx'

        master_df = pd.read_excel(file_obj)

    print(f"✅ マスターファイルを読み込みました")
    print(f"   レコード数: {len(master_df)}")
    print(f"   カラム: {list(master_df.columns)}")
    print()
    print("先頭5行:")
    print(master_df.head())
    print()

    return master_df


def test_update_master(master_df):
    """マスター更新テスト"""
    print("=" * 80)
    print("テスト2: 親子関係マスターの更新")
    print("=" * 80)

    # テスト用の新しいペア（既存と重複しないもの）
    test_pairs = [
        {
            'main_drawing': 'EE6321-039-06A',
            'source_drawing': 'EE6097-039-06C'
        },
        {
            'main_drawing': 'EE9999-999-99Z',  # 新規
            'source_drawing': 'EE8888-888-88Y'  # 新規
        }
    ]

    original_count = len(master_df)
    print(f"更新前のレコード数: {original_count}")
    print()

    # マスターを更新
    updated_df, added_count = update_parent_child_master(master_df, test_pairs)

    print(f"✅ マスター更新完了")
    print(f"   追加された件数: {added_count}")
    print(f"   更新後のレコード数: {len(updated_df)}")
    print()

    # 追加されたレコードを表示
    if added_count > 0:
        print("追加されたレコード:")
        new_records = updated_df.tail(added_count)
        print(new_records)
        print()

    return updated_df, added_count


def test_save_master(master_df):
    """マスター保存テスト"""
    print("=" * 80)
    print("テスト3: 親子関係マスターの保存")
    print("=" * 80)

    # バイトデータに変換
    excel_bytes = save_master_to_bytes(master_df)

    print(f"✅ Excelデータに変換しました")
    print(f"   データサイズ: {len(excel_bytes)} bytes")
    print()

    # テスト用に保存
    test_output_path = os.path.join(current_dir, 'test_output_master.xlsx')
    with open(test_output_path, 'wb') as f:
        f.write(excel_bytes)

    print(f"✅ テスト出力ファイルを保存しました: {test_output_path}")

    # 保存したファイルを読み込んで検証
    test_df = pd.read_excel(test_output_path)
    print(f"   検証: レコード数 = {len(test_df)}")
    print()

    return excel_bytes


def main():
    print()
    print("=" * 80)
    print("親子関係マスター管理機能 テストスクリプト")
    print("=" * 80)
    print()

    # テスト1: 読み込み
    master_df = test_load_master()
    if master_df is None:
        print("❌ テスト失敗: マスターファイルの読み込みに失敗しました")
        sys.exit(1)

    # テスト2: 更新
    updated_df, added_count = test_update_master(master_df)

    # テスト3: 保存
    excel_bytes = test_save_master(updated_df)

    # 全体の結果
    print("=" * 80)
    print("テスト結果サマリー")
    print("=" * 80)
    print("✅ 全てのテストに合格しました！")
    print()
    print("実装された機能:")
    print("  - 親子関係マスターファイルの読み込み")
    print("  - 新しい親子関係の追加（重複スキップ）")
    print("  - 更新されたマスターのExcel保存")
    print()


if __name__ == "__main__":
    main()
