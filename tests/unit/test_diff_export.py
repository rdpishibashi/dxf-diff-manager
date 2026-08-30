"""
model.diff_export（UI 非依存）のユニットテスト。

streamlit に依存しないため app.py をインポートせず、コアを直接検証する
（tests/unit/test_pairing.py と同じ方針）。

実行:
    cd DXF-diff-manager
    python -m tests.unit.test_diff_export
"""
import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ezdxf
import pandas as pd

from model.diff_export import create_diff_zip
from model.master_ledger import create_empty_master_df, create_empty_drawing_list_df, MASTER_SHEET_NAME


def _make_pair_dxf_files(d, main_drawing, source_drawing,
                          new_only_label, old_only_label,
                          new_insert=(100, 100), old_insert=(0, 0)):
    """1ペア分の新旧DXFファイルを作成し、pairs用のdictを返す。

    new_only_label は main_drawing（新）側だけに、old_only_label は
    source_drawing（旧）側だけに配置する。
    """
    old_doc = ezdxf.new()
    old_doc.modelspace().add_text(old_only_label, dxfattribs={'insert': old_insert})
    new_doc = ezdxf.new()
    new_doc.modelspace().add_text(new_only_label, dxfattribs={'insert': new_insert})

    old_path = os.path.join(d, f'{source_drawing}.dxf')
    new_path = os.path.join(d, f'{main_drawing}.dxf')
    old_doc.saveas(old_path)
    new_doc.saveas(new_path)

    return {
        'main_drawing': main_drawing,
        'source_drawing': source_drawing,
        'main_file_info': {'temp_path': new_path, 'title': None, 'subtitle': None},
        'source_file_info': {'temp_path': old_path},
        'status': 'complete',
        'relation': 'RevUp',
        'title': None,
        'subtitle': None,
    }


def test_create_diff_zip_passes_old_new_in_correct_order_to_compare_dxf():
    """create_diff_zip() が compare_dxf_files_and_generate_dxf() に旧→新の順で
    ファイルを渡し、出力DXFの ADDED/DELETED レイヤーが正しい内容になることを保証する。

    実際に発生した不具合の回帰テスト: create_diff_zip() 内部で
    compare_dxf_files_and_generate_dxf(main_file_path, source_file_path, ...)
    （新, 旧の順）で呼んでいたため、file_a のみ→DELETED / file_b のみ→ADDED という
    関数の契約と逆転し、ADDED レイヤーに旧図面の内容、DELETED レイヤーに新図面の
    内容が出力されていた（実データ EE4144-613-49D_vs_49C で、ADDED に旧図番自身の
    テキスト 'EE4144-613-49C' が、DELETED に新図番自身のテキスト 'EE4144-613-49D' と
    新規追加された改訂メモが混入していた）。
    """
    with tempfile.TemporaryDirectory() as d:
        pair = _make_pair_dxf_files(
            d, 'NEW-001', 'OLD-001',
            new_only_label='NEW_ONLY_LABEL', old_only_label='OLD_ONLY_LABEL',
        )
        pairs = [pair]

        zip_data, results, diff_labels_excel, unchanged_labels_excel, master_df, drawing_list_df = create_diff_zip(pairs)

        assert len(results) == 1
        assert results[0]['success']
        # main(新)のみのラベルが ADDED、source(旧)のみのラベルが DELETED になるはず
        assert results[0]['entity_counts']['added_entities'] == 1
        assert results[0]['entity_counts']['deleted_entities'] == 1

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            dxf_names = [n for n in zf.namelist() if n.endswith('.dxf')]
            assert len(dxf_names) == 1
            dxf_bytes = zf.read(dxf_names[0])

        with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as f:
            f.write(dxf_bytes)
            out_path = f.name
        try:
            out_doc = ezdxf.readfile(out_path)
            by_layer = {}
            for e in out_doc.modelspace():
                if e.dxftype() == 'TEXT':
                    by_layer[getattr(e.dxf, 'layer', '')] = e.dxf.text

            assert by_layer.get('ADDED') == 'NEW_ONLY_LABEL', \
                f"ADDEDレイヤーに新図面のラベルが期待通り出力されていない: {by_layer}"
            assert by_layer.get('DELETED') == 'OLD_ONLY_LABEL', \
                f"DELETEDレイヤーに旧図面のラベルが期待通り出力されていない: {by_layer}"
        finally:
            os.unlink(out_path)

        # diff_labels.xlsx 側の New/Old も新旧が入れ替わっていないことを確認する
        # （compute_label_differences(new_file, old_file, ...) の呼び出し順序は今回の
        # 修正で変更していないが、同種の取り違えが将来起きないことを保証する）
        sheet_df = pd.read_excel(io.BytesIO(diff_labels_excel), sheet_name='NEW-001')
        new_col = 'New: NEW-001'
        old_col = 'Old: OLD-001'
        assert new_col in sheet_df.columns and old_col in sheet_df.columns
        new_values = set(sheet_df[new_col].dropna())
        old_values = set(sheet_df[old_col].dropna())
        assert 'NEW_ONLY_LABEL' in new_values, \
            f"diff_labelsのNew列に新図面のラベルが無い: {new_values}"
        assert 'OLD_ONLY_LABEL' in old_values, \
            f"diff_labelsのOld列に旧図面のラベルが無い: {old_values}"
        assert 'NEW_ONLY_LABEL' not in old_values and 'OLD_ONLY_LABEL' not in new_values, \
            "diff_labelsのNew/Oldが入れ替わっている"


