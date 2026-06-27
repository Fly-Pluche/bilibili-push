#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从博主动态里抽取「可打分的前瞻喊单」。

严谨第一：只把**明确的、前瞻的买入/关注**计入打分，并**剔除事后炫耀**。
分类（按离个股最近的关键词）：
  - buy  : 今日关注/今日计划/方向/低吸/调仓进/新开/看好/布局/可以考虑… （前瞻买入，计入打分）
  - sell : 止盈/止损/出局/落袋/减仓/走了… （卖出，单独统计，不计入买入命中率）
  - 跳过 : 事后炫耀（已翻X倍、喜提X板、恭喜吃肉、X涨停了、分享的…）、纯持有、纯闲聊
"出局A 上车B" 这种同句两向：按"离个股最近的关键词"分别定向。
"""

import re

from stocklist import Resolver  # 同目录直接运行

# 事后炫耀 / 结果陈述（强标记：出现即判定该分句为"晒结果"，跳过）。
# 不用裸"昨天/昨日"——那常是"昨天高点"这种参照位，会误杀真喊单。
HINDSIGHT = ["已翻", "倍已成", "已成", "喜提", "连板了", "恭喜", "吃肉", "战绩", "兑现",
             "涨停了", "封板了", "拿下", "收获", "马失前蹄", "分享的"]
# 前瞻买入 / 关注
# 注：去掉了"可以做"（"可以做高抛落袋/做T"语义含糊，易误判为买入）
BUY = ["今日关注", "今日计划", "今日方向", "方向参考", "今日分享", "今日看点", "今日早茶",
       "关注", "计划", "低吸", "可以看看", "可以考虑", "考虑进", "进场", "上车", "调仓到",
       "调仓进", "新开", "看好", "布局", "埋伏", "留意", "可以博弈",
       "开了", "我开", "切入", "新入", "可以关注", "我b了", "看点"]
NEG = "不别勿没未"  # 买入关键词紧跟在否定词后（"不看好""没关注"）→ 不算买入
# 卖出 / 离场
SELL = ["止盈", "止损", "出局", "可以走", "走了", "走一半", "落袋", "清仓", "减仓", "卖了",
        "卖飞", "撤退", "撤了", "离场", "出来", "出掉", "我出", "高抛", "我走"]


MAX_DIST = 25  # 个股离关键词超过这么多字就不归属（低置信度，宁可不计）


def _sentences(text: str):
    # 只按句末标点/换行切句，保留逗号——"今日关注：A，B，C" 整列留在一句里
    return [c.strip() for c in re.split(r"[。；！\n]", text) if c.strip()]


def _signals(nsent: str):
    """句中所有信号位置 [(pos, dir, kw)]，dir ∈ buy/sell/skip(事后)。"""
    out = []
    for k in BUY:
        i = nsent.find(k)
        if i >= 0:
            if i > 0 and nsent[i - 1] in NEG:   # "不看好/没关注" → 跳过该买入信号
                continue
            out.append((i, "buy", k))
    for k in SELL:
        i = nsent.find(k)
        if i >= 0:
            out.append((i, "sell", k))
    for k in HINDSIGHT:
        i = nsent.find(k)
        if i >= 0:
            out.append((i, "skip", k))
    return out


def extract_calls(text: str, resolver: Resolver) -> list[dict]:
    """返回 [{code,name,direction('buy'/'sell'),kind,clause}]。

    对每只个股，取『句内离它最近的信号』定方向；最近的是"事后炫耀"或没有信号/太远 → 不计。
    """
    out, seen = [], set()
    for sent in _sentences(text):
        nsent = resolver.norm(sent)
        stocks = resolver.find_with_pos(sent)
        if not stocks:
            continue
        sigs = _signals(nsent)
        if not sigs:
            continue
        for code, name, spos in stocks:
            pos, direction, kind = min(sigs, key=lambda s: abs(s[0] - spos))
            if direction == "skip" or abs(pos - spos) > MAX_DIST:
                continue  # 离它最近的是"晒结果"，或关键词太远 → 不计
            key = (code, direction)
            if key in seen:
                continue
            seen.add(key)
            out.append({"code": code, "name": name, "direction": direction,
                        "kind": kind, "clause": sent[:60]})
    return out


if __name__ == "__main__":
    r = Resolver()
    samples = [
        ("无敌姜神", "今日关注 双星新材、福达合金 买卖点自行把握"),
        ("一念斩龙", "早上分享的长电科技涨停了，今天永鼎股份也强势涨停了"),  # 事后→跳过
        ("龙虎分析", "今日喜提3个板！明天继续布局"),  # 纯炫耀→空
        ("料市如神", "新莱、利和、晶方 拿不住的就止盈"),  # sell
        ("疯狂猩猩四", "目前已经调仓到了宝鼎科技"),  # buy(调仓)
        ("观势浮生", "出局江海 上车京东方A"),  # 江海sell + 京东方buy（最近关键词）
        ("礼貌的大帅", "多氟多跌破昨天高点，已分批落袋完"),  # sell（"昨天高点"不再误杀）
        ("龙虎分析", "今日关注：三安光电，万润股份，盛视科技，京泉华"),  # 多只buy
    ]
    for who, txt in samples:
        print(f"\n[{who}] {txt}")
        for c in extract_calls(txt, r):
            print(f"    {c['direction']:4} {c['name']}({c['code']})  ←{c['kind']}")
