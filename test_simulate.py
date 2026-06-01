from moomoo import *

# 连接OpenD（不需要trd_env）
trd_ctx = OpenSecTradeContext(
    host='127.0.0.1',
    port=11111
)

# 查模拟仓账户信息（trd_env在这里传）
ret, data = trd_ctx.accinfo_query(trd_env=TrdEnv.SIMULATE)

if ret == RET_OK:
    print("模拟仓账户信息：")
    print(data[['cash', 'total_assets', 'market_val']])
else:
    print("错误：", data)

trd_ctx.close()