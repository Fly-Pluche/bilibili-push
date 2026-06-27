#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""博主成绩单 —— 主流程（严谨版）。

流程：抓本周各博主动态 → 抽前瞻喊单(extract) → 真实行情验证(market) → 按博主聚合打分 → 出报告。

严谨性保证：
  · 只计入「前瞻买入/关注」喊单，剔除事后炫耀（见 extract.py）；
  · 喊单的『决策时点』=发帖时间，收益只看发帖之后，**无未来函数**；
  · 入场=喊单当日收盘（保守、明确）；收益做**中证1000基准超额**，剔除大盘普涨；
  · 每条喊单连同原文记入 data/calls.jsonl，可逐条审计；
  · 样本不足 / T+N 未到期 都如实标注，不夸大。

用法： BILI_COOKIE=... python3 score.py [起始日 截止日]
       默认本周交易日 2026-06-22 ~ 2026-06-26
"""

import json
import os
import sys
import time
import statistics as st
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))          # scorecard/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # 项目根（bili_push）

import bili_push as bp
from stocklist import Resolver
from extract import extract_calls
from market import forward_returns

_DIR = Path(__file__).resolve().parent
LEDGER = _DIR / "data" / "calls.jsonl"
REPORT = _DIR.parent / "digests" / "BLOGGER_SCORECARD.md"
SPACE = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"


def cst(ts, fmt="%Y-%m-%d"):
    return time.strftime(fmt, time.gmtime(ts + 8 * 3600))


def fetch_until(client, uid, stop_date, max_pages=8):
    """翻页抓某 UP 的空间动态，直到最旧一条早于 stop_date。"""
    client._ensure_keys()
    out, offset = [], ""
    for _ in range(max_pages):
        params = {"offset": offset, "host_mid": uid, "timezone_offset": "-480", "platform": "web",
                  "features": "itemOpusStyle,opusBigCover,onlyfansVote,decorationCard,forwardListHidden,ugcDelete",
                  "web_location": "333.1387"}
        signed = bp.sign_wbi(params, client._img_key, client._sub_key)
        try:
            data = client.session.get(SPACE, params=signed, timeout=20).json()
        except Exception:
            break
        if data.get("code") != 0:
            break
        d = data.get("data", {}); items = d.get("items", []) or []
        out += items
        offset = d.get("offset", "")
        oldest = min((int((x.get("modules", {}).get("module_author", {}) or {}).get("pub_ts", 0) or 0)
                      for x in items), default=0)
        if not d.get("has_more") or not offset or (oldest and cst(oldest) < stop_date):
            break
        time.sleep(1.1)
    return out


def main():
    d_from = sys.argv[1] if len(sys.argv) > 2 else "2026-06-22"
    d_to = sys.argv[2] if len(sys.argv) > 2 else "2026-06-26"
    cookie = os.environ.get("BILI_COOKIE", "").strip()
    if not cookie:
        print("缺少 BILI_COOKIE"); return 2

    print(f"== 博主成绩单 {d_from} ~ {d_to} ==")
    resolver = Resolver()
    client = bp.BiliClient(cookie)
    subs = bp.load_subscriptions()

    # 1) 抓动态 + 抽喊单
    calls = []  # 每条: {blogger, code, name, date, time, direction, kind, clause}
    for uid, name in subs:
        try:
            items = fetch_until(client, uid, d_from)
        except Exception as e:
            print(f"[{name}] 抓取失败: {e}"); continue
        n_posts = 0
        for it in items:
            p = bp.extract(it)
            if not p["ts"]:
                continue
            day = cst(p["ts"])
            if not (d_from <= day <= d_to):
                continue
            n_posts += 1
            for c in extract_calls(p["text"], resolver):
                calls.append({"blogger": name, "code": c["code"], "name": c["name"],
                              "date": day, "time": cst(p["ts"], "%H:%M"),
                              "direction": c["direction"], "kind": c["kind"], "clause": c["clause"]})
        print(f"  {name}: {n_posts} 帖, 抽出 {sum(1 for c in calls if c['blogger']==name)} 条信号")
        time.sleep(0.8)

    # 2) 与历史台账合并（**累积**，不覆盖；按 博主+代码+日期+方向 去重，喊单时点以首次为准）
    by_key = {}
    if LEDGER.exists():
        for ln in LEDGER.read_text("utf-8").splitlines():
            if ln.strip():
                c = json.loads(ln)
                by_key[(c["blogger"], c["code"], c["date"], c["direction"])] = c
    added = 0
    for c in calls:
        k = (c["blogger"], c["code"], c["date"], c["direction"])
        if k not in by_key:
            by_key[k] = c
            added += 1
    calls = list(by_key.values())
    print(f"\n本次新增 {added} 条；台账累计 {len(calls)} 条。重评全部 buy（T+N 随时间补全）...")

    # 3) 重新评估所有 buy（mootdx 缓存；已到期的 T+N 会补上）→ 写台账 + 出报告
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        for c in calls:
            if c["direction"] == "buy":
                c["eval"] = forward_returns(c["code"], c["date"])
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    dates = sorted(c["date"] for c in calls)
    write_report(aggregate(calls), calls, dates[0], dates[-1])
    print(f"\n✅ 报告: {REPORT}\n   台账: {LEDGER}")
    return 0


def aggregate(calls):
    """按博主聚合已评估的 buy 喊单 → 排好序的 rows。"""
    by_blogger = {}
    for c in calls:
        if c["direction"] == "buy":
            by_blogger.setdefault(c["blogger"], []).append(c.get("eval"))

    def agg(hs):
        if not hs:
            return None
        ex = [h["excess"] for h in hs]
        return {"n": len(hs),
                "hit": sum(1 for h in hs if h["ret"] > 0) / len(hs),
                "win": sum(1 for x in ex if x > 0) / len(ex),
                "avg_ex": st.mean(ex), "med_ex": st.median(ex),
                "avg_ret": st.mean(h["ret"] for h in hs)}

    rows = []
    for blogger, evs in by_blogger.items():
        t1 = [_hold(v, 1) for v in evs if _hold(v, 1).get("mature")]
        t3 = [_hold(v, 3) for v in evs if _hold(v, 3).get("mature")]
        rows.append({"blogger": blogger, "n_buy": len(evs), "t1": agg(t1), "t3": agg(t3)})
    rows.sort(key=lambda r: (r["t1"]["avg_ex"] if r["t1"] else -9), reverse=True)
    return rows


def _hold(ev, N):
    """安全取 T+N 持有期结果（兼容 JSON 的字符串键）。"""
    return ((ev or {}).get("by_hold", {}) or {}).get(str(N), {}) or {}


def pct(x):
    return f"{x*100:+.1f}%" if x is not None else "—"


def write_report(rows, calls, d_from, d_to):
    L = []
    L.append("# 📊 博主成绩单（前瞻喊单 · 真实行情验证）\n")
    L.append(f"> 区间：{d_from} ~ {d_to} · 入场=喊单当日收盘 · 超额基准=中证1000ETF · 仅计前瞻买入、剔除事后炫耀\n")
    L.append("> ⚠️ 样本=本周,极小,结论仅供参考;长期累积才有统计意义。不构成投资建议。\n")
    L.append("\n## 排名（按 T+1 平均超额；样本<5 视为不足）\n")
    L.append("| 博主 | 买入喊单数 | T+1样本 | T+1命中率 | T+1跑赢基准 | T+1平均超额 | T+3平均超额 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in rows:
        t1, t3 = r["t1"], r["t3"]
        flag = "" if (t1 and t1["n"] >= 5) else " ⚠️样本少"
        L.append(f"| {r['blogger']}{flag} | {r['n_buy']} | {t1['n'] if t1 else 0} | "
                 f"{pct(t1['hit']) if t1 else '—'} | {pct(t1['win']) if t1 else '—'} | "
                 f"{pct(t1['avg_ex']) if t1 else '—'} | {pct(t3['avg_ex']) if t3 else '—'} |")
    # 审计：列出每条已到期 buy 喊单的真实结果
    L.append("\n## 逐条喊单审计（已到期的前瞻买入）\n")
    L.append("| 博主 | 日期 | 个股 | 喊单原文 | T+1超额 | T+3超额 |")
    L.append("|---|---|---|---|---|---|")
    audit = [c for c in calls if c["direction"] == "buy" and c.get("eval")]
    audit.sort(key=lambda c: (c["blogger"], c["date"]))
    for c in audit:
        h1, h3 = _hold(c["eval"], 1), _hold(c["eval"], 3)
        e1 = pct(h1["excess"]) if h1.get("mature") else "未到期"
        e3 = pct(h3["excess"]) if h3.get("mature") else "未到期"
        L.append(f"| {c['blogger']} | {c['date']} | {c['name']} | {c['clause']} | {e1} | {e3} |")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L), "utf-8")


if __name__ == "__main__":
    sys.exit(main())
