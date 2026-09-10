import os
import tempfile
import time
import re

# このアプリが作成する一時ファイルの識別用prefix。
# セッションが正常終了せず孤立した一時ファイルを安全に掃除する際の目印として使う。
TEMP_FILE_PREFIX = "dxfdm_"

def is_invisible(e):
    """DXFの`invisible`属性（グループコード60、1=非表示）が立っている
    エンティティかを返す。CADソフト上で「非表示」に設定された図形
    （紙面には一切表示されない）は、たとえDXFファイル中に座標・テキスト
    として存在していても収集対象にしてはならない（DXF-extract-labels
    2026-09-11の横展開に伴い、model/extract_labels.pyのbyte一致維持のため
    ここへ追加。詳細はDXF-extract-labels/tests/regression/test_ref_designator.py
    のinvisible関連テストを参照）。

    呼び出し側は次の3箇所すべてでチェックする必要がある（`virtual_entities()`
    は親INSERTのinvisible属性を継承しないため、INSERT自身のチェックを
    省くと、INSERT自身がinvisibleでも展開後の中身は素通りしてしまう）:
      1. 直接配置エンティティ
      2. INSERT自身（invisibleなINSERTは中身ごと丸ごと除外する）
      3. `virtual_entities()`で展開した仮想エンティティ（親が可視でも
         個々の子エンティティにinvisibleが立っている場合があるため）
    """
    return bool(e.dxf.get('invisible', 0))


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


def is_drawing_number_filename(filename):
    """
    ファイル名（拡張子を除く部分）が図番フォーマットに完全一致するかを判定する。

    対応フォーマット（config.ExtractionConfig.DRAWING_NUMBER_PATTERN と同じ形状）:
      - 長: aannnn-nnn-nna（例: EE1234-567-89A）
      - 短: aannnn-nnna    （例: EE1234-567A）
    大文字のみを受け付ける（DXFテキスト内からの図番抽出はIGNORECASEで行うが、
    ファイル名フィルタは表記揺れを避けるため大文字限定とする）。

    Step2のDXFファイルアップロード（フォルダを丸ごとドラッグ&ドロップした際に
    ブラウザが再帰展開する全ファイルの中から、図番フォーマットに合致する
    DXFファイルのみを対象とする）に使用する。

    config.py に依存する他の関数と異なりモジュール先頭でimportしないのは、
    本ファイルが他プロジェクト（DXF-extract-labels等）にもコピーされて
    使われており、config.py が無い/構造が異なる環境でも本ファイルの他の
    関数（filter_non_circuit_symbols等）を壊さないため（遅延import）。
    """
    from config import extraction_config
    stem = os.path.splitext(filename)[0]
    return re.fullmatch(extraction_config.DRAWING_NUMBER_PATTERN, stem) is not None


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
