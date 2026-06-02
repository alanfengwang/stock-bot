"""
refresh_fundamentals.py — 手动刷新慢速基本面缓存

用法：
  python3 refresh_fundamentals.py
  python3 refresh_fundamentals.py --force
  python3 refresh_fundamentals.py --limit 20
"""

from __future__ import annotations

import sys

from fundamental_store import refresh_fundamental_cache
from strategy_config import WATCH_UNIVERSE


def main(argv: list[str]) -> int:
    force = '--force' in argv
    limit = None
    if '--limit' in argv:
        idx = argv.index('--limit')
        if idx + 1 < len(argv):
            limit = int(argv[idx + 1])

    universe = WATCH_UNIVERSE[:limit] if limit else WATCH_UNIVERSE
    print(f"刷新慢速基本面缓存：{len(universe)} 只")
    summary = refresh_fundamental_cache(universe, force=force)
    print(
        f"完成：更新 {summary['updated']}  跳过 {summary['skipped']}  失败 {summary['failed']}"
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
