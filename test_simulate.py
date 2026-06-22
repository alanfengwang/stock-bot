"""
手动诊断脚本：查看 moomoo 模拟账户信息。

注意：
- 这是脚本，不是 unittest 用例。
- 只有在直接执行时才会连接 OpenD，避免被 `python -m unittest` 误导入时报错。
"""

from __future__ import annotations


def main():
    from moomoo import OpenSecTradeContext, RET_OK, TrdEnv

    trd_ctx = OpenSecTradeContext(host='127.0.0.1', port=11111)
    try:
        ret, data = trd_ctx.accinfo_query(trd_env=TrdEnv.SIMULATE)
        if ret == RET_OK:
            print("模拟仓账户信息：")
            print(data[['cash', 'total_assets', 'market_val']])
        else:
            print("错误：", data)
    finally:
        trd_ctx.close()


if __name__ == '__main__':
    main()
