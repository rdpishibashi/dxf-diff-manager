"""diff_labels.xlsx プレビューの「機器符号候補」絞り込み（streamlit 非依存）。

DXF-extract-labels の `app.py` の `candidate_filter_options()`・`filter_by_candidate()` と
同じ振る舞い（選択肢は DEF → CAN → その他 → （空欄）の順、空欄は欠損値または空文字）。
共有ファイルにはせず、DXF-diff-manager だけが持つ（`app.py` から使う。表示専用で、
Excel の出力には使わない）。
"""
import pandas as pd

CANDIDATE_COLUMN = '機器符号候補'
BLANK_MARK = '（空欄）'
_CANDIDATE_ORDER = ('DEF', 'CAN')


def _key(value):
    """セルの値を選択肢の名前にする（欠損・空文字は `BLANK_MARK`）。"""
    if pd.isna(value) or str(value).strip() == '':
        return BLANK_MARK
    return str(value)


def candidate_filter_options(series):
    """列に実際にある値を DEF → CAN → その他（名前順）→ （空欄）の順で返す。"""
    present = {_key(v) for v in series}
    ordered = [v for v in _CANDIDATE_ORDER if v in present]
    ordered += sorted(v for v in present if v not in _CANDIDATE_ORDER and v != BLANK_MARK)
    if BLANK_MARK in present:
        ordered.append(BLANK_MARK)
    return ordered


def filter_by_candidate(df, selected):
    """「機器符号候補」列が `selected`（`candidate_filter_options()` の値）に含まれる行だけを
    返す（index は保持）。"""
    keys = df[CANDIDATE_COLUMN].apply(_key)
    return df[keys.isin(set(selected))]
