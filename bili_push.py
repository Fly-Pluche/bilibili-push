#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站 UP 主动态推送
=================
定时轮询 subscriptions.txt 里的 B站 UP 主，发现新动态后推送（支持
Bark / 企业微信 / ntfy / Server酱 / Telegram 等多渠道）。B站没有官方更新回调，只能轮询。

每次运行：
  1) 取 WBI 签名所需的 img_key / sub_key（nav 接口）
  2) 对空间动态接口（web-dynamic/v1/feed/space）做 WBI 签名后请求（需登录 Cookie）
  3) 与上次记录的 state.json 对比，找出新动态
  4) 多渠道推送，并更新 state.json

环境变量（见 README）：
  BILI_COOKIE        必填，浏览器里复制的完整 Cookie（含 SESSDATA 等）
  推送渠道           至少配一个：BARK_URL / WECOM_WEBHOOK / NTFY_URL / SERVERCHAN_SENDKEY
                     / TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
  BILI_UIDS          可选，临时补充的 UID（逗号分隔）；常规增删请改 subscriptions.txt
  SUBS_FILE          可选，订阅列表路径，默认 ./subscriptions.txt
  STATE_FILE         可选，状态文件路径，默认 ./state.json
  MAX_PUSH_PER_RUN   可选，单 UP 主单次最多推送条数（防刷屏），默认 10