def test_diff_labels_summary_and_sheets_sorted_alphabetically_by_drawing_number():
    """diff_labels.xlsx の Summary シート「図番」欄と個別シートの並びが図番の
    ABC順になることを確認する（処理順（ペアリスト順）のままだと順不同になるため）。
    あわせて unchanged_labels.xlsx のシート順も同じABC順に揃っていることを確認する
    （同一バッチから出力される2ファイルのシート順が食い違うと突き合わせにくいため、
    2026-07 に diff_labels 側のソートと合わせて対応）。
    """
    with tempfile.TemporaryDirectory() as d:
        # わざと図番の並び順とは異なる処理順で pairs を渡す
        pairs = [
            _make_pair_dxf_files(d, 'C-DRAW', 'C-SRC', 'C_NEW', 'C_OLD'),
            _make_pair_dxf_files(d, 'A-DRAW', 'A-SRC', 'A_NEW', 'A_OLD'),
            _make_pair_dxf_files(d, 'B-DRAW', 'B-SRC', 'B_NEW', 'B_OLD'),
        ]

        zip_data, results, diff_labels_excel, unchanged_labels_excel, master_df, drawing_list_df = create_diff_zip(pairs)
        assert len(results) == 3

        xl = pd.ExcelFile(io.BytesIO(diff_labels_excel))

        # 個別シートの並び（Summary の次から）が ABC 順になっていること
        pair_sheet_names = [n for n in xl.sheet_names if n != 'Summary']
        assert pair_sheet_names == ['A-DRAW', 'B-DRAW', 'C-DRAW'], \
            f"個別シートの並びがABC順になっていない: {pair_sheet_names}"

        # Summary シートの「図番」欄が ABC 順になっていること
        summary_df = pd.read_excel(xl, sheet_name='Summary')
        assert summary_df['図番'].tolist() == ['A-DRAW', 'B-DRAW', 'C-DRAW'], \
            f"Summaryシートの図番欄がABC順になっていない: {summary_df['図番'].tolist()}"

        # Summary の各行のハイパーリンクが、並び替え後も対応する図番のシートを
        # 正しく指していること（summary_data と diff_label_sheets の対応がソートで
        # 崩れていないことの確認）
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(diff_labels_excel))
        ws = wb['Summary']
        for row_idx, expected in enumerate(['A-DRAW', 'B-DRAW', 'C-DRAW'], start=2):
            cell = ws.cell(row=row_idx, column=1)
            assert cell.value == expected
            assert cell.hyperlink is not None
            assert cell.hyperlink.location == f"'{expected}'!A1", \
                f"{expected}行のハイパーリンクが対応するシートを指していない: {cell.hyperlink.location}"

        # unchanged_labels.xlsx のシート順も diff_labels.xlsx と同じABC順であること
        unchanged_xl = pd.ExcelFile(io.BytesIO(unchanged_labels_excel))
        assert unchanged_xl.sheet_names == ['A-DRAW', 'B-DRAW', 'C-DRAW'], \
            f"unchanged_labels.xlsxのシート順がdiff_labels.xlsxと食い違っている: {unchanged_xl.sheet_names}"


