"""
CLI Runner for Overseas Bizarre Business Content Factory
海外奇趣商业自媒体内容工厂命令行工具
"""

import argparse
import sys
from pathlib import Path

# Fix Windows console UTF-8 output encoding
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.database import get_all_cases, get_case_by_id
from core.publisher import package_case_for_all_platforms

def main():
    parser = argparse.ArgumentParser(description="海外奇趣商业案例全平台自动化生成流水线")
    parser.add_argument("--list", action="store_true", help="列出案例库中所有的海外商业案例")
    parser.add_argument("--case", type=str, help="指定案例ID进行自动化出片与物料打包 (例如: pet_rock)")
    parser.add_argument("--batch", action="store_true", help="一键批量生成案例库中的所有案例物料")

    args = parser.parse_args()

    if args.list:
        cases = get_all_cases()
        print("\n" + "="*60)
        print("📚 【海外奇趣商业案例库】清单")
        print("="*60)
        for i, c in enumerate(cases, 1):
            print(f"[{i}] ID: {c['id']:<24} | 标题: {c['title']} ({c['revenue']})")
        print("="*60 + "\n")
        return

    if args.case:
        case = get_case_by_id(args.case)
        if not case:
            print(f"❌ 找不到案例 ID: '{args.case}'。请使用 --list 查看已有案例。")
            return
        print(f"\n🚀 开始为案例【{case['title']}】执行全平台自动化流水线...")
        res = package_case_for_all_platforms(case)
        print("\n✅ 全平台物料生成完毕！")
        for plat, info in res["platforms"].items():
            print(f"  👉 [{plat}] 视频: {info['video']}")
            if info["cover"]:
                print(f"     封面: {info['cover']}")
            print(f"     文案: {info['copy_file']}")
        return

    if args.batch:
        cases = get_all_cases()
        print(f"\n🚀 启动批量生成任务，共 {len(cases)} 个案例...")
        for c in cases:
            print(f"\n⏳ 正在生成: {c['title']} ({c['id']})...")
            package_case_for_all_platforms(c)
        print("\n🎉 批量生成全部完成！所有物料保存在 output/ 目录中。")
        return

    parser.print_help()

if __name__ == "__main__":
    main()
