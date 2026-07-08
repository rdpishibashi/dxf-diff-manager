"""
utils.diff_export（UI 非依存）のユニットテスト。

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

from utils.diff_export import create_diff_zip


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
        old_doc = ezdxf.new()
        old_doc.modelspace().add_text('OLD_ONLY_LABEL', dxfattribs={'insert': (0, 0)})
        new_doc = ezdxf.new()
        new_doc.modelspace().add_text('NEW_ONLY_LABEL', dxfattribs={'insert': (100, 100)})

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

        zip_data, results, diff_labels_excel, unchanged_labels_excel, master_df = create_diff_zip(pairs)

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