"""

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
SUBS_FILE = Path(os.environ.get("SUBS_FILE", "subscriptions.txt"))
MAX_PUSH_PER_RUN = int(os.environ.get("MAX_PUSH_PER_RUN", "10"))
SEEN_CAP = 120  # 每个 UP 主最多记住多少条已见 ID

# 人类可读的动态类型
TYPE_LABELS = {
    "DYNAMIC_TYPE_AV": "投稿视频",
    "DYNAMIC_TYPE_UGC_SEASON": "合集更新",
    "DYNAMIC_TYPE_WORD": "文字动态",
    "DYNAMIC_TYPE_DRAW": "图文动态",
    "DYNAMIC_TYPE_ARTICLE": "专栏文章",
    "DYNAMIC_TYPE_FORWARD": "转发动态",
    "DYNAMIC_TYPE_LIVE": "直播",
    "DYNAMIC_TYPE_LIVE_RCMD": "直播",
    "DYNAMIC_TYPE_PGC": "番剧/影视",
    "DYNAMIC_TYPE_MUSIC": "音频投稿",
    "DYNAMIC_TYPE_COMMON_SQUARE": "动态",
    "DYNAMIC_TYPE_COMMON_VERTICAL": "动态",
}


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# WBI 签名
# ---------------------------------------------------------------------------

# B站 WBI 混淆置换表（固定常量）
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def _get_mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB)[:32]


def get_wbi_keys(session: requests.Session) -> tuple[str, str]:
    """从 nav 接口取 img_key / sub_key（带登录 Cookie 时返回更稳定）。"""
    r = session.get(
        "https://api.bilibili.com/x/web-interface/nav", timeout=15
    )
    data = r.json()
    wbi = data["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
    return img_key, sub_key


def sign_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """对参数做 WBI 签名，返回带 wts/w_rid 的新参数字典。"""
    mixin_key = _get_mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))
    # 过滤值中的特殊字符
    params = {
        k: "".join(c for c in str(v) if c not in "!'()*")
        for k, v in params.items()
    }
    query = urllib.parse.urlencode(params)
    params["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return params


# ---------------------------------------------------------------------------
# B站客户端
# ---------------------------------------------------------------------------


class BiliClient:
    def __init__(self, cookie: str):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Referer": "https://space.bilibili.com/",
                "Origin": "https://space.bilibili.com",
                "Accept": "application/json, text/plain, */*",
                "Cookie": cookie.strip(),
            }
        )
        self._img_key = None
        self._sub_key = None

    def _ensure_keys(self):
        if self._img_key is None:
            self._img_key, self._sub_key = get_wbi_keys(self.session)
            log(f"WBI keys ok: img={self._img_key[:8]}… sub={self._sub_key[:8]}…")

    def fetch_space_dynamics(self, uid: str) -> list[dict]:
        """拉取某 UP 主空间动态的第一页（最新动态）。"""
        self._ensure_keys()
        params = {
            "offset": "",
            "host_mid": uid,
            "timezone_offset": "-480",
            "platform": "web",
            "features": "itemOpusStyle,opusBigCover,onlyfansVote,decorationCard,forwardListHidden,ugcDelete",
            "web_location": "333.1387",
        }
        signed = sign_wbi(params, self._img_key, self._sub_key)
        r = self.session.get(
            "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
            params=signed,
            timeout=20,
        )
        if r.status_code == 412:
            raise RuntimeError(
                "HTTP 412 被风控拦截：Cookie 可能无效/过期，或请求过于频繁。"
            )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"接口返回错误 code={data.get('code')} msg={data.get('message')}"
            )
        return data.get("data", {}).get("items", []) or []


# ---------------------------------------------------------------------------
# 动态内容解析
# ---------------------------------------------------------------------------


def _major_text(major: dict) -> tuple[str, str]:
    """从 major 区块提取 (标题, 正文)。

    注意：B站返回的 major 会带上所有可能的子键，未使用的为 None，
    所以必须用 .get() 取值并判断真值，不能用 `key in major` 判断。
    """
    if not major:
        return "", ""
    a = major.get("archive")
    if a:  # 视频
        return f"📺 {a.get('title', '')}", a.get("desc", "")
    op = major.get("opus")
    if op:  # 新版统一图文/专栏/长文
        title = (op.get("title") or "").strip()
        summary = ((op.get("summary") or {}).get("text") or "")
        return title, summary
    ar = major.get("article")
    if ar:  # 旧版专栏
        return f"📄 {ar.get('title', '')}", ar.get("desc", "")
    if major.get("live_rcmd") or major.get("live"):
        return "🔴 直播", ""
    m = major.get("music")
    if m:
        return f"🎵 {m.get('title', '')}", ""
    p = major.get("pgc")
    if p:
        return f"🎬 {p.get('title', '')}", ""
    return "", ""


def extract(item: dict) -> dict:
    """把一条动态归一化成 {id, ts, type, label, author, title, text, url, is_top}。"""
    mods = item.get("modules", {}) or {}
    author = mods.get("module_author", {}) or {}
    md = mods.get("module_dynamic", {}) or {}
    tag = (mods.get("module_tag") or {}).get("text", "")

    dyn_type = item.get("type", "")
    label = TYPE_LABELS.get(dyn_type, "动态")

    # 正文：desc.text + major
    text_parts = []
    desc = md.get("desc")
    if desc and desc.get("text"):
        text_parts.append(desc["text"])
    m_title, m_body = _major_text(md.get("major") or {})
    if m_title:
        text_parts.append(m_title)
    if m_body:
        text_parts.append(m_body)

    # 转发：附上被转发的原动态摘要
    if dyn_type == "DYNAMIC_TYPE_FORWARD" and item.get("orig"):
        orig = extract(item["orig"])
        text_parts.append(f"\n↩️ 转发自 @{orig['author']}：{orig['title']} {orig['text']}".rstrip())

    text = "\n".join(p for p in text_parts if p).strip()

    return {
        "id": item.get("id_str", ""),
        "ts": int(author.get("pub_ts", 0) or 0),
        "type": dyn_type,
        "label": label,
        "author": author.get("name", ""),
        "title": (text.splitlines()[0][:40] if text else label),
        "text": text,
        "url": f"https://t.bilibili.com/{item.get('id_str', '')}",
        "is_top": "置顶" in tag,
    }


def fmt_time(ts: int) -> str:
    if not ts:
        return ""
    # 转成北京时间（UTC+8）
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts + 8 * 3600))


# ---------------------------------------------------------------------------
# 推送渠道（可同时配置多个；任一成功即视为推送成功）
#   想用哪个就配哪个的环境变量，全部免费可选：
#     BARK_URL            Bark（iOS，最简单，免费）：形如 https://api.day.app/你的key
#     WECOM_WEBHOOK       企业微信群机器人 Webhook（免费、无限量，推到企业微信）
#     NTFY_URL            ntfy（开源跨平台，免费）：形如 https://ntfy.sh/你的主题
#     SERVERCHAN_SENDKEY  Server酱（推到微信，有免费额度）
#     TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   Telegram Bot（免费，需能访问 TG）
# ---------------------------------------------------------------------------


def _push_bark(title, body, url):
    base = os.environ["BARK_URL"].strip().rstrip("/")
    params = {"group": "B站动态"}
    if url:
        params["url"] = url
    path = f"{base}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
    r = requests.get(path, params=params, timeout=15)
    return r.status_code == 200 and r.json().get("code") == 200


def _push_wecom(title, body, url):
    hook = os.environ["WECOM_WEBHOOK"].strip()
    content = f"**{title}**\n\n{body}" + (f"\n\n[查看动态]({url})" if url else "")
    r = requests.post(hook, json={"msgtype": "markdown", "markdown": {"content": content[:4000]}}, timeout=15)
    return r.status_code == 200 and r.json().get("errcode") == 0


def _push_ntfy(title, body, url):
    ep = os.environ["NTFY_URL"].strip()
    headers = {"Title": "Bilibili Dynamic"}  # 头部需 ASCII，中文标题放正文里
    if url:
        headers["Click"] = url
    text = f"{title}\n\n{body}"
    r = requests.post(ep, data=text.encode("utf-8"), headers=headers, timeout=15)
    return r.status_code in (200, 201)


def _push_serverchan(title, body, url):
    key = os.environ["SERVERCHAN_SENDKEY"].strip()
    if key.startswith("sctp"):  # Server酱³：key 形如 sctp{通道号}t{随机串}
        channel = key[4:].split("t", 1)[0]
        ep = f"https://{channel}.push.ft07.com/send/{key}.send"
    else:  # Server酱·Turbo
        ep = f"https://sctapi.ftqq.com/{key}.send"
    desp = body + (f"\n\n[查看动态]({url})" if url else "")
    r = requests.post(ep, data={"title": title[:100], "desp": desp}, timeout=15)
    return r.status_code == 200 and str(r.json().get("code", "")) == "0"


def _push_telegram(title, body, url):
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    chat = os.environ["TELEGRAM_CHAT_ID"].strip()
    text = f"*{title}*\n\n{body}" + (f"\n\n[查看动态]({url})" if url else "")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    return r.status_code == 200 and r.json().get("ok") is True


# (显示名, 触发用的环境变量, 推送函数)
_CHANNELS = [
    ("Bark", "BARK_URL", _push_bark),
    ("企业微信", "WECOM_WEBHOOK", _push_wecom),
    ("ntfy", "NTFY_URL", _push_ntfy),
    ("Server酱", "SERVERCHAN_SENDKEY", _push_serverchan),
    ("Telegram", "TELEGRAM_BOT_TOKEN", _push_telegram),
]


def active_channels():
    """返回当前已配置的推送渠道列表 [(名称, 函数), ...]。"""
    out = []
    for name, envk, fn in _CHANNELS:
        if os.environ.get(envk, "").strip():
            if name == "Telegram" and not os.environ.get("TELEGRAM_CHAT_ID", "").strip():
                continue  # Telegram 还需要 chat id
            out.append((name, fn))
    return out


def notify(title: str, body: str, url: str = None) -> bool:
    """向所有已配置渠道推送，任一成功即返回 True。"""
    ok = False
    for name, fn in active_channels():
        try:
            if fn(title, body, url):
                ok = True
            else:
                log(f"{name} 推送返回失败")
        except Exception as e:  # noqa: BLE001
            log(f"{name} 推送异常: {e}")
    return ok


# ---------------------------------------------------------------------------
# 状态读写
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            log("state.json 解析失败，按空状态处理")
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")


# ---------------------------------------------------------------------------
# 订阅列表
#   优先级：环境变量 BILI_SUBS（多行，适合 fork 后在仓库 Variables 里配置）
#          → 否则用 subscriptions.txt 文件。
#   另外 BILI_UIDS（逗号分隔的纯 UID）总会合并进来，便于临时补充。
# ---------------------------------------------------------------------------

_BILI_RE = re.compile(r"(?:https?://)?space\.bilibili\.com/(\d+)", re.I)


def _name_before(line: str, start: int, end: int = None) -> str:
    """取匹配位置之前（必要时加上之后）的文字作为备注名。"""
    s = line[:start] if end is None else (line[:start] + " " + line[end:])
    return s.strip().strip(",=|:：- ").strip()


def _parse_sub_line(raw: str):
    """解析一行 “名字 链接/UID” → (uid, name)；注释/空行/无法识别返回 None。"""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    m = _BILI_RE.search(line)
    if m:
        return (m.group(1), _name_before(line, m.start()))
    m2 = re.search(r"\d{5,}", line)  # 纯数字 → 当作 UID
    if m2:
        return (m2.group(0), _name_before(line, m2.start(), m2.end()))
    return None


def load_subscriptions() -> list[tuple[str, str]]:
    """读取订阅列表 → [(uid, name), ...]，按 UID 去重（保留首次出现的名字）。

    来源优先级：
      1) 环境变量 BILI_SUBS（多行，每行 “名字 链接/UID”）—— fork 后在仓库 Variables 里设一次即可，
         设了它就忽略 subscriptions.txt 文件；
      2) subscriptions.txt 文件（未设 BILI_SUBS 时使用）；
      3) 环境变量 BILI_UIDS（逗号分隔纯 UID）—— 始终合并，便于临时补充。
    """
    subs: list[tuple[str, str]] = []

    def _add_from(text: str, src: str):
        for raw in text.splitlines():
            parsed = _parse_sub_line(raw)
            if parsed:
                subs.append(parsed)
            elif raw.strip() and not raw.strip().startswith("#"):
                log(f"[{src}] 忽略无法识别的行: {raw!r}")

    env_subs = os.environ.get("BILI_SUBS", "").strip()
    if env_subs:
        _add_from(env_subs, "BILI_SUBS")             # 设了 Variable 就以它为准
    elif SUBS_FILE.exists():
        _add_from(SUBS_FILE.read_text("utf-8"), "subscriptions.txt")

    for u in os.environ.get("BILI_UIDS", "").replace("，", ",").split(","):
        u = u.strip()
        if u.isdigit():
            subs.append((u, ""))

    seen, out = set(), []
    for uid, name in subs:
        if uid not in seen:
            seen.add(uid)
            out.append((uid, name))
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def process_uid(client: BiliClient, uid: str, state: dict, display_name: str = "") -> int:
    """处理单个 UP 主，返回本次推送的条数。display_name 为订阅文件里的备注名（可选）。"""
    items = client.fetch_space_dynamics(uid)
    if not items:
        log(f"[{uid}] 未取到动态（可能该用户无动态或被限制）")
        return 0

    parsed = []
    for it in items:
        try:
            parsed.append(extract(it))
        except Exception as e:  # noqa: BLE001
            log(f"[{uid}] 跳过一条无法解析的动态 {it.get('id_str', '?')}: {e}")
    # 非置顶动态里最新的发布时间
    fresh = [p for p in parsed if not p["is_top"]]
    api_author = next((p["author"] for p in parsed if p["author"]), "")
    author = display_name or api_author or uid

    st = state.get(uid)
    pushed = 0

    if st is None:
        # 首次监控该 UP 主：仅建立基线，不推送历史动态
        state[uid] = {
            "author": author,
            "last_ts": max((p["ts"] for p in fresh), default=0),
            "seen_ids": [p["id"] for p in parsed][:SEEN_CAP],
        }
        log(f"[{uid}] {author} 首次建立基线，记录 {len(state[uid]['seen_ids'])} 条已读，不推送")
        return 0

    prev_seen = st.get("seen_ids", [])
    seen = set(prev_seen)
    last_ts = st.get("last_ts", 0)

    # 新动态：未见过 且 发布时间晚于基线（自动排除置顶旧动态/历史回填）
    new_items = sorted(
        (p for p in fresh if p["id"] and p["id"] not in seen and p["ts"] > last_ts),
        key=lambda p: p["ts"],  # 旧→新顺序推送
    )
    if len(new_items) > MAX_PUSH_PER_RUN:
        log(f"[{uid}] 新动态 {len(new_items)} 条超过上限，仅推送最新 {MAX_PUSH_PER_RUN} 条")
        new_items = new_items[-MAX_PUSH_PER_RUN:]

    new_last_ts = last_ts
    pushed_ids = []
    for p in new_items:
        title = f"📢 {p['author']} 发布了{p['label']}"
        body = (
            f"{p['author']} · {p['label']} · {fmt_time(p['ts'])}\n\n"
            f"{p['text'] or '(无文字内容)'}"
        )
        if not notify(title, body, p["url"]):
            # 推送失败：停在这里，不推进水位线，下次重试本条及更新的
            log(f"[{uid}] 推送失败，停止本轮，下次重试: {p['id']}")
            break
        pushed += 1
        pushed_ids.append(p["id"])
        new_last_ts = max(new_last_ts, p["ts"])  # 仅推进到已成功推送的最新一条
        log(f"[{uid}] 已推送: {p['label']} {p['id']} {p['title']}")

    # 更新状态：seen 只记基线 + 已成功推送的（失败的不计，保证重试）
    merged_seen = pushed_ids + [i for i in prev_seen if i not in set(pushed_ids)]
    state[uid] = {
        "author": author,
        "last_ts": new_last_ts,
        "seen_ids": merged_seen[:SEEN_CAP],
    }
    return pushed


def _load_dotenv():
    """本地运行时若存在 .env 就加载（GitHub Actions 上用真正的环境变量，不会有 .env）。"""
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


ALERT_THROTTLE_SEC = 6 * 3600  # 全部失败时，最多每 6 小时报警一次，避免刷屏


def main() -> int:
    _load_dotenv()
    cookie = os.environ.get("BILI_COOKIE", "").strip()
    subs = load_subscriptions()
    uids = [uid for uid, _ in subs]

    missing = []
    if not cookie:
        missing.append("BILI_COOKIE")
    if not subs:
        missing.append("订阅列表(subscriptions.txt 为空或缺失)")
    if not active_channels():
        missing.append("推送渠道(至少配一个: BARK_URL / WECOM_WEBHOOK / NTFY_URL / SERVERCHAN_SENDKEY / TELEGRAM_*)")
    if missing:
        log(f"缺少必填配置: {', '.join(missing)}")
        return 2
    log("已启用推送渠道: " + "、".join(n for n, _ in active_channels()))
    log(f"共监控 {len(subs)} 个 UP 主: " + "、".join(name or uid for uid, name in subs))

    first_ever = not STATE_FILE.exists()
    state = load_state()
    client = BiliClient(cookie)

    total = 0
    errors = []
    for i, (uid, name) in enumerate(subs):
        try:
            total += process_uid(client, uid, state, name)
        except Exception as e:  # noqa: BLE001
            log(f"[{uid}] 处理失败: {e}")
            errors.append(f"{name or uid}: {e}")
        save_state(state)  # 每个 UP 主处理完都落盘，避免中途失败丢状态
        if i < len(subs) - 1:
            time.sleep(3)  # 轻微间隔，降低风控概率

    if first_ever and any(u in state for u in uids):
        # 首次部署且至少有一个 UP 主拉取成功，发一条确认消息，方便确认推送链路正常
        names = "、".join(state[u].get("author", u) for u in uids if u in state)
        notify(
            "✅ B站动态监控已启动",
            f"已开始监控 {len([u for u in uids if u in state])} 个 UP 主：{names}\n\n"
            f"后续有新动态会自动推送到这里。\n\n（首次运行仅建立基线，不推送历史动态）",
        )
        log("已发送启动确认消息")

    log(f"本次完成，共推送 {total} 条" + (f"，{len(errors)} 个 UP 主出错" if errors else ""))

    # 所有 UP 主都失败（多半是 Cookie 过期/被风控）→ 限频报警，避免静默失效
    if subs and len(errors) == len(subs):
        meta = state.get("_meta", {})
        now = int(time.time())
        if now - meta.get("last_alert_ts", 0) > ALERT_THROTTLE_SEC:
            notify(
                "⚠️ B站动态监控异常",
                "所有 UP 主拉取都失败了，可能是 Cookie 过期或触发风控。\n\n"
                "请更新 GitHub Secrets 里的 BILI_COOKIE。\n\n"
                f"错误样例：{errors[0]}",
            )
            meta["last_alert_ts"] = now
            state["_meta"] = meta
            save_state(state)
            log("已发送异常报警")
        return 1  # 全失败时让 Actions 标红，便于察觉

    return 0  # 偶发的部分失败不影响整体，保持绿色


if __name__ == "__main__":
    sys.exit(main())
