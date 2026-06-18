import os
import tempfile
import time
import traceback
import re

# このアプリが作成する一時ファイルの識別用prefix。
# セッションが正常終了せず孤立した一時ファイルを安全に掃除する際の目印として使う。
TEMP_FILE_PREFIX = "dxfdm_"

def save_uploadedfile(uploadedfile):
    """アップロードされたファイルを一時ディレクトリに保存する"""
    with tempfile.NamedTemporaryFile(delete=False, prefix=TEMP_FILE_PREFIX,
                                      suffix=os.path.splitext(uploadedfile.name)[1]) as f:
        f.write(uploadedfile.getbuffer())
        return f.name


def cleanup_stale_temp_files(max_age_seconds=3 * 60 * 60):
    """
    タブを閉じる等でセッションが正常終了せず孤立した本アプリの一時ファイルを掃除する。

    通常の一時ファイルは cleanup_temp_files()（リスタートボタン押下時）で回収されるが、
    ユーザーがリスタートを押さずにセッションを離脱した場合は回収されないまま残る。
    新しいセッション開始時に一度だけ呼び、本アプリのprefix付きファイルのうち
    十分古いもの（既存セッションが使用中である可能性が低いもの）だけを削除する。
    """
    try:
        tmp_dir = tempfile.gettempdir()
        now = time.time()
        for name in os.listdir(tmp_dir):
            if not name.startswith(TEMP_FILE_PREFIX):
                continue
            path = os.path.join(tmp_dir, name)
            try:
                if os.path.isfile(path) and (now - os.path.getmtime(path)) > max_age_seconds:
                    os.unlink(path)
            except Exception:
                pass  # 他プロセスが使用中などのエラーは無視
    except Exception:
        pass

def handle_error(e, show_traceback=True):
    """エラーを適切に処理して表示する"""
    import streamlit as st
    st.error(f"エラーが発生しました: {str(e)}")
    if show_traceback:
        st.error(traceback.format_exc())


def filter_non_circuit_symbols(labels, debug=False):
    """機器符号フォーマットに一致しないラベルをフィルタリングする"""
    patterns = [
        r'^[A-Za-z]{2,}$',               # 英文字のみ（2文字以上）
        r'^[A-Za-z]+\d+$',               # 英文字+数字
        r'^[A-Za-z]+\d+[A-Za-z]+$',      # 英文字+数字+英文字
        r'^[A-Za-z]{2,}\([^)]*\)$',      # 英文字のみ+括弧
        r'^[A-Za-z]+\d+\([^)]*\)$',      # 英文字+数字+括弧
        r'^[A-Za-z]+\d+[A-Za-z]+\([^)]*\)$',  # 英文字+数字+英文字+括弧
    ]

    filtered_labels = []
    excluded_count = 0

    for label in labels:
        is_match = any(re.match(p, label) for p in patterns)
        if is_match:
            filtered_labels.append(label)
        else:
            excluded_count += 1

    return filtered_labels, excluded_count


def validate_circuit_symbols(labels):
    """機器符号の妥当性をチェックし、適合しないものを返す"""
    standard_patterns = [
        r'^CB\d+$', r'^ELB\(CB\)\d+$', r'^MCCB\d+$', r'^NFB\d+$',
        r'^R\d*$', r'^C\d*$', r'^L\d*$', r'^Q\d*$',
        r'^U\d*[A-Z]*$',
        r'^PSW?\d*$', r'^DC\d*$', r'^AC\d*$',
        r'^M\d*[A-Z]*$', r'^MOT\d*$',
        r'^K\d*[A-Z]*$', r'^MC\d*$',
        r'^S\d*[A-Z]*$', r'^SW\d*$', r'^PB\d*$',
        r'^H\d*[A-Z]*$', r'^HL\d*$', r'^PL\d*$',
        r'^X\d*[A-Z]*$', r'^CN\d*$', r'^TB\d*$',
        r'^F\d*$', r'^T\d*$', r'^A\d*$',
    ]

    invalid_symbols = []
    for label in labels:
        if not any(re.match(p, label) for p in standard_patterns):
            invalid_symbols.append(label)

    return invalid_symbols


def process_circuit_symbol_labels(labels, filter_non_parts=False, validate_ref_designators=False, debug=False):
    """ラベルに対して機器符号処理を統合的に実行する"""
    result = {
        'labels': labels.copy(),
        'filtered_count': 0,
        'invalid_ref_designators': []
    }

    if filter_non_parts:
        filtered_labels, filtered_count = filter_non_circuit_symbols(labels, debug)
        result['labels'] = filtered_labels
        result['filtered_count'] = filtered_count

    if validate_ref_designators and filter_non_parts:
        result['invalid_ref_designators'] = validate_circuit_symbols(result['labels'])

    return result
