"""
完全新規図面（流用元の参照がない図面）のDXFファイル出力の仕様テスト
（model.diff_export.create_diff_zip 経由、model.compare_dxf.generate_all_added_dxf 使用）。

受入条件（2026-09、ユーザー要求「ダウンロードするDXFファイルに完全新規図面も追加する」）:
    - 出力ファイル名は "{図番}_vs_none.dxf"。
    - 中身は、その図面の全図形要素を ADDED レイヤーに配置したもの
      （DELETED・UNCHANGED レイヤーは定義のみで中身は空）。
    - 図面管理台帳の作成有無（master_df の有無）に関わらず出力される
      （旧仕様では master_df is not None のときしかエンティティ数を算出せず、
      「台帳を作成しない」を選んだ場合はDXFすら出力されなかった）。
    - 台帳を作成する場合、Added Entities = Total Entities として登録される
      （count_entities_in_dxf_file() と同じ定義。model/master_ledger.py 参照）。

受入条件（2026-09、追加ユーザー要求「diff_labels.xlsx には完全新規図面についても
シートを作成してください。"New" だけになるのは問題ありません。Summaryシートにも
記載してください」）:
    - diff_labels.xlsx に完全新規図面のシート（図番名）が作成される。
    - シートは New 列のみ値が入り、Old 列は常に空。
    - Summary シートにも完全新規図面の行が追加される（流用元図番="none"、
      削除ラベル数=0、変更ラベル数=0、追加ラベル数=シートの行数）。
    - 他の通常ペアと同じ図番ABC順で並ぶ（Summaryの図番欄・シート順とも）。

受入条件（2026-09-16、追加ユーザー要求「機器符号候補列を追加」）:
    - ペアシートの先頭列に「機器符号候補」列が追加される（完全新規図面のシートも
      対象。Old列が常に空のため実質 New Label のみで判定される）。
    - Summary シートに「機器符号候補数」列が追加される。

実行:
    cd DXF-diff-manager
    python -m tests.regression.spec.test_brand_new_drawing_dxf_output
"""
import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf
import pandas as pd

from model.diff_export import create_diff_zip
from model.pairing import build_pairs_from_list
from model.master_ledger import create_empty_master_df


def _build_brand_new_pairs(d, drawing_number='BRANDNEW-001', entity_count=2, labels=None):
    """1件の完全新規図面ペア（流用元図番が空白）を作成する。

    labels を渡すと、その文字列をテキストラベルとして配置する（entity_count は
    無視される）。省略時は 'LABEL0', 'LABEL1', ... を entity_count 件配置する。
    """
    doc = ezdxf.new()
    if labels is not None:
        for i, label in enumerate(labels):
            doc.modelspace().add_text(label, dxfattribs={'insert': (i * 10, 0)})
    else:
        for i in range(entity_count):
            doc.modelspace().add_text(f'LABEL{i}', dxfattribs={'insert': (i * 10, 0)})
    path = os.path.join(d, f'{drawing_number}.dxf')
    doc.saveas(path)

    all_files_dict = {drawing_number: {'temp_path': path, 'title': None, 'subtitle': None, 'filename': f'{drawing_number}.dxf'}}
    df = pd.DataFrame({'流用元図番': [''], '流用先図番': [drawing_number]})
    return build_pairs_from_list(df, all_files_dict)


def _read_layer_counts(dxf_bytes):
    with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as f:
        f.write(dxf_bytes)
        path = f.name
    try:
        doc = ezdxf.readfile(path)
        counts = {}
        for e in doc.modelspace():
            layer = getattr(e.dxf, 'layer', '')
            counts[layer] = counts.get(layer, 0) + 1
        return counts
    finally:
        os.unlink(path)


def test_brand_new_drawing_dxf_filename_and_layers():
    """出力ファイル名が '{図番}_vs_none.dxf'、全要素がADDEDレイヤーに配置される。"""
    with tempfile.TemporaryDirectory() as d:
        pairs = _build_brand_new_pairs(d, 'BRANDNEW-001', entity_count=2)
        zip_data, results, _, _, _ = create_diff_zip(pairs, step1_mode='pair_list')

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            names = zf.namelist()
            assert 'BRANDNEW-001_vs_none.dxf' in names, f"想定ファイル名が無い: {names}"
            dxf_bytes = zf.read('BRANDNEW-001_vs_none.dxf')

        counts = _read_layer_counts(dxf_bytes)
        # 2026-09-18、オフセット補正機能の組み込みに伴いレイヤー名がOLD/NEW接頭辞付きに
        # 変更された（ADDED→NEW_ADDED、DELETED→OLD_DELETED）。NEW_ADDEDはNEW_ALLにも
        # 複製されるため、そちらの件数も併せて確認する。
        assert counts.get('NEW_ADDED') == 2, f"NEW_ADDEDレイヤーの要素数が想定と異なる: {counts}"
        assert counts.get('NEW_ALL') == 2, f"NEW_ALLレイヤーの要素数が想定と異なる: {counts}"
        assert counts.get('OLD_DELETED', 0) == 0, f"OLD_DELETEDレイヤーに要素が残っている: {counts}"
        assert counts.get('UNCHANGED', 0) == 0, f"UNCHANGEDレイヤーに要素が残っている: {counts}"
        assert counts.get('OLD_ALL', 0) == 0, f"OLD_ALLレイヤーに要素が残っている（比較対象が無いため空のはず）: {counts}"


