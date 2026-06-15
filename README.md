# bilibili-push

> 监控 B站 UP 主动态，有新动态就推送到你的手机/微信。零服务器——挂在 **GitHub Actions** 上自动跑。

![python](https://img.shields.io/badge/python-3.10+-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![platform](https://img.shields.io/badge/deploy-GitHub%20Actions-black)

## ✨ 特性

- 🔔 **多 UP 主监控**：在一个文本文件里维护名单，随时增删
- 📱 **多推送渠道**：Bark / 企业微信 / ntfy / Server酱 / Telegram，任选一个或多个，都免费可选
- 🧩 **视频 / 图文 / 文字 / 专栏 / 转发 / 直播** 等动态类型都能识别并附带链接
- 🤖 **GitHub Actions 定时运行**，无需服务器、关机也不影响
- 🛡️ 内置 **WBI 签名**、去重、失败重试、Cookie 失效告警

## 🧠 原理

B站没有官方的“动态更新”推送回调，只能**轮询**：每次运行拉取 UP 主动态列表，
和上次记录的 `state.json` 对比，发现新动态就推送，然后更新记录。

- 接口：`x/polymer/web-dynamic/v1/feed/space`，需 **WBI 签名**（已内置实现）。
- **必须带登录 Cookie**：游客请求会被风控拦截（HTTP 412），所以要提供一个 B站账号的 Cookie。
- 首次运行只建立基线、**不推送历史动态**；之后只推新发布的。

---

## 🚀 部署到 GitHub Actions（推荐）

### 第 0 步：拿到这套代码到你自己的仓库

Fork 本仓库，或把代码 clone 下来推到你自己的仓库。

> **建议用「公开（Public）仓库」**：Actions 分钟数免费且不限量，而 `state.json` 里只有动态 ID 和
> UP 主昵称、**不含任何密钥**，公开无妨。私有仓库也行，但每月仅 2000 分钟免费额度（见 [FAQ](#-常见问题)）。

### 第 1 步：准备两样东西

<details>
<summary><b>① B站 Cookie</b>（点开看获取步骤）</summary>

1. 电脑浏览器登录 B站，打开任意页面（如 https://space.bilibili.com ）。
2. 按 `F12` → **Network（网络）** 标签 → 刷新页面。
3. 点任意一条对 `bilibili.com` 的请求 → 右侧 **Headers** → 找到请求头里的 **`Cookie:`**。
4. 复制 `Cookie:` 后面**一整行**内容（含 `SESSDATA`、`buvid3`、`bili_jct` 等）。

⚠️ Cookie 等同于登录凭证，**只填进 GitHub Secrets，绝不要写进代码或公开**。
</details>

<details>
<summary><b>② 一个推送渠道</b>（点开看各渠道怎么拿）</summary>

| 渠道 | Secret 名 | 怎么拿 | 备注 |
| --- | --- | --- | --- |
| **Bark**（iOS，最简单） | `BARK_URL` | App Store 装 [Bark](https://apps.apple.com/app/id1403753865)，复制里面的推送地址（`https://api.day.app/xxxxx`） | 免费、无限量 |
| 企业微信群机器人 | `WECOM_WEBHOOK` | 企业微信建内部群 → 群设置 → 群机器人 → 添加 → 复制 Webhook | 免费、无限量 |
| ntfy | `NTFY_URL` | 装 [ntfy](https://ntfy.sh/) App，订阅一个自定义主题，地址即 `https://ntfy.sh/你的主题名` | 开源、跨平台 |
| Server酱 | `SERVERCHAN_SENDKEY` | [sct.ftqq.com](https://sct.ftqq.com/) 扫码登录复制 SendKey 并关注其公众号 | 免费版每天 5 条 |
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | 找 @BotFather 建 bot 拿 token；chat id 用 @userinfobot 获取 | 需能访问 Telegram |

</details>

### 第 2 步：配置 Secrets

仓库页面 → **Settings → Secrets and variables → Actions → 选 “Secrets” 标签 → New repository secret**，
添加：

- `BILI_COOKIE` —— 第 1 步复制的整行 Cookie
- 你所选渠道对应的 Secret（例如 `BARK_URL`）

### 第 3 步：填监控名单（两种方式，二选一）

**方式 A（推荐，fork 后不用动代码）：用仓库 Variable**

**Settings → Secrets and variables → Actions → 选 “Variables” 标签 → New repository variable**，
新建 **`BILI_SUBS`**，值为多行的“名字 链接”（在网页输入框里直接换行）：

```text
老番茄     https://space.bilibili.com/546195/dynamic
影视飓风   https://space.bilibili.com/946974/dynamic
```

以后增删 UP 主，回来改这个变量即可，无需改文件、无需提交。

> 为什么用 Variable 而不是 “Run workflow” 的输入框？因为 `workflow_dispatch` 输入**只对手动运行生效，
> 定时任务（cron）时拿不到**；而 Variable 对每次定时运行都有效。

**方式 B：编辑文件**

直接改仓库里的 [`subscriptions.txt`](subscriptions.txt)，把示例换成你要监控的 UP 主后提交。
（注意：一旦设了 `BILI_SUBS` 变量，本文件会被忽略，以变量为准。）

### 第 4 步：开启 Actions 并授予写权限

1. **Settings → Actions → General → Workflow permissions** → 选 **“Read and write permissions”** → Save。
   （工作流要把更新后的 `state.json` 提交回仓库，需要写权限，否则会推送失败。）
2. 打开 **Actions** 标签页，若提示启用工作流就点启用（Fork 来的仓库默认会暂停定时任务）。

### 第 5 步：跑起来

1. **Actions** → 左侧选「B站动态推送」→ 右上 **Run workflow** 手动触发一次。
2. 首次成功会收到一条 **“✅ B站动态监控已启动”** 推送，并自动提交一个 `state.json`。
3. 之后它按 [`.github/workflows/bili-dynamic.yml`](.github/workflows/bili-dynamic.yml) 里的 `cron`
   **每 10 分钟自动运行**，UP 主发新动态就会推给你。

> 改频率就改 workflow 里的 `cron`（如 `*/30 * * * *` 每 30 分钟）。GitHub 定时任务最快约 5 分钟一次，
> 高峰期可能延迟几分钟，属正常现象。

---

## 💻 本地 / 服务器运行（可选）

```bash
pip install -r requirements.txt
cp .env.example .env      # 编辑 .env：填 BILI_COOKIE 和任一推送渠道
# 监控名单在 subscriptions.txt 里维护
python bili_push.py
```

配合 `cron` / `launchd` / `systemd timer` 定时执行即可常驻。

## ➕ 增删监控的 UP 主

两种方式，**设了变量就以变量为准、文件被忽略**：

- **仓库 Variable `BILI_SUBS`**（推荐）：多行 `名字 链接`，在 GitHub 网页上改，立即对下次运行生效，不用动代码。
- **`subscriptions.txt` 文件**：每行一个，格式同上。

```text
老番茄     https://space.bilibili.com/546195/dynamic
影视飓风   https://space.bilibili.com/946974/dynamic
# 直接写 UID 也行，名字可省略；纯 UID 也能用环境变量 BILI_UIDS 临时补充
2267573
```

`#` 开头是注释，空行忽略；名字只是备注，脚本以链接里的 UID 为准。新加入的 UP 主只建立基线、**不补推历史**。

## ❓ 常见问题

- **HTTP 412 / 接口 `-352`**：被风控拦截，一般是 Cookie 失效或太频繁。先确认 Cookie 有效、放宽 `cron` 间隔。
  所有 UP 主都失败时，会给你发一条“⚠️ 监控异常”提醒（每 6 小时最多一次）。
- **Cookie 过期**：重新取一次，更新 Secret `BILI_COOKIE` 即可，其余不动。
- **Actions 免费额度**：公开仓库不限量；私有仓库每月 2000 分钟，监控较多 UP 主时建议把 `cron` 放宽到 `*/30`。
- **会重复推送吗**：不会。用 `last_ts` 水位线 + 已读 ID 双重去重；推送失败的条目下次自动重试。
- **想推到微信但不想被 Server酱 限额**：用**企业微信群机器人**（免费无限量），或用 Bark / ntfy。

## 🗂️ 项目结构

| 文件 | 作用 |
| --- | --- |
| [`bili_push.py`](bili_push.py) | 主程序：WBI 签名、拉取动态、对比、多渠道推送 |
| [`subscriptions.txt`](subscriptions.txt) | 监控名单（要加/删 UP 主就改这里） |
| [`.github/workflows/bili-dynamic.yml`](.github/workflows/bili-dynamic.yml) | GitHub Actions 定时任务 |
| [`.env.example`](.env.example) | 本地运行的配置模板 |
| `state.json` | 运行时自动生成/更新，记录每个 UP 主已推送到哪 |

## ⚠️ 免责声明

本项目仅用于个人学习与信息订阅，使用的是 B站 Web 端公开接口。请合理设置轮询频率、遵守
B站的服务条款，**勿用于商业用途或高频抓取**。接口为非官方接口，可能随 B站调整而失效。
使用本工具产生的一切后果由使用者自行承担。

## 📄 License

[MIT](LICENSE) © 2026 Fly-Pluche
