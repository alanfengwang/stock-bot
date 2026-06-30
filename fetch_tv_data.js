/**
 * fetch_tv_data.js — 用 TradingView-API 下载历史 K 线数据
 *
 * 用法：
 *   node fetch_tv_data.js                        # 下载所有股票，日线，2025 上半年
 *   node fetch_tv_data.js --from 2024-01-01      # 自定义起始日期
 *   node fetch_tv_data.js --to 2025-06-30        # 自定义结束日期
 *   node fetch_tv_data.js --stocks NVDA,AMD,AAPL # 只下载指定股票
 *   node fetch_tv_data.js --outdir ./my_data     # 输出目录
 *
 * 输出：historical_data/<SYMBOL>_D.csv
 *
 * CSV 格式（与 backtest.py 兼容）：
 *   time_key,open,high,low,close,volume
 */

const TradingView = require('./TradingView-API/main');
const fs   = require('fs');
const path = require('path');

// ── 命令行参数解析 ────────────────────────────────────────────
const args = process.argv.slice(2);
const getArg = (name) => {
  const idx = args.indexOf(name);
  return idx !== -1 ? args[idx + 1] : null;
};

const FROM_DATE  = getArg('--from')    || '2025-01-01';
const TO_DATE    = getArg('--to')      || '2025-06-30';
const OUT_DIR    = getArg('--outdir')  || path.join(__dirname, 'historical_data');
const ONLY_STOCK = getArg('--stocks'); // 逗号分隔

// ── 股票池（与 strategy_config.py 保持一致）────────────────────
const STOCK_MAP = {
  // 保守桶
  'AAPL':  'NASDAQ:AAPL',
  'MSFT':  'NASDAQ:MSFT',
  'GOOGL': 'NASDAQ:GOOGL',
  'META':  'NASDAQ:META',
  'AVGO':  'NASDAQ:AVGO',
  'ORCL':  'NYSE:ORCL',
  'AMZN':  'NASDAQ:AMZN',
  'V':     'NYSE:V',
  'MA':    'NYSE:MA',
  'DELL':  'NYSE:DELL',
  'HPQ':   'NYSE:HPQ',
  'ADBE':  'NASDAQ:ADBE',
  'CSCO':  'NASDAQ:CSCO',
  'IBM':   'NYSE:IBM',
  'INTU':  'NASDAQ:INTU',
  'SAP':   'NYSE:SAP',
  // 成长桶
  'NVDA':  'NASDAQ:NVDA',
  'AMD':   'NASDAQ:AMD',
  'MU':    'NASDAQ:MU',
  'AMAT':  'NASDAQ:AMAT',
  'MRVL':  'NASDAQ:MRVL',
  'KLAC':  'NASDAQ:KLAC',
  'LRCX':  'NASDAQ:LRCX',
  'ARM':   'NASDAQ:ARM',
  'QCOM':  'NASDAQ:QCOM',
  'WDC':   'NASDAQ:WDC',
  'STX':   'NASDAQ:STX',
  'SNDK':  'NASDAQ:SNDK',
  'CIEN':  'NYSE:CIEN',
  'COHR':  'NYSE:COHR',
  'GLW':   'NYSE:GLW',
  'TSM':   'NYSE:TSM',
  'INTC':  'NASDAQ:INTC',
  'CRWV':  'NASDAQ:CRWV',
  'GLD':   'AMEX:GLD',
  'ADI':   'NASDAQ:ADI',
  'NXPI':  'NASDAQ:NXPI',
  'MPWR':  'NASDAQ:MPWR',
  'TER':   'NASDAQ:TER',
  'ENTG':  'NASDAQ:ENTG',
  // 短线桶
  'TSLA':  'NASDAQ:TSLA',
  'NOK':   'NYSE:NOK',
  'ASTS':  'NASDAQ:ASTS',
  'POET':  'NASDAQ:POET',
  'UUUU':  'NYSE:UUUU',
  'OKLO':  'NYSE:OKLO',
  'AEP':   'NASDAQ:AEP',
  'PLTR':  'NYSE:PLTR',
  'APP':   'NASDAQ:APP',
  'NOW':   'NYSE:NOW',
  'CRWD':  'NASDAQ:CRWD',
  'DDOG':  'NASDAQ:DDOG',
  'PANW':  'NASDAQ:PANW',
  'SMCI':  'NASDAQ:SMCI',
  'KTOS':  'NASDAQ:KTOS',
  'RKLB':  'NASDAQ:RKLB',
  'LUNR':  'NASDAQ:LUNR',
  'VST':   'NYSE:VST',
  'CEG':   'NASDAQ:CEG',
  'NRG':   'NYSE:NRG',
  'FCX':   'NYSE:FCX',
  'MP':    'NYSE:MP',
  'LITE':  'NASDAQ:LITE',
  'CCJ':   'NYSE:CCJ',
  'AXON':  'NASDAQ:AXON',
  'NET':   'NYSE:NET',
  'ZS':    'NASDAQ:ZS',
  'AI':    'NYSE:AI',
  'AAOI':  'NASDAQ:AAOI',
  'AVAV':  'NASDAQ:AVAV',
  'FSLR':  'NASDAQ:FSLR',
  'FN':    'NYSE:FN',
  // ETF
  'QQQ':   'NASDAQ:QQQ',
  'VOO':   'AMEX:VOO',
  'SPY':   'AMEX:SPY',
};