def test_brand_new_drawing_dxf_output_without_master_df():
    """図面管理台帳を作成しない（master_df=None）場合でも、完全新規図面のDXFは出力される
    （2026-09 の変更点。旧仕様では master_df is not None のときしか出力されなかった）。
    """
    with tempfile.TemporaryDirectory() as d:
        pairs = _build_brand_new_pairs(d, 'BRANDNEW-002')
        zip_data, results, _, master_df, _ = create_diff_zip(
            pairs, master_df=None, step1_mode='pair_list',
        )
        assert master_df is None  # 台帳は作成されない

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            names = zf.namelist()
            assert 'BRANDNEW-002_vs_none.dxf' in names, \
                f"master_df=Noneでも完全新規図面のDXFが出力されるべき: {names}"

        brand_new_results = [r for r in results if r['relation'] == '完全新規図面']
        assert len(brand_new_results) == 1
        assert brand_new_results[0]['main_drawing'] == 'BRANDNEW-002'
        assert brand_new_results[0]['source_drawing'] == 'none'
        assert brand_new_results[0]['success'] is True


def test_brand_new_drawing_registered_in_master_with_added_equals_total():
    """図面管理台帳を作成する場合、Added Entities = Total Entities として登録される。"""
    with tempfile.TemporaryDirectory() as d:
        pairs = _build_brand_new_pairs(d, 'BRANDNEW-003', entity_count=3)
        _, _, _, master_df, _ = create_diff_zip(
            pairs, master_df=create_empty_master_df(), step1_mode='pair_list',
        )
        row = master_df[master_df['Child'] == 'BRANDNEW-003'].iloc[0]
        assert row['Parent'] == 'none'
        assert row['Added Entities'] == 3
        assert row['Total Entities'] == 3
        assert row['Deleted Entities'] == 'n/a'


def test_brand_new_drawing_has_diff_labels_sheet_with_new_only():
    """diff_labels.xlsx に完全新規図面のシートが作成され、New列のみ値が入る
    （Old列は常に空）。Summaryシートにも図番・流用元図番="none"・削除ラベル数=0・
    変更ラベル数=0・追加ラベル数=シート行数で記録される。
    機器符号候補列（ペアシート先頭・Summaryの「機器符号候補数」）も出力される
    （2026-09-16。'R10' は機器符号候補パターンに一致し、'LABEL_A' は
    アンダースコアを含むためどの候補パターンにも一致しない）。"""
    with tempfile.TemporaryDirectory() as d:
        pairs = _build_brand_new_pairs(d, 'BRANDNEW-004', labels=['LABEL_A', 'R10'])
        _, results, diff_labels_excel, _, _ = create_diff_zip(pairs, step1_mode='pair_list')

        assert results[0]['success']

        xl = pd.ExcelFile(io.BytesIO(diff_labels_excel))
        assert 'BRANDNEW-004' in xl.sheet_names, f"完全新規図面のシートが無い: {xl.sheet_names}"

        sheet_df = pd.read_excel(xl, sheet_name='BRANDNEW-004')
        assert list(sheet_df.columns) == ['機器符号候補', 'X', 'Y', 'Old: none', 'New: BRANDNEW-004']
        assert sheet_df['Old: none'].isna().all(), "Old列に値が入っている（Newのみのはず）"
        assert set(sheet_df['New: BRANDNEW-004']) == {'LABEL_A', 'R10'}

        r10_row = sheet_df[sheet_df['New: BRANDNEW-004'] == 'R10'].iloc[0]
        assert r10_row['機器符号候補'] == 'Y'
        label_a_row = sheet_df[sheet_df['New: BRANDNEW-004'] == 'LABEL_A'].iloc[0]
        assert pd.isna(label_a_row['機器符号候補'])

        summary_df = pd.read_excel(xl, sheet_name='Summary')
        row = summary_df[summary_df['図番'] == 'BRANDNEW-004'].iloc[0]
        assert row['流用元図番'] == 'none'
        assert row['削除ラベル数'] == 0
        assert row['変更ラベル数'] == 0
        assert row['追加ラベル数'] == 2
        assert row['機器符号候補数'] == 1


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append(t.__name__)
            print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
