#!/usr/bin/env python3
"""
DXF Projects Utils Folder Synchronization Script

This script synchronizes the utils folder between DXF-diff-manager and DXF-visual-diff
projects, automatically determining which should be the master based on file timestamps.

SYNC STRATEGY:
- Primary Master: DXF-diff-manager (more complex features, active development)
- extract_labels.py uses adaptive config pattern (works in both environments)
- See SYNC_STRATEGY.md for detailed documentation

ADAPTIVE CONFIG PATTERN:
The extract_labels.py file uses try/except to work in both environments:
  - DXF-diff-manager: Loads from external config.py
  - DXF-visual-diff: Falls back to internal ExtractionConfig class
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Tuple, Dict, List, Optional
import hashlib

# Project directories (fixed path - can be invoked from anywhere)
BASE_DIR = Path("/Users/ryozo/Dropbox/Client/ULVAC/ElectricDesignManagement/Tools")
PROJECT_A = BASE_DIR / "DXF-diff-manager"
PROJECT_B = BASE_DIR / "DXF-visual-diff"

# DXF-diff-manager renamed utils/ -> model/ (2026-07-15, 3-layer refactor);
# DXF-visual-diff still uses utils/. Look up the right subdir name per project.
SUBDIR_BY_PROJECT = {
    PROJECT_A: "model",
    PROJECT_B: "utils",
}


def utils_subdir(project_dir: Path) -> str:
    """Return this project's model/utils subfolder name (see SUBDIR_BY_PROJECT)."""
    return SUBDIR_BY_PROJECT[project_dir]

# Utils files to sync
UTILS_FILES = [
    "common_utils.py",
    "compare_dxf.py",
    "extract_labels.py",
    "label_diff.py",
]

class Color:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def print_colored(text: str, color: str = Color.ENDC):
    """Print colored text to terminal"""
    print(f"{color}{text}{Color.ENDC}")


def get_file_info(file_path: Path) -> Optional[Dict]:
    """Get file information including size, mtime, and hash"""
    if not file_path.exists():
        return None

    stat = file_path.stat()

    # Calculate file hash
    with open(file_path, 'rb') as f:
        file_hash = hashlib.md5(f.read()).hexdigest()

    return {
        'path': file_path,
        'size': stat.st_size,
        'mtime': stat.st_mtime,
        'mtime_str': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        'hash': file_hash,
    }


def compare_projects() -> Tuple[str, Dict[str, Dict]]:
    """
    Compare both projects and determine which should be the master.

    Returns:
        Tuple of (master_name, comparison_data)
    """
    print_colored("\n" + "="*80, Color.HEADER)
    print_colored("  Utils フォルダ比較分析", Color.HEADER)
    print_colored("="*80, Color.HEADER)

    comparison = {}
    project_a_score = 0
    project_b_score = 0

    for filename in UTILS_FILES:
        file_a = PROJECT_A / utils_subdir(PROJECT_A) / filename
        file_b = PROJECT_B / utils_subdir(PROJECT_B) / filename

        info_a = get_file_info(file_a)
        info_b = get_file_info(file_b)

        comparison[filename] = {
            'diff-manager': info_a,
            'visual-diff': info_b,
        }

        # Determine which is newer
        if info_a and info_b:
            if info_a['hash'] == info_b['hash']:
                status = "同一"
                symbol = "="
                color = Color.OKGREEN
            elif info_a['mtime'] > info_b['mtime']:
                status = "diff-manager が新しい"
                symbol = ">"
                color = Color.WARNING
                project_a_score += 1
            elif info_a['mtime'] < info_b['mtime']:
                status = "visual-diff が新しい"
                symbol = "<"
                color = Color.WARNING
                project_b_score += 1
            else:
                status = "同時刻（内容が異なる）"
                symbol = "!"
                color = Color.FAIL
        elif info_a:
            status = "visual-diff に存在しない"
            symbol = "A"
            color = Color.FAIL
            project_a_score += 1
        elif info_b:
            status = "diff-manager に存在しない"
            symbol = "B"
            color = Color.FAIL
            project_b_score += 1
        else:
            status = "両方に存在しない"
            symbol = "X"
            color = Color.FAIL

        # Print comparison
        print_colored(f"\n📄 {filename}", Color.BOLD)
        if info_a:
            print(f"  DXF-diff-manager: {info_a['size']:>7,} bytes  {info_a['mtime_str']}")
        else:
            print(f"  DXF-diff-manager: {'存在しない':>7}")

        if info_b:
            print(f"  DXF-visual-diff:  {info_b['size']:>7,} bytes  {info_b['mtime_str']}")
        else:
            print(f"  DXF-visual-diff:  {'存在しない':>7}")

        print_colored(f"  {symbol} {status}", color)

    # Determine master
    print_colored("\n" + "-"*80, Color.HEADER)
    print_colored("  判定結果", Color.HEADER)
    print_colored("-"*80, Color.HEADER)
    print(f"  DXF-diff-manager スコア: {project_a_score}")
    print(f"  DXF-visual-diff スコア:  {project_b_score}")

    if project_a_score > project_b_score:
        master = "DXF-diff-manager"
        color = Color.OKGREEN
        reason = "（タイムスタンプが新しい）"
    elif project_b_score > project_a_score:
        master = "DXF-visual-diff"
        color = Color.WARNING
        reason = "（タイムスタンプが新しいが、戦略上は DXF-diff-manager 推奨）"
        print_colored(f"\n  💡 戦略的推奨: DXF-diff-manager を通常のマスターとして使用", Color.OKCYAN)
        print_colored(f"     理由: より多機能、extract_labels.py の主要開発元", Color.OKCYAN)
    else:
        master = "DXF-diff-manager"  # Default to diff-manager
        color = Color.OKGREEN
        reason = "（同点のため、戦略的推奨マスターを選択）"

    print_colored(f"\n  🎯 自動判定マスター: {master} {reason}", color)

    return master, comparison