def test_ignore_moved_labels_combined_with_prefix_filter_and_ref_designator_check():
    """ignore_moved_labels（移動しただけのラベルを差分から除外）が、既存の
    「未変更ラベル先頭文字列フィルタ」（prefixes）・「機器符号妥当性チェック」
    （validate_ref_designators/filter_non_parts）と組み合わさっても正しく動作する。

    dev-workflow スキルの選択肢組み合わせ表で「影響あり→要テスト」と判定した
    組み合わせの回帰テスト。移動したラベル（旧座標→新座標、件数一致）が
    prefixフィルタに一致する場合、diff_labels.xlsx からは除外されつつ、
    unchanged_labels.xlsx（filter_unchanged_by_prefix 経由）には正しく残ることを
    end-to-end（create_diff_zip 経由）で確認する。
    """
    with tempfile.TemporaryDirectory() as d:
        # 'R10' という機器符号ラベルが座標だけ変わって移動したペアを用意
        old_doc = ezdxf.new()
        old_doc.modelspace().add_text('R10', dxfattribs={'insert': (0, 0)})
        new_doc = ezdxf.new()
        new_doc.modelspace().add_text('R10', dxfattribs={'insert': (100, 100)})
        old_path = os.path.join(d, 'OLD-001.dxf')
        new_path = os.path.join(d, 'NEW-001.dxf')
        old_doc.saveas(old_path)
        new_doc.saveas(new_path)

        pairs = [{
            'main_drawing': 'NEW-001',
            'source_drawing': 'OLD-001',
            'main_file_info': {'temp_path': new_path, 'title': None, 'subtitle': None},
            'source_file_info': {'temp_path': old_path},
            'status': 'complete',
            'relation': 'RevUp',
            'title': None,
            'subtitle': None,
        }]

        zip_data, results, diff_labels_excel, unchanged_labels_excel, master_df, drawing_list_df = create_diff_zip(
            pairs,
            ignore_moved_labels=True,
            prefixes=['R'],
            validate_ref_designators=True,
        )

        assert results[0]['success']

        # diff_labels.xlsx: 移動したラベルは変更候補から除外されているはず
        sheet_df = pd.read_excel(io.BytesIO(diff_labels_excel), sheet_name='NEW-001')
        assert len(sheet_df) == 0, f"移動したラベルが変更候補として残っている: {sheet_df}"

        # unchanged_labels.xlsx: 移動先座標で 'R10' が prefix フィルタを通って残っているはず
        unchanged_df = pd.read_excel(io.BytesIO(unchanged_labels_excel), sheet_name='NEW-001')
        assert list(unchanged_df['Label']) == ['R10'], \
            f"移動したラベルがunchanged_labels.xlsxに正しく残っていない: {unchanged_df}"
        assert unchanged_df.iloc[0]['Coordinate X'] == 100.0  # 新座標を採用


def test_create_diff_zip_records_drawing_list_for_successful_pair():
    """成功した complete ペアは Drawing List に Sashiban/Module/Side（台帳ファイル名から
    逆算）・Child/Parent・解決済みTitle/Subtitleとともに記録される。"""
    with tempfile.TemporaryDirectory() as d:
        pair = _make_pair_dxf_files(d, 'NEW-001', 'OLD-001', 'NEW_ONLY', 'OLD_ONLY')
        _, _, _, _, master_df, drawing_list_df = create_diff_zip(
            [pair], master_df=create_empty_master_df(),
            master_filename='AA11-1111-1_ZM00_405.xlsx',
            drawing_list_df=create_empty_drawing_list_df(),
        )
        row = drawing_list_df[drawing_list_df['Child Drawing Number'] == 'NEW-001'].iloc[0]
        assert row['Parent Drawing Number'] == 'OLD-001'
        assert row['Sashiban'] == 'AA11-1111-1'
        assert row['Module'] == 'ZM00'
        assert row['Side'] == '405'


def test_create_diff_zip_records_drawing_list_even_when_pair_processing_fails():
    """失敗した complete ペア（DXF比較失敗）もDrawing Listには記録される
    （Masterは成功ペアのみだが、Drawing Listは成否を問わず全件対象という仕様）。"""
    with tempfile.TemporaryDirectory() as d:
        pair = {
            'main_drawing': 'NEW-BAD', 'source_drawing': 'OLD-MISSING-FILE',
            'main_file_info': {'temp_path': os.path.join(d, 'NEW-BAD.dxf'), 'title': None, 'subtitle': None},
            'source_file_info': {'temp_path': os.path.join(d, 'does_not_exist.dxf')},
            'status': 'complete', 'relation': '流用', 'title': None, 'subtitle': None,
        }
        new_doc = ezdxf.new()
        new_doc.modelspace().add_text('X', dxfattribs={'insert': (0, 0)})
        new_doc.saveas(pair['main_file_info']['temp_path'])

        _, results, _, _, master_df, drawing_list_df = create_diff_zip(
            [pair], master_df=create_empty_master_df(),
            master_filename='AA11-1111-1_ZM00_405.xlsx',
            drawing_list_df=create_empty_drawing_list_df(),
            on_error=lambda msg: None,
        )
        assert results[0]['success'] is False
        row = drawing_list_df[drawing_list_df['Child Drawing Number'] == 'NEW-BAD'].iloc[0]
        assert row['Parent Drawing Number'] == 'OLD-MISSING-FILE'


