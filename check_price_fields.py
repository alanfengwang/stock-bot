"""
诊断脚本：打印多只股票快照中所有价格字段的实际值
用法：python3 check_price_fields.py
"""
from moomoo import OpenQuoteContext, RET_OK
from market_utils import live_price_from_row

STOCKS = ['US.AMD', 'US.RKLB', 'US.WDC', 'US.STX', 'US.CIEN', 'US.COHR']

ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
ret, snap = ctx.get_market_snapshot(STOCKS)
ctx.close()

if ret == RET_OK:
    for _, row in snap.iterrows():
        code = row.get('code', '?')
        live = live_price_from_row(row)
        price_fields = [c for c in row.index if any(k in c.lower() for k in
                        ['price', 'pre', 'after', 'overnight', 'last', 'cur', 'open', 'close'])]
        print(f"\n{'='*60}")
        print(f"  {code}   → live_price_from_row = {live:.4f}")
        print('='*60)
        for f in sorted(price_fields):
            v = row.get(f)
            flag = "✅" if v and str(v) not in ('0', '0.0', 'nan', 'None', '') else "⬜"
            print(f"  {flag} {f:<32} = {v}")
else:
    print("快照获取失败")
