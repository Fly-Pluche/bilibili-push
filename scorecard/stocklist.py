#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股股票名 → 代码 解析器（博主成绩单的地基）。

博主满嘴简称/错字/拼音首字母（"山东bx""江海gf""三安""德邦照明"），
识别错一只，整张成绩单就废了。所以这里的原则是 **宁缺毋滥（精确优先）**：
  1) 先用「全 A 股全名」精确匹配（最长优先，避免 "中材" 误吃 "中材科技"）；
  2) 再用人工维护的「别名/简称表」补常见简称；
  3) 对 gf=股份 / kj=科技 这类拼音首字母做有限的规范化；
  4) 匹配不到或有歧义 → 跳过并记录，绝不瞎猜。
全 A 股名单从东财拉一次后本地缓存。
"""

import json
import re
import time
from pathlib import Path

import requests

_DIR = Path(__file__).resolve().parent
_CACHE = _DIR / "data" / "stocklist.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# ── 人工维护的别名表：博主简称/错字 → 全名（精确，宁少毋错） ──────────────
# key 为博主常用写法，value 为标准全名（必须能在全A名单里精确命中）
ALIAS = {
    "京东方A": "京东方A", "京东方": "京东方A",
    "三安": "三安光电", "三安光电": "三安光电",
    "得邦照明": "得邦照明", "德邦照明": "得邦照明", "得邦": "得邦照明",
    "山东玻纤": "山东玻纤", "山东bx": "山东玻纤", "山玻": "山东玻纤",
    "永鼎": "永鼎股份", "中天": "中天科技", "亨通": "亨通光电",
    "江海": "江海股份", "中材": "中材科技", "中钨": "中钨高新",
    "长电": "长电科技", "太极": "太极实业", "晶方": "晶方科技",
    "多氟多": "多氟多", "莲花控股": "莲花控股", "莲花": "莲花控股",
    "康强": "康强电子", "康强电子": "康强电子",
    "圣泉": "圣泉集团", "大族": "大族激光", "云南锗业": "云南锗业",
    "雅克": "雅克科技", "长川": "长川科技", "德福": "德福科技",
    "中国巨石": "中国巨石", "巨石": "中国巨石", "石英": "石英股份",
    "凯盛": "凯盛科技", "宝鼎": "宝鼎科技", "联诚精密": "联诚精密",
    "泰和新材": "泰和新材", "泰和": "泰和新材", "泰坦": "泰坦股份",
    "盛视": "盛视科技", "华锋": "华锋股份", "麦格米特": "麦格米特",
    "埃斯顿": "埃斯顿", "杭电": "杭电股份", "金钼": "金钼股份",
    "通富微电": "通富微电", "通富": "通富微电", "德明利": "德明利",
    "TCL科技": "TCL科技", "中微半导": "中微半导", "兴森": "兴森科技",
    "利通电子": "利通电子", "利通": "利通电子", "安泰": "安泰科技",
    "博敏电子": "博敏电子", "博敏": "博敏电子", "彩虹股份": "彩虹股份",
    "昊华科技": "昊华科技", "昊华": "昊华科技", "超声电子": "超声电子",
    "新莱应材": "新莱应材", "新莱": "新莱应材", "利和兴": "利和兴",
    "天华新能": "天华新能", "天赐材料": "天赐材料", "江波龙": "江波龙",
    "中化国际": "中化国际", "光华科技": "光华科技", "光华": "光华科技",
    "洁美科技": "洁美科技", "洁美": "洁美科技", "双星新材": "双星新材",
    "双星": "双星新材", "菲利华": "菲利华", "瑞丰高材": "瑞丰高材",
    "中京电子": "中京电子", "中材科技": "中材科技", "顺络电子": "顺络电子",
    "顺络": "顺络电子", "立昂微": "立昂微", "火炬电子": "火炬电子",
    "天通股份": "天通股份", "华天科技": "华天科技", "蔚蓝锂芯": "蔚蓝锂芯",
    "帝尔激光": "帝尔激光", "有研新材": "有研新材", "生益科技": "生益科技",
    "生益": "生益科技", "章源钨业": "章源钨业", "金安": "金安国纪",
    "风华高科": "风华高科", "风华": "风华高科", "赛腾": "赛腾股份",
    "共达电声": "共达电声", "新洁能": "新洁能", "江化微": "江化微",
    "三孚股份": "三孚股份", "三孚": "三孚股份", "诺德股份": "诺德股份",
    "先导智能": "先导智能", "银之杰": "银之杰", "万润科技": "万润科技",
    "万润股份": "万润股份", "信维通信": "信维通信", "京泉华": "京泉华",
    "快克智能": "快克智能", "快克": "快克智能", "石英股份": "石英股份",
    "中天科技": "中天科技", "永鼎股份": "永鼎股份", "江海股份": "江海股份",
}

# "江海gf" → "江海股份"，"XXkj" → "XX科技" 等拼音首字母后缀
_SUFFIX = [("gf", "股份"), ("kj", "科技"), ("gufen", "股份")]


def _half(s: str) -> str:
    """全角 ASCII → 半角（京东方Ａ→京东方A、ＴＣＬ→TCL），并统一大写。"""
    out = []
    for ch in s:
        o = ord(ch)
        if o == 0x3000:
            out.append(" ")
        elif 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out).upper()


def _fetch_all_stocks() -> dict:
    """从东财翻页拉全 A 股 code→name（该接口单页上限 100），缓存到本地。"""
    out = {}
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"  # 沪深主板/创业板/科创板/北交所
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    pn, total = 1, None
    while True:
        params = {"pn": str(pn), "pz": "100", "po": "0", "np": "1", "fltt": "2",
                  "invt": "2", "fs": fs, "fields": "f12,f14"}
        d = sess.get(url, params=params, timeout=20).json().get("data") or {}
        if total is None:
            total = d.get("total", 0)
        diff = d.get("diff") or []
        if not diff:
            break
        for it in diff:
            code, name = it.get("f12"), it.get("f14")
            if code and name:
                out[code] = name
        pn += 1
        if total and len(out) >= total:
            break
        if pn > 80:  # 安全上限（~8000 只）
            break
        time.sleep(0.25)
    return out


def load_stocks(refresh: bool = False) -> dict:
    """返回 {code: name}，本地缓存（7 天）。"""
    if not refresh and _CACHE.exists():
        age = time.time() - _CACHE.stat().st_mtime
        if age < 7 * 86400:
            return json.loads(_CACHE.read_text("utf-8"))
    stocks = _fetch_all_stocks()
    if stocks:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(stocks, ensure_ascii=False), "utf-8")
        return stocks
    return json.loads(_CACHE.read_text("utf-8")) if _CACHE.exists() else {}


class Resolver:
    def __init__(self, refresh: bool = False):
        self.code2name = load_stocks(refresh)
        # 半角归一化后的 全名 → code（用于匹配；展示仍用原名 code2name）
        self._nname2code = {_half(n): c for c, n in self.code2name.items()}
        self._nnames_sorted = sorted(self._nname2code.keys(), key=len, reverse=True)
        # 别名：半角化的简称 → code（目标全名须在名单里）
        self.alias = {}
        for k, v in ALIAS.items():
            code = self._nname2code.get(_half(v))
            if code:
                self.alias[_half(k)] = code

    def _norm(self, text: str) -> str:
        t = _half(text)
        for suf, rep in _SUFFIX:
            t = re.sub(rf"([一-龥]{{1,4}}){_half(suf)}", rf"\1{rep}", t)
        return t

    def find(self, text: str) -> list[tuple[str, str]]:
        """从文本中提取所有命中的 (code, name)，去重，宁缺毋滥。"""
        return [(c, n) for c, n, _ in self.find_with_pos(text)]

    def find_with_pos(self, text: str) -> list[tuple[str, str, int]]:
        """返回 [(code, name, pos)]，pos 为在『归一化后文本』中的首次出现位置。"""
        t = self._norm(text)
        pos = {}  # code -> earliest index
        for alias in sorted(self.alias, key=len, reverse=True):
            i = t.find(alias)
            if i >= 0:
                code = self.alias[alias]
                pos[code] = min(pos.get(code, i), i)
        for nname in self._nnames_sorted:
            if len(nname) >= 3:
                i = t.find(nname)
                if i >= 0:
                    code = self._nname2code[nname]
                    pos[code] = min(pos.get(code, i), i)
        return [(c, self.code2name[c], p) for c, p in pos.items()]

    def norm(self, text: str) -> str:
        return self._norm(text)


if __name__ == "__main__":
    r = Resolver()
    print(f"全A股: {len(r.code2name)} 只 | 有效别名: {len(r.alias)}")
    tests = [
        "今日关注：长电科技、江海股份、中材科技",
        "三安一定要减仓做T；京东方A爆拉",
        "山东bx拿着的朋友等修复；江海gf不能走强出局",
        "得邦照明20日线附近震荡；永鼎可以做高抛低吸",
        "调仓到了石英股份；莲花控股不破5日线止盈",
        "中钨高新今天再次涨停",
    ]
    for s in tests:
        print(f"\n{s}\n  → {r.find(s)}")
