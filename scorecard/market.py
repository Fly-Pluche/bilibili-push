#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实行情层（mootdx 通达信日 K，不封 IP）。

用于成绩单的客观验证：取个股 + 基准的日收盘，算前瞻喊单的 T+1/T+3 收益与超额。
基准用 中证1000ETF(512100) —— 这些博主玩的多是小盘题材股，用小盘基准做超额最公允。
"""

import logging
import time

from mootdx.quotes import Quotes

logging.disable(logging.WARNING)

BENCH = "512100"  # 中证1000ETF（小盘基准）
_client = None
_cache = {}  # code -> {date: close}


def _c():
    global _client
    if _client is None:
        _client = Quotes.factory(market="std")
    return _client


def daily_closes(code: str, offset: int = 30) -> dict:
    """{‘YYYY-MM-DD’: close}，最近 offset 个交易日。带进程内缓存。"""
    if code in _cache:
        return _cache[code]
    out = {}
    for attempt in range(3):
        try:
            bars = _c().bars(symbol=code, category=4, offset=offset)
            if bars is not None and hasattr(bars, "iterrows"):
                for idx, row in bars.iterrows():
                    out[str(idx)[:10]] = float(row["close"])
            if out:
                break
        except Exception:
            time.sleep(0.5)
    _cache[code] = out
    return out


def forward_returns(code: str, call_date: str, holds=(1, 3, 5)) -> dict | None:
    """从 call_date 收盘买入，算各持有期 T+N 的个股收益、基准收益、超额。

    返回 {entry, by_hold: {N: {ret, bench, excess, mature}}}；个股/基准取不到数 → None。
    mature=False 表示该 T+N 交易日尚未到（数据还没产生），暂不计分。
    """
    sc = daily_closes(code)
    bc = daily_closes(BENCH)
    if call_date not in sc or call_date not in bc:
        return None
    s_dates = sorted(d for d in sc if d >= call_date)
    b_dates = sorted(d for d in bc if d >= call_date)
    entry = sc[call_date]
    b_entry = bc[call_date]
    res = {"entry": entry, "by_hold": {}}
    for N in holds:
        if N < len(s_dates) and N < len(b_dates):
            tN_s, tN_b = s_dates[N], b_dates[N]
            ret = sc[tN_s] / entry - 1
            bench = bc[tN_b] / b_entry - 1
            res["by_hold"][str(N)] = {"ret": ret, "bench": bench, "excess": ret - bench,
                                      "date": tN_s, "mature": True}
        else:
            res["by_hold"][str(N)] = {"mature": False}  # T+N 还没到（用 str 键，和 JSON 一致）
    return res


if __name__ == "__main__":
    for name, code, d in [("长电科技", "600584", "2026-06-24"),
                          ("中钨高新", "000657", "2026-06-22"),
                          ("中材科技", "002080", "2026-06-24")]:
        r = forward_returns(code, d)
        if not r:
            print(f"{name}: 取数失败"); continue
        print(f"\n{name}({code}) 于 {d} 收盘买入(={r['entry']}):")
        for N, h in r["by_hold"].items():
            if h["mature"]:
                print(f"  T+{N}: 个股 {h['ret']*100:+.1f}% | 基准 {h['bench']*100:+.1f}% | 超额 {h['excess']*100:+.1f}%")
            else:
                print(f"  T+{N}: 未到期")