def test_create_diff_zip_records_drawing_list_for_missing_source_pair():
    """流用元ファイル未アップロード（missing_source）のペアも Drawing List に記録され、
    流用先ファイルからTitleが直接抽出される（pair_extracted_infoに無いためフォールバック）。"""
    with tempfile.TemporaryDirectory() as d:
        new_doc = ezdxf.new()
        new_doc.modelspace().add_text('X', dxfattribs={'insert': (0, 0)})
        new_path = os.path.join(d, 'NEW-002.dxf')
        new_doc.saveas(new_path)

        pair = {
            'main_drawing': 'NEW-002', 'source_drawing': 'OLD-002',
            'main_file_info': {'temp_path': new_path, 'title': None, 'subtitle': None},
            'source_file_info': None,
            'status': 'missing_source', 'relation': '流用', 'title': None, 'subtitle': None,
        }
        _, _, _, _, master_df, drawing_list_df = create_diff_zip(
            [pair], master_df=create_empty_master_df(),
            master_filename='AA11-1111-1_ZM00_405.xlsx',
            drawing_list_df=create_empty_drawing_list_df(),
        )
        row = drawing_list_df[drawing_list_df['Child Drawing Number'] == 'NEW-002'].iloc[0]
        assert row['Parent Drawing Number'] == 'OLD-002'


def test_create_diff_zip_does_not_overwrite_existing_drawing_list_entry():
    """既にDrawing Listに登録済みのChild Drawing Numberは、同じ図番が再処理されても
    上書きされない（新規のChild Drawing Numberのみ追加、という仕様）。"""
    with tempfile.TemporaryDirectory() as d:
        pair = _make_pair_dxf_files(d, 'NEW-001', 'OLD-001', 'NEW_ONLY', 'OLD_ONLY')

        existing_drawing_list = create_empty_drawing_list_df()
        existing_drawing_list.loc[0] = {
            'Sashiban': 'ZZ99-9999-9', 'Module': 'na', 'Side': 'na',
            'Child Drawing Number': 'NEW-001', 'Parent Drawing Number': 'ORIGINAL-PARENT',
            'Title': 'ORIGINAL-TITLE', 'Subtitle': None, 'Recorded Date': None,
        }

        _, _, _, _, master_df, drawing_list_df = create_diff_zip(
            [pair], master_df=create_empty_master_df(),
            master_filename='AA11-1111-1_ZM00_405.xlsx',
            drawing_list_df=existing_drawing_list,
        )
        assert len(drawing_list_df) == 1
        row = drawing_list_df.iloc[0]
        assert row['Parent Drawing Number'] == 'ORIGINAL-PARENT'
        assert row['Title'] == 'ORIGINAL-TITLE'
        assert row['Sashiban'] == 'ZZ99-9999-9'  # 元の指番も保持される


def test_create_diff_zip_drawing_list_blank_shiban_when_master_filename_unresolvable():
    """台帳ファイル名が命名規則に一致しない場合、新規追加行のSashiban/Module/Sideは空欄。"""
    with tempfile.TemporaryDirectory() as d:
        pair = _make_pair_dxf_files(d, 'NEW-001', 'OLD-001', 'NEW_ONLY', 'OLD_ONLY')
        _, _, _, _, master_df, drawing_list_df = create_diff_zip(
            [pair], master_df=create_empty_master_df(),
            master_filename='my_free_named_master.xlsx',
            drawing_list_df=create_empty_drawing_list_df(),
        )
        row = drawing_list_df[drawing_list_df['Child Drawing Number'] == 'NEW-001'].iloc[0]
        assert row['Sashiban'] == ''
        assert row['Module'] == ''
        assert row['Side'] == ''


def test_create_diff_zip_drawing_list_written_to_master_excel():
    """create_diff_zip() の出力台帳Excelに Drawing List シートが Master の後ろに含まれる。"""
    with tempfile.TemporaryDirectory() as d:
        pair = _make_pair_dxf_files(d, 'NEW-001', 'OLD-001', 'NEW_ONLY', 'OLD_ONLY')
        zip_data, _, _, _, _, _ = create_diff_zip(
            [pair], master_df=create_empty_master_df(),
            master_filename='AA11-1111-1_ZM00_405.xlsx',
            drawing_list_df=create_empty_drawing_list_df(),
        )
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            master_bytes = zf.read('AA11-1111-1_ZM00_405.xlsx')
        xl = pd.ExcelFile(io.BytesIO(master_bytes))
        assert xl.sheet_names == ['Summary', MASTER_SHEET_NAME, 'Drawing List']
        dl = pd.read_excel(xl, sheet_name='Drawing List')
        assert list(dl['Child Drawing Number']) == ['NEW-001']


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append(t.__name__); print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