def run_diff(file_a: Path, file_b: Path) -> bool:
    """Run diff command and show differences"""
    try:
        result = subprocess.run(
            ['diff', '-u', str(file_b), str(file_a)],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            print_colored("  ✓ ファイルは同一です", Color.OKGREEN)
            return False
        else:
            print_colored("  差分あり:", Color.WARNING)
            # Show first 20 lines of diff
            lines = result.stdout.split('\n')
            for line in lines[:20]:
                if line.startswith('+'):
                    print_colored(f"    {line}", Color.OKGREEN)
                elif line.startswith('-'):
                    print_colored(f"    {line}", Color.FAIL)
                else:
                    print(f"    {line}")

            if len(lines) > 20:
                print_colored(f"    ... ({len(lines) - 20} 行省略)", Color.OKCYAN)

            return True

    except Exception as e:
        print_colored(f"  ⚠️  diff 実行エラー: {e}", Color.FAIL)
        return False


def sync_files(master: str, dry_run: bool = False) -> List[str]:
    """
    Sync files from master to target.

    Args:
        master: Master project name ("DXF-diff-manager" or "DXF-visual-diff")
        dry_run: If True, only show what would be done

    Returns:
        List of synced files
    """
    if master == "DXF-diff-manager":
        source_dir = PROJECT_A / utils_subdir(PROJECT_A)
        target_dir = PROJECT_B / utils_subdir(PROJECT_B)
        target_name = "DXF-visual-diff"
    else:
        source_dir = PROJECT_B / utils_subdir(PROJECT_B)
        target_dir = PROJECT_A / utils_subdir(PROJECT_A)
        target_name = "DXF-diff-manager"

    print_colored("\n" + "="*80, Color.HEADER)
    print_colored(f"  同期実行: {master} → {target_name}", Color.HEADER)
    print_colored("="*80, Color.HEADER)

    if dry_run:
        print_colored("\n  🔍 DRY RUN モード（実際にはコピーしません）", Color.WARNING)

    synced_files = []

    for filename in UTILS_FILES:
        source_file = source_dir / filename
        target_file = target_dir / filename

        print(f"\n📄 {filename}")

        if not source_file.exists():
            print_colored(f"  ⚠️  ソースファイルが存在しません: {source_file}", Color.FAIL)
            continue

        # Show diff first
        if target_file.exists():
            has_diff = run_diff(source_file, target_file)
            if not has_diff:
                print_colored("  → スキップ（同一ファイル）", Color.OKCYAN)
                continue

        # Copy file
        if not dry_run:
            try:
                shutil.copy2(source_file, target_file)
                print_colored(f"  ✓ コピー完了: {source_file} → {target_file}", Color.OKGREEN)
                synced_files.append(filename)
            except Exception as e:
                print_colored(f"  ✗ コピー失敗: {e}", Color.FAIL)
        else:
            print_colored(f"  → コピー予定: {source_file} → {target_file}", Color.OKCYAN)
            synced_files.append(filename)

    return synced_files


def verify_syntax(project_dir: Path) -> bool:
    """Run syntax check on Python files"""
    print_colored("\n" + "="*80, Color.HEADER)
    print_colored(f"  構文チェック: {project_dir.name}", Color.HEADER)
    print_colored("="*80, Color.HEADER)

    utils_dir = project_dir / utils_subdir(project_dir)
    app_file = project_dir / "app.py"

    files_to_check = [app_file]
    for filename in UTILS_FILES:
        file_path = utils_dir / filename
        if file_path.exists():
            files_to_check.append(file_path)

    all_ok = True

    for file_path in files_to_check:
        try:
            result = subprocess.run(
                ['python3', '-m', 'py_compile', str(file_path)],
                capture_output=True,
                text=True,
                cwd=project_dir
            )

            if result.returncode == 0:
                print_colored(f"  ✓ {file_path.name}", Color.OKGREEN)
            else:
                print_colored(f"  ✗ {file_path.name}", Color.FAIL)
                print_colored(f"    {result.stderr}", Color.FAIL)
                all_ok = False

        except Exception as e:
            print_colored(f"  ✗ {file_path.name}: {e}", Color.FAIL)
            all_ok = False

    return all_ok


def main():
    """Main execution"""
    print_colored("\n" + "="*80, Color.BOLD)
    print_colored("  DXF Projects Utils Sync Tool", Color.BOLD)
    print_colored("="*80 + "\n", Color.BOLD)

    # Check if projects exist
    if not PROJECT_A.exists():
        print_colored(f"✗ DXF-diff-manager が見つかりません: {PROJECT_A}", Color.FAIL)
        sys.exit(1)

    if not PROJECT_B.exists():
        print_colored(f"✗ DXF-visual-diff が見つかりません: {PROJECT_B}", Color.FAIL)
        sys.exit(1)

    # Parse arguments
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    force_master = None

    for arg in sys.argv[1:]:
        if arg.startswith('--master='):
            force_master = arg.split('=')[1]
        elif arg == '--diff-manager':
            force_master = 'DXF-diff-manager'
        elif arg == '--visual-diff':
            force_master = 'DXF-visual-diff'

    # Compare and determine master
    auto_master, comparison = compare_projects()

    if force_master:
        if force_master not in ['DXF-diff-manager', 'DXF-visual-diff']:
            print_colored(f"\n✗ 無効なマスター指定: {force_master}", Color.FAIL)
            print_colored("  有効な値: DXF-diff-manager, DXF-visual-diff", Color.WARNING)
            sys.exit(1)

        print_colored(f"\n⚠️  マスターを手動で指定: {force_master}", Color.WARNING)
        master = force_master
    else:
        master = auto_master

    # Confirm
    print_colored("\n" + "="*80, Color.HEADER)
    if force_master:
        print_colored(f"  マスター: {master} (手動指定)", Color.WARNING)
    else:
        print_colored(f"  マスター: {master} (自動判定)", Color.OKGREEN)
    print_colored("="*80, Color.HEADER)

    if not dry_run:
        response = input("\n同期を実行しますか? [y/N]: ")
        if response.lower() != 'y':
            print_colored("\n中止しました", Color.WARNING)
            sys.exit(0)

    # Sync files
    synced_files = sync_files(master, dry_run)

    if not synced_files:
        print_colored("\n✓ 同期するファイルはありません（すべて最新です）", Color.OKGREEN)
        sys.exit(0)

    # Verify syntax
    if not dry_run:
        target_project = PROJECT_B if master == "DXF-diff-manager" else PROJECT_A
        syntax_ok = verify_syntax(target_project)

        if syntax_ok:
            print_colored("\n✓ すべての構文チェックに合格しました", Color.OKGREEN)
        else:
            print_colored("\n✗ 構文エラーが検出されました", Color.FAIL)
            sys.exit(1)

    # Summary
    print_colored("\n" + "="*80, Color.HEADER)
    print_colored("  同期完了", Color.HEADER)
    print_colored("="*80, Color.HEADER)
    print(f"  マスター: {master}")
    print(f"  同期ファイル数: {len(synced_files)}")
    print("  同期ファイル:")
    for filename in synced_files:
        print(f"    - {filename}")

    # Show next steps
    target_name = "DXF-visual-diff" if master == "DXF-diff-manager" else "DXF-diff-manager"
    print_colored("\n📝 次のステップ:", Color.OKCYAN)
    print(f"  1. {target_name}/app.py の更新が必要か確認してください")
    print(f"  2. 特に compare_dxf.py の戻り値変更に注意してください")
    print(f"  3. Streamlit アプリを起動してテストしてください:")
    print_colored(f"     cd {target_name} && streamlit run app.py", Color.BOLD)


if __name__ == "__main__":
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
        print("\n使用方法:")
        print("  python sync_utils.py [オプション]")
        print("\nオプション:")
        print("  -n, --dry-run           ドライランモード（実際にはコピーしない）")
        print("  --master=PROJECT        マスターを手動指定")
        print("  --diff-manager          DXF-diff-manager をマスターに指定")
        print("  --visual-diff           DXF-visual-diff をマスターに指定")
        print("  -h, --help              このヘルプを表示")
        print("\n例:")
        print("  python sync_utils.py --dry-run")
        print("  python sync_utils.py --diff-manager")
        print("  python sync_utils.py --master=DXF-visual-diff")
        sys.exit(0)

    try:
        main()
    except KeyboardInterrupt:
        print_colored("\n\n中断されました", Color.WARNING)
        sys.exit(1)
    except Exception as e:
        print_colored(f"\n✗ エラー: {e}", Color.FAIL)
        import traceback
        traceback.print_exc()
        sys.exit(1)