// ── 日期工具 ──────────────────────────────────────────────────
function dateToTs(dateStr) {
  return Math.floor(new Date(dateStr + 'T23:59:59Z').getTime() / 1000);
}

function tsToDate(ts) {
  return new Date(ts * 1000).toISOString().slice(0, 10);
}

// ── 核心：获取单只股票数据 ─────────────────────────────────────
function fetchStock(symbol, tvSymbol, fromTs, toTs) {
  return new Promise((resolve, reject) => {
    const client = new TradingView.Client();
    const chart  = new client.Session.Chart();

    const daysDiff = Math.ceil((toTs - fromTs) / 86400) + 60; // 多拉 60 根作为回测 buffer

    chart.setMarket(tvSymbol, {
      timeframe: 'D',           // 日线
      range: daysDiff,          // 根数（最多拉 daysDiff 根）
      to: toTs,                 // 截止时间戳
    });

    const timeout = setTimeout(() => {
      client.end();
      reject(new Error(`Timeout: ${symbol}`));
    }, 15000);

    chart.onError((...err) => {
      clearTimeout(timeout);
      client.end();
      reject(new Error(`Chart error for ${symbol}: ${err.join(' ')}`));
    });

    chart.onUpdate(() => {
      if (!chart.periods || chart.periods.length === 0) return;

      clearTimeout(timeout);

      // 过滤到 fromTs 之后的数据
      const rows = chart.periods
        .filter(p => p.time >= fromTs)
        .sort((a, b) => a.time - b.time);

      client.end();
      resolve({ symbol, rows });
    });
  });
}

// ── 写 CSV ────────────────────────────────────────────────────
function writeCsv(symbol, rows, outDir) {
  const filePath = path.join(outDir, `${symbol}_D.csv`);
  const header   = 'time_key,open,high,low,close,volume\n';
  const lines    = rows.map(r =>
    `${tsToDate(r.time)},${r.open},${r.max},${r.min},${r.close},${r.volume}`
  ).join('\n');
  fs.writeFileSync(filePath, header + lines + '\n');
  return filePath;
}

// ── 主程序 ────────────────────────────────────────────────────
async function main() {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

  const fromTs = dateToTs(FROM_DATE) - 86400 * 70; // 回测需要 buffer（均线预热）
  const toTs   = dateToTs(TO_DATE);

  // 决定要下载哪些股票
  let symbols = Object.keys(STOCK_MAP);
  if (ONLY_STOCK) {
    symbols = ONLY_STOCK.toUpperCase().split(',').map(s => s.trim());
  }

  console.log(`\n📦 TradingView 数据下载`);
  console.log(`   日期范围：${FROM_DATE} → ${TO_DATE}（含 70 根预热 buffer）`);
  console.log(`   股票数量：${symbols.length}`);
  console.log(`   输出目录：${OUT_DIR}\n`);

  let ok = 0, fail = 0;

  // 逐只下载（WebSocket 方式不适合并发太多，每次 3 只）
  for (let i = 0; i < symbols.length; i += 3) {
    const batch = symbols.slice(i, i + 3);
    await Promise.allSettled(
      batch.map(async (sym) => {
        const tvSym = STOCK_MAP[sym];
        if (!tvSym) {
          console.log(`  ⚠️  ${sym.padEnd(6)} 未在 STOCK_MAP 中配置，跳过`);
          fail++;
          return;
        }
        try {
          const { rows } = await fetchStock(sym, tvSym, fromTs, toTs);
          if (rows.length === 0) {
            console.log(`  ⚠️  ${sym.padEnd(6)} 无数据`);
            fail++;
            return;
          }
          const filePath = writeCsv(sym, rows, OUT_DIR);
          console.log(`  ✅  ${sym.padEnd(6)} ${rows.length} 根 K 线 → ${path.basename(filePath)}`);
          ok++;
        } catch (e) {
          console.log(`  ❌  ${sym.padEnd(6)} 失败: ${e.message}`);
          fail++;
        }
      })
    );
    // 避免太快被 TradingView 限速
    if (i + 3 < symbols.length) {
      await new Promise(r => setTimeout(r, 500));
    }
  }

  console.log(`\n完成：${ok} 只成功，${fail} 只失败`);
  console.log(`\n下一步：`);
  console.log(`  python3 backtest.py --local --from ${FROM_DATE} --to ${TO_DATE}\n`);
}

main().catch(console.error);
