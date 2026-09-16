"""
Step2 の DXFファイルアップロード（app.process_all_uploaded_files）に関する
2件の回帰テスト。

不具合の識別子: 2026-09-16 ユーザー報告
    「フォルダに22ファイル（DXF21件+xlsx1件）を入力したのに、
    『入力23件中、DXFファイルとして21件を読み込みました』と表示された」

以前どう壊れていたか:
    1. 図番フォーマットに一致しないファイルは `failures_key`（アップロード
       失敗一覧）にも記録されず、完全にサイレントに捨てられていた。入力総数
       (total_input) と実際にDXFとして読み込まれた件数 (processed) の差分が
       何だったのか、ユーザーは一切確認できなかった。
    2. `group_results` は「一致ファイルがある group」だけで初期化されていたため、
       複数グループ（Type B/auto の流用元・流用先）のうち一方のグループの
       一致が0件だと、後段の `res = group_results[gid]` で KeyError になり
       アプリごと落ちていた。

修正後に保証したいこと:
    - 図番フォーマットに不一致（かつ不可視ファイルでもない）ファイルは
      summary の 'skipped' リストに記録される。
    - 不可視ファイル（`.` で始まるファイル名。相対パスの途中セグメントも対象）は
      'total_input' からも 'skipped' からも除外される（2026-09-16 追加要求。
      `.DS_Store` 等をノイズとして数えない）。
    - `is_drawing_number_filename()` 自体の判定基準は変更しない
      （`.`始まりのファイルはそもそも図番フォーマットに不一致のため、
      processed件数には影響しない——あくまで表示件数の定義の変更）。
    - Type B（auto）のように複数グループを同時に処理する際、一方のグループの
      一致が0件でも KeyError にならず、両方のグループのsummaryが正しく更新される。
    - 入力ファイルが不可視ファイルのみの場合（total_input=0）も落ちない。

実行:
    cd DXF-diff-manager
    python -m pytest tests/regression/bugfix/test_upload_skipped_files_reported.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

# app.py は `import streamlit as st` した上で module レベルで st.set_page_config() を
# 呼ぶが、`streamlit run` 以外の文脈（pytest含む）では ScriptRunContext が無いため
# 警告ログが出るだけで実害はない（tests/regression/test_auto_revup.py 等、既存の
# 複数の回帰テストも同じ方法で `import app` している）。警告ログはテスト出力を
# 汚すだけなので抑制する。
logging.getLogger("streamlit").setLevel(logging.ERROR)

import streamlit as st

import app


class _FakeUploadedFile:
    """st.file_uploader が返す UploadedFile の name 属性だけを模したフェイク。"""

    def __init__(self, name):
        self.name = name


def _extractor(f):
    return {
        'filename': f.name,
        'temp_path': f'/tmp/{f.name}',
        'main_drawing_number': os.path.splitext(f.name)[0],
    }


def _make_group(uploaded_files, prefix):
    st.session_state[f'{prefix}_key'] = 0
    st.session_state[f'{prefix}_fail'] = []
    st.session_state[f'{prefix}_summary'] = None
    return {
        'uploaded_files': uploaded_files,
        'files_dict': {},
        'upload_key_name': f'{prefix}_key',
        'failures_key': f'{prefix}_fail',
        'summary_key': f'{prefix}_summary',
        'extractor': _extractor,
    }


def test_non_matching_files_are_reported_as_skipped():
    """図番フォーマットに一致しないファイルは summary['skipped'] に記録される
    （以前はサイレントに捨てられ、どこにも記録されなかった）。"""
    st.session_state.clear()
    group = _make_group([
        _FakeUploadedFile('EE1234-567A.dxf'),
        _FakeUploadedFile('差分.xlsx'),
        _FakeUploadedFile('not_a_drawing_number.dxf'),
    ], 'g1')

    result = app.process_all_uploaded_files([group])

    assert result is True
    summary = st.session_state['g1_summary']
    assert summary['processed'] == 1
    assert summary['total_input'] == 3
    assert set(summary['skipped']) == {'差分.xlsx', 'not_a_drawing_number.dxf'}
    assert group['files_dict'] == {
        'EE1234-567A': {
            'filename': 'EE1234-567A.dxf',
            'temp_path': '/tmp/EE1234-567A.dxf',
            'main_drawing_number': 'EE1234-567A',
        }
    }


def test_hidden_files_excluded_from_total_input_and_skipped():
    """不可視ファイル（`.` 始まり）は total_input からもスキップ一覧からも除外される
    （2026-09-16 追加要求）。相対パスの途中セグメントが `.` 始まりの場合も対象。"""
    st.session_state.clear()
    group = _make_group([
        _FakeUploadedFile('EE1234-567A.dxf'),
        _FakeUploadedFile('.DS_Store'),
        _FakeUploadedFile('subfolder/.DS_Store'),
        _FakeUploadedFile('.git/config'),
    ], 'g2')

    app.process_all_uploaded_files([group])

    summary = st.session_state['g2_summary']
    # 不可視ファイル3件は total_input に数えられない（母数は EE1234-567A.dxf の1件のみ）
    assert summary['total_input'] == 1
    assert summary['processed'] == 1
    assert summary['skipped'] == []


def test_all_hidden_input_does_not_crash():
    """入力ファイルが不可視ファイルのみの場合（total_input=0）も落ちない。"""
    st.session_state.clear()
    group = _make_group([_FakeUploadedFile('.DS_Store')], 'g3')

    result = app.process_all_uploaded_files([group])

    summary = st.session_state['g3_summary']
    assert summary['total_input'] == 0
    assert summary['processed'] == 0
    assert summary['skipped'] == []
    # uploaded_files自体は非空だったので、グループのアップロードカウンタは進む
    assert result is False  # 実際に読み込めたファイルは0件


def test_multi_group_with_one_group_having_zero_matches_does_not_raise_keyerror():
    """Type B(auto) 相当: 2グループのうち一方の一致が0件でも KeyError にならず、
    両グループのsummaryが正しく更新される（以前は group_results の初期化漏れで
    KeyError が発生していた）。"""
    st.session_state.clear()
    group_with_match = _make_group([_FakeUploadedFile('EE1234-567A.dxf')], 'src')
    group_without_match = _make_group([_FakeUploadedFile('random_file.dxf')], 'dst')

    result = app.process_all_uploaded_files([group_with_match, group_without_match])

    assert result is True  # 少なくとも一方のグループは処理された

    src_summary = st.session_state['src_summary']
    assert src_summary['processed'] == 1
    assert src_summary['skipped'] == []

    dst_summary = st.session_state['dst_summary']
    assert dst_summary['processed'] == 0
    assert dst_summary['failed'] == 0
    assert dst_summary['skipped'] == ['random_file.dxf']


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
