# bilibili-push

> 监控 B站 UP 主动态，有新动态就推送到你的手机/微信。
> **推荐部署在你自己的电脑/服务器上** —— B站 对云/机房 IP 风控严格，GitHub Actions 多半跑不通（见下方 ⚠️）。

![python](https://img.shields.io/badge/python-3.10+-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![deploy](https://img.shields.io/badge/deploy-self--hosted-orange)

## ✨ 特性

- 🔔 **多 UP 主监控**：在一个文本文件里维护名单，随时增删
- 📱 **多推送渠道**：企业微信 / Bark / ntfy / Server酱 / Telegram（⚠️ **目前作者只在企业微信(WeCom)上完整实测通过**，其余渠道按文档实现、未逐一验证，可能需要自行调试，欢迎反馈/PR）
- 🧩 **视频 / 图文 / 文字 / 专栏 / 转发 / 直播** 等动态类型都能识别并附带链接
- 🛡️ 内置 **WBI 签名**、去重、失败重试、Cookie 失效告警

## ⚠️ 重要：别用 GitHub Actions 跑 B站（会被风控）

B站 对**数据中心 / 云服务器 IP** 风控严格。GitHub Actions 的 runner 跑在 Azure 机房 IP 上，
**即使 Cookie 完全有效**，请求动态接口也会被拦截、返回 **HTTP 412**。

> 实测对比（同一个有效 Cookie）：
> - 在**住宅网络**（自己的电脑 / 家庭宽带）→ ✅ 正常拉取
> - 在 **GitHub Actions**（机房 IP）→ ❌ 全部 412 失败

所以：

- ✅ **推荐**：部署在**自己的电脑或服务器**上（住宅 IP 最稳；或出口 IP 未被 B站风控的环境 / 代理）。见 [🖥️ 自建部署](#️-自建部署推荐)。
- ⚠️ GitHub Actions、以及大多数云 VPS（同为机房 IP）→ 抓取 B站 这一步大概率持续 412，不可靠。仓库里仍保留 Actions 配置，但**不建议用它跑 B站**。

> 注：被拦的只是「抓取 B站」这一步；推送渠道（企业微信等）本身在任何环境都能用。

## 🧠 原理

B站没有官方的“动态更新”推送回调，只能**轮询**：每次运行拉取 UP 主动态列表，
和上次记录的 `state.json` 对比，发现新动态就推送，然后更新记录。

- 接口：`x/polymer/web-dynamic/v1/feed/space`，需 **WBI 签名**（已内置实现）。
- **必须带登录 Cookie**：游客请求会被风控拦截（HTTP 412），所以要提供一个 B站账号的 Cookie。
- 首次运行只建立基线、**不推送历史动态**；之后只推新发布的。

---

## 🖥️ 自建部署（推荐）

**前提**：一台能正常访问 B站 的机器（住宅 IP 最稳；若用代理，需保证**代理出口 IP 没被 B站风控**）。

### 1. 拿到代码并装依赖

```bash
git clone https://github.com/Fly-Pluche/bilibili-push.git
cd bilibili-push
pip install -r requirements.txt   # 只依赖 requests
```

### 2. 配置

```bash
cp .env.example .env
```

编辑 `.env`，至少填两项：

- `BILI_COOKIE` —— 浏览器里复制的整行 Cookie（含 `SESSDATA`，获取方式见 [附录](#-附录获取-b站-cookie)）
- 一个推送渠道，例如 `WECOM_WEBHOOK`（见 [推送渠道](#-推送渠道)）

再编辑 [`subscriptions.txt`](subscriptions.txt) 填你要监控的 UP 主（格式见文件内说明）。

### 3. 先手动跑一次

```bash
python bili_push.py
```

看到「✅ B站动态监控已启动」推送、日志里各 UP 主「首次建立基线」即正常。

### 4. 设置定时任务（cron）

`crontab -e`，按需加入。**示例：工作日 8:30–15:00、每 5 分钟一次**（cron 用系统本地时区，确认机器时区是你要的）：

```cron
30-55/5 8 * * 1-5  cd /path/to/bilibili-push && python3 bili_push.py >> run.log 2>&1
*/5 9-14 * * 1-5   cd /path/to/bilibili-push && python3 bili_push.py >> run.log 2>&1
0 15 * * 1-5       cd /path/to/bilibili-push && python3 bili_push.py >> run.log 2>&1
```

> **需要代理才能上外网？** 在命令里先导出代理变量即可，例如：
> ```cron
> */5 9-14 * * 1-5  cd /path/to/bilibili-push && https_proxy=http://IP:PORT http_proxy=http://IP:PORT python3 bili_push.py >> run.log 2>&1
> ```
> 脚本用 `requests`，会自动走 `http_proxy`/`https_proxy` 环境变量。

> macOS 可改用 `launchd`；Linux 服务器也可用 `systemd timer`，原理相同。

---

## 🤖 GitHub Actions 部署（⚠️ 不建议用于 B站）

> 如前所述，GitHub Actions 的机房 IP 抓取 B站 会被 412 拦截，**这套基本跑不通**。
> 仅在你的运行环境出口 IP 未被 B站风控时才考虑。配置方式如下（点开）。

<details>
<summary>展开 GitHub Actions 配置步骤</summary>

1. Fork 本仓库（建议公开仓库，Actions 分钟数不限量；`state.json` 不含密钥）。
2. **Settings → Secrets and variables → Actions → Secrets**：加 `BILI_COOKIE` 和一个推送渠道（如 `WECOM_WEBHOOK`）。
3. 监控名单：在 **Variables** 标签建多行变量 `BILI_SUBS`（每行“名字 链接”），或直接编辑 `subscriptions.txt`。
   设了 `BILI_SUBS` 就以它为准。
4. **Settings → Actions → General → Workflow permissions** → 选 **Read and write**（用于回写 `state.json`）。
5. **Actions** 页面启用工作流 → **Run workflow**。定时规则见 [`.github/workflows/bili-dynamic.yml`](.github/workflows/bili-dynamic.yml) 的 `cron`。

</details>

---

## 📮 推送渠道

在 `.env`（自建）或仓库 Secrets（Actions）里配置，**任选一个或多个**：

| 渠道 | 环境变量 | 怎么拿 | 状态 |
| --- | --- | --- | --- |
| **企业微信群机器人** | `WECOM_WEBHOOK` | 企业微信建内部群 → 群设置 → 群机器人 → 添加 → 复制 Webhook | ✅ **已实测** |
| Bark（iOS） | `BARK_URL` | App Store 装 [Bark](https://apps.apple.com/app/id1403753865)，复制推送地址 `https://api.day.app/xxxxx` | 未逐一验证 |
| ntfy | `NTFY_URL` | 装 [ntfy](https://ntfy.sh/) App，订阅一个主题，地址即 `https://ntfy.sh/你的主题名` | 未逐一验证 |
| Server酱 | `SERVERCHAN_SENDKEY` | [sct.ftqq.com](https://sct.ftqq.com/) 扫码登录复制 SendKey 并关注公众号 | 未逐一验证（免费版每天 5 条） |
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | @BotFather 建 bot 拿 token；chat id 用 @userinfobot 获取 | 未逐一验证 |

> 目前作者只在**企业微信**上完整验证过端到端推送；其余渠道代码按各自文档实现，但未逐一实测，使用时如遇问题欢迎提 Issue / PR。

## ➕ 增删监控的 UP 主

编辑 [`subscriptions.txt`](subscriptions.txt)，每行一个，格式 `名字 链接`：

```text
老番茄     https://space.bilibili.com/546195/dynamic
影视飓风   https://space.bilibili.com/946974/dynamic
# 直接写 UID 也行，名字可省略
2267573
```

`#` 开头是注释，空行忽略；名字只是备注，脚本以链接里的 UID 为准。新加入的 UP 主只建立基线、**不补推历史**。
（GitHub Actions 用户也可改用仓库 Variable `BILI_SUBS` 多行配置，设了它就忽略本文件。）

## ❓ 常见问题

- **全部 UP 主 HTTP 412 失败**：最常见是**运行环境的 IP 被 B站风控**（GitHub Actions / 云 VPS 等机房 IP）。
  换到住宅 IP / 自己的机器，或换出口未被风控的代理。其次才是 Cookie 失效。
- **Cookie 多久过期 / 怎么看**：过期时间就写在 `SESSDATA` 里（中间那段时间戳）。一般能用几个月；
  主动退出登录、改密码会提前失效。换 Cookie 只需更新 `.env` 里的 `BILI_COOKIE`。
- **会重复推送吗**：不会。用 `last_ts` 水位线 + 已读 ID 双重去重；推送失败的条目下次自动重试。
- **想推到微信但不想被 Server酱 限额**：用**企业微信群机器人**（免费、无限量）。

## 🗂️ 项目结构

| 文件 | 作用 |
| --- | --- |
| [`bili_push.py`](bili_push.py) | 主程序：WBI 签名、拉取动态、对比、多渠道推送 |
| [`subscriptions.txt`](subscriptions.txt) | 监控名单（要加/删 UP 主就改这里） |
| [`.env.example`](.env.example) | 自建部署的配置模板 |
| [`.github/workflows/bili-dynamic.yml`](.github/workflows/bili-dynamic.yml) | GitHub Actions 配置（⚠️ 抓 B站 多半被 412） |
| `state.json` | 运行时自动生成/更新，记录每个 UP 主已推送到哪 |

## 📎 附录：获取 B站 Cookie

1. 电脑浏览器登录 B站，打开任意页面（如 https://space.bilibili.com ）。
2. 按 `F12` → **Network（网络）** 标签 → 刷新页面。
3. 点任意一条对 `bilibili.com` 的请求 → 右侧 **Headers** → 找到请求头里的 **`Cookie:`**。
4. 复制 `Cookie:` 后面**一整行**内容（含 `SESSDATA`、`buvid3`、`bili_jct` 等）。

> ⚠️ Cookie 等同于登录凭证，只填进本地 `.env` 或 GitHub Secrets，**绝不要写进代码或公开提交**。

## ⚠️ 免责声明

本项目仅用于个人学习与信息订阅，使用的是 B站 Web 端公开接口。请合理设置轮询频率、遵守
B站的服务条款，**勿用于商业用途或高频抓取**。接口为非官方接口，可能随 B站调整而失效。
使用本工具产生的一切后果由使用者自行承担。

## 📄 License

[MIT](LICENSE) © 2026 Fly-Pluche
