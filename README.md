# FinPulse 智能股票舆情平台

FinPulse 是软件工程课程项目，目前提供本机可运行的真实新闻研究流程：React + TypeScript + ECharts 前端、FastAPI 后端、MySQL / SQLite 账号与研究记录、Tavily 检索、AkShare 数据接入，以及兼容 Chat Completions 的大模型分析。

**团队共享 MySQL 与四个管理员的操作说明见 [数据库与管理员登录说明](数据库与管理员登录说明.md)。** 当前开发机已连接服务器 `finpulse_dev`；配置模板默认 SQLite，方便独立试运行。新成员共享服务器数据时，需要填写 MySQL 配置并具有 SSH 访问权限。

正式运行时，前端和后端共用 **http://localhost:8000/**。浏览器只访问本地后端，模型 API 密钥由后端读取。

## 1. 当前能做什么

| 功能 | 当前状态 |
| --- | --- |
| 财经阅读布局、股票检索、自选股添加/移除与切换 | 已实现；AkShare 交易所列表提供沪深 A 股名称、代码、拼音缩写搜索，添加后自动采集并分析，自选股按登录账号保存 |
| 日/周 K、MA5/10/20、成交量、MACD、RSI、新闻日期标记 | 已接入 AkShare / 腾讯历史日线；失败显示缺失或旧数据状态，Ctrl + 滚轮缩放 |
| 真实新闻检索、正文提取、清洗去重、记录查看 | 已接入主网站；Tavily Search / Extract + AkShare 东方财富新闻，保留来源和过滤原因 |
| 注册、登录、退出、刷新后保持登录 | 已实现；支持共享 MySQL 与独立 SQLite |
| 管理员登录与管理中心 | 已实现；角色校验、普通用户启停、管理记录、修改自己的网站密码 |
| AI 问答、新闻研判、模型连接测试 | 登录后调用真实模型；研判使用清洗后的真实材料，JSON 校验、格式失败重试、缓存与持久化 |
| 采集任务进度、消息筛选、研究数据导出 | 已实现；后台执行，任务归属当前账号，公开新闻快照共享，JSON 导出 |
| 社区样本、三类倾向比例、热词与极端阈值提示 | 基础版已实现；Tavily 检索股吧/雪球的独立帖子并逐条分类，缺少有效日期则不生成比例 |
| 每日晨报、推荐排序、历史记录、每日更新与推送 | 已实现；自选股/主题个性化排序、共享采集、每日调度、重启补做今日任务、邮件/PushPlus 结果与重试；真实送达仍需配置渠道后验收 |
| 新闻后 3/5 交易日价格观察 | 基础版已实现；使用真实日线计算，不等于策略回测胜率 |
| 实时报价、全市场恐慌贪婪指数、北交所/港股/美股股票库 | 尚未实现；页面展示真实历史收盘价，目前股票检索范围为沪深 A 股，不生成模拟替代值 |
| 阅读/回复量加权社区爬虫、KDJ/形态识别、组合风险、PDF 导出 | 仍属课程后续规划，不能视为已完成 |

**主看板已移除模拟行情与模拟新闻。** 首次启动没有数据时显示空状态；登录后点击「采集并分析」获取真实来源。股价采用行情源最后返回的日线收盘价，不是实时盘口。日期不固定，始终显示源数据日期。采集会消耗 Tavily 和模型额度，默认每轮最多分析 3 条新闻；其余新闻可单独研判。

原有课程方案和分工文档保留在仓库中，它们描述的是项目规划；已实现范围以本 README 和代码为准。

## 2. 第一次在 Windows 上启动

以下命令在 **PowerShell** 中执行。建议使用 **Python 3.12、Node.js 22、Git**。无需单独安装 MySQL 或启动数据库服务。

### 第一步：拉取代码

```powershell
git clone https://github.com/hus-king/finpulse.git
Set-Location -LiteralPath .\finpulse
```

如果已经在现有项目目录内，跳过这一步。后面的命令都在项目根目录执行，即包含 `package.json` 和 `requirements.txt` 的目录。

### 第二步：安装前后端依赖

```powershell
node --version
python --version
npm ci
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`npm ci` 按仓库中的 `package-lock.json` 安装前端依赖。Python 命令直接使用虚拟环境中的解释器，不需要先执行环境激活脚本。

如果系统 Python 版本不合适，并且已经安装了 `uv`，也可以用以下两条命令替代创建虚拟环境与安装 Python 依赖的步骤：

```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

### 第三步：创建本地配置并填入密钥

首次配置时复制模板；已有 `config.local.json` 时保留原文件：

```powershell
if (-not (Test-Path -LiteralPath .\config.local.json)) {
    Copy-Item -LiteralPath .\config.example.json -Destination .\config.local.json
}
```

用编辑器打开 `config.local.json`，填写自己的配置。结构如下，示例中的值需要替换：

```json
{
  "base_url": "https://your-provider.example/openai/v1",
  "api_key": "replace-with-your-key",
  "model": "replace-with-your-model",
  "tavily_api_key": "",
  "tavily_base_url": "https://api.tavily.com"
}
```

| 字段 | 用途 |
| --- | --- |
| `base_url` | 兼容 Chat Completions 的模型 API 基础地址；后端自动追加 `/chat/completions`，也支持完整端点 |
| `api_key` | 对应模型服务的密钥 |
| `model` | 服务商支持的模型名称 |
| `tavily_api_key` | 主网站真实新闻搜索与正文提取使用；启动账号功能不需要它 |
| `tavily_base_url` | Tavily 地址，默认 `https://api.tavily.com` |

不要在 JSON 中添加注释或末尾多余的逗号。模型配置会在请求时重新读取，修改模型地址、密钥或模型名称无需重启。

没有可用模型配置时，可以浏览已保存的数据和使用账号功能，但 AI 请求无法完成。`config.local.json` 已被 Git 忽略，不随仓库分发。调度和 SMTP 字段的完整模板见 `config.example.json`。

### 第四步：构建前端并启动网站

```powershell
npm run build
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

浏览器打开：

- 网站：**http://localhost:8000/**
- 服务状态：**http://localhost:8000/api/health**
- 接口文档：**http://localhost:8000/docs**

启动终端需要保持运行；按 **Ctrl+C** 停止服务。`start.ps1` 默认监听本机回环地址，只能从这台电脑访问。

其他启动方式：

```powershell
# 8000 端口已被占用时，改用 8001
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Port 8001

# 修改前端后重新构建，再启动
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Rebuild
```

更换端口后，相应地访问 `http://localhost:8001/`。如果已有服务正在运行，先停止旧服务再启动。

### 第五步：注册并验证 AI 请求

1. 点击右上角「用户登录」，选择「没有账号？注册普通用户」。
2. 填写用户名、昵称、密码及确认密码，注册成功后自动登录。普通注册固定创建普通用户；共享 MySQL 中四个管理员使用独立的管理员入口。
3. 打开「AI 请求实验室」，发送连接测试，确认真实模型能响应。
4. 返回看板，点击「采集并分析」，等待检索、清洗和分析完成；选择真实新闻查看研判与原文，或者打开 AI 助手追问。
5. 点击右上角账号菜单可查看当前账号并退出。

用户名为 3–32 位字母、数字、下划线、点或短横线，并以字母或数字开头，不区分大小写；注册密码为 8–128 位。

## 3. 日常开发、更新和测试

### 前后端开发模式

终端一启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

终端二启动 Vite：

```powershell
npm run dev
```

使用 Vite 输出的地址，通常为 `http://127.0.0.1:5173/`。Vite 把 `/api` 请求代理到 8000 端口后端。若 Vite 使用其他端口，需要把实际来源加入 `FINPULSE_ALLOWED_ORIGINS` 并重启后端。

访问 8000 端口时，使用的是 `dist` 中的构建结果；更新前端后需要重新执行 `npm run build` 并重启后端。开发时，5173 端口显示源码的即时更新。

### 从 main 更新

先提交自己的修改或暂存工作，再更新：

```powershell
git switch main
git pull --ff-only origin main
npm ci
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm run build
```

之后重启服务。`--ff-only` 失败时，说明本地和远程提交分叉，需要处理合并；不要用强制推送替代合并。更新源码时，保留本地的 `config.local.json` 和 `data` 目录。

### 自动化验证

```powershell
npm run build
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

后端测试覆盖账号注册登录、会话撤销和过期、来源及 CSRF 校验、20 个账号并发注册、管理员权限隔离、停用用户、修改密码，以及新闻清洗规则。测试使用临时数据库和模拟模型响应，不需要真实 API 密钥，也不会请求收费模型接口。真实共享 MySQL 的显式验证另见管理员操作说明。

## 4. 用户数据与账号鉴权

- SQLite 模式首次启动自动创建 `data/finpulse.db`；MySQL 模式连接 `config.local.json` 指定的共享数据库，表结构由管理员提前建立。`users` 保存用户资料、角色及 Argon2id 密码哈希，`sessions` 保存会话令牌哈希、有效期和活动时间，`auth_rate_limits` 保存短期请求次数，`admin_audit` 保存管理操作。
- 密码不以明文保存。浏览器通过 HttpOnly、SameSite=Strict Cookie 保持登录，前端只在内存中持有 CSRF 校验值。
- 会话最长有效 24 小时；连续 1 小时未访问鉴权接口后失效。退出时立即撤销当前会话，服务重启保留未过期会话。
- 未登录可以浏览看板；问答、研判和连接测试由后端检查登录状态，直接调用这些接口也会被拦截。
- 退出或切换账号时清空当前 AI 对话、任务展示及最近响应；自选股和订阅按账号隔离，重新登录后读取自己的列表。当前没有持久化聊天记录或密码找回。
- `research_records` 保存新闻采集/清洗审计、行情快照、AI 分析、后台任务、自选股、早报和订阅。公开新闻与行情共享，任务和个人配置使用 owner 隔离。PushPlus token 仅后端使用，不通过接口原样返回；数据库管理员具有读取数据库内容的权限。
- SQLite 使用 WAL 和短写事务，模型网络请求不占用数据库写锁。当前按一个后端实例、小规模用户使用的方式运行。

**用户资料不会通过 Git 自动迁移。** SQLite 模式停服后可以备份整个 `data` 目录；服务运行中应使用 SQLite 在线备份接口，不能只复制 `.db` 而遗漏 WAL 中尚未合并的事务。参考 [SQLite WAL 文档](https://sqlite.org/wal.html)。MySQL 模式应备份服务器数据库，例如使用 `mysqldump`；成员连接同一数据库时无需互相传数据库文件。

环境变量在启动后端之前设置，修改后需要重启：

| 环境变量 | 默认值 / 用途 |
| --- | --- |
| `FINPULSE_DB_PATH` | 默认 `data/finpulse.db`；可指定数据库绝对路径 |
| `FINPULSE_DB_ENGINE` | 可覆盖配置的数据库模式，`mysql` 或 `sqlite`；仅测试时可显式切换 SQLite |
| `FINPULSE_ALLOWED_HOSTS` | 默认 `localhost,127.0.0.1,[::1]`；允许访问的主机名，逗号分隔，不带协议和路径 |
| `FINPULSE_ALLOWED_ORIGINS` | 默认额外允许本机 Vite 的两个 5173 来源；请求自身的同源地址也会被允许；填写完整来源，逗号分隔 |
| `FINPULSE_COOKIE_SECURE` | 默认根据请求是否为 HTTPS 判断；`1` 强制仅通过 HTTPS 传送 Cookie，本地 HTTP 不要设置为 `1` |
| `FINPULSE_SCHEDULER_ENABLED` | `1` 允许当前实例运行每日调度，`0` 关闭；覆盖本地配置；Windows 可用 `start.ps1 -Scheduler` 设置 |

## 5. 目录与 API

```text
backend/               FastAPI、账号鉴权、模型调用与数据适配
src/                   React 前端与 ECharts 图表
public/                公共图标
scripts/               独立新闻清洗和预览脚本
tests/                 账号鉴权与新闻规则测试
config.example.json    可提交的配置模板
config.local.json      本机密钥配置，不提交
data/                  SQLite 数据库，不提交
dist/                  前端构建结果，不提交
start.ps1              Windows 启动入口
requirements.txt       Python 依赖
package-lock.json      前端依赖锁文件
```

| 文件 | 主要用途 |
| --- | --- |
| `backend/app.py` | HTTP 接口、模型代理、Prompt 与 JSON 输出校验 |
| `backend/auth.py` | SQLite 账号、密码哈希、会话、鉴权与登录限流 |
| `backend/demo_data.py` | 保留的早期演示样例；当前主看板不再引用 |
| `backend/news_cleaning.py` / `backend/market_data.py` | 新闻清洗规则与 AkShare 适配 |
| `src/App.tsx` / `src/PriceChart.tsx` | 看板、请求实验室、K 线与技术指标 |
| `src/AuthContext.tsx` / `src/AuthDialog.tsx` / `src/AccountMenu.tsx` | 登录状态、注册登录表单与账号菜单 |
| `src/AiDrawer.tsx` | AI 问答与结构化研判 |

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 服务及模型配置状态；配置完成不等于真实请求已验证 |
| `GET /api/stocks` | 股票库；`q` 按名称/代码/拼音搜索并按需更新交易所列表，`limit` 最多 50，`codes` 按逗号分隔批量读取股票元信息；报价为空，价格从看板快照读取 |
| `GET /api/dashboard/{code}` | 已保存的真实行情与新闻快照，读取不会触发采集或收费 |
| `POST /api/auth/register` | `username`、`nickname`、`password`；注册并登录 |
| `POST /api/auth/login` | `username`、`password`；建立登录会话 |
| `POST /api/auth/admin/login` | 网站管理员专用登录入口，后端检查角色 |
| `POST /api/auth/password` | 验证旧密码并修改自己的网站密码，撤销其他会话 |
| `GET /api/admin/users` | 管理员查看网站账号 |
| `POST /api/admin/users/{id}/status` | 管理员启停普通用户，需要 CSRF |
| `GET /api/admin/audit` | 管理员查看启停操作记录 |
| `GET /api/auth/me` | 当前用户、CSRF 校验值与会话有效期 |
| `POST /api/auth/logout` | 撤销当前会话 |
| `POST /api/chat` | 已登录用户的多轮问答；传 `messages`，可选 `stock_code` |
| `POST /api/analyze` | 已登录用户的新闻研判；传 `title`、`content`、`stock_name` |

所有 POST 接口要求本站允许的 `Origin` 和 JSON 请求体。登录后的 POST 请求还需要会话 Cookie 和 `X-CSRF-Token`，其值来自登录或 `/api/auth/me` 响应。前端自动处理这些字段；Swagger 或脚本调用同样要提供它们。401 表示需要登录，403 可能表示来源或 CSRF 校验失败，429 表示登录尝试过于频繁。

## 6. Tavily / AkShare 独立实验

主网站现已通过 `/api/research/{code}/refresh` 自行发起 Tavily 搜索，不依赖历史实验样本。下述脚本仅用于复现早期实验；日常使用直接在网站发起采集。

早期实验会补充已有搜索样本的正文，过滤非主体页面、旧文章和日期冲突，合并重复事件，再关联 AkShare 返回的真实历史日线。最新报价接口失败会单独记录，历史收盘价不会被标为实时价格。

**首次克隆仓库不能直接复现这次实验：** 脚本依赖 `.runtime/tavily-smoke-2026-10-01.json` 搜索样本；原始搜索响应、正文缓存及实验输出只保存在原开发电脑，未提交到公开仓库。脚本目前也不负责重新生成这个搜索样本。

具备该样本后，在根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.news_experiment
```

Tavily 正文提取需要配置 `tavily_api_key`。已有缓存默认复用；`--offline` 只使用本地响应，`--refresh-extract`、`--refresh-market` 分别刷新正文和行情，`--reference-date YYYY-MM-DD` 指定实验参考日期。这个命令不是主网站启动的前置步骤。

输出包括 `.runtime/news-cleaned-akshare-<日期>.json` 与 `.runtime/tavily-preview/cleaned.html`。行情和正文可因网络、源站限制而获取失败，查看记录中的状态及来源。参考 [AkShare 股票文档](https://akshare.akfamily.xyz/data/stock/stock.html)。

## 7. 后续独立服务器部署

当前仓库提供源码和本机启动方式，尚未配置目标服务器。后续可以使用 Linux 服务器运行一个后端实例，由 FastAPI 同时提供前端构建文件和 API。

服务器上的首次安装与本地类似，预先安装 Git、Node.js 22 与 Python 3.12，然后执行：

```bash
git clone https://github.com/hus-king/finpulse.git
cd finpulse
npm ci
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm run build
```

单独创建 `config.local.json` 并填写服务器使用的密钥，已有配置时保留它。可以先在服务器内部验证：

```bash
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1
```

这个命令只监听服务器自身；客户端电脑的 `localhost` 指向客户端电脑，不能用它访问远程服务器。公网访问需要后续配置域名或服务器地址及反向代理。

正式部署需要完成：

1. 配置 Nginx 或 Caddy 反向代理与 HTTPS，由代理访问 `127.0.0.1:8000`，前端和 `/api` 保持同源。参见 [FastAPI HTTPS 文档](https://fastapi.tiangolo.com/deployment/https/)。
2. 设置真实的 `FINPULSE_ALLOWED_HOSTS` 和 `FINPULSE_ALLOWED_ORIGINS`，设置 `FINPULSE_COOKIE_SECURE=1`。代理保留原始 Host，并传递实际协议；Uvicorn 只信任指定代理地址的转发头。
3. 用 systemd 或其他进程管理器保证服务启动及自动恢复，初期使用一个 worker，并为进程设置正确的项目工作目录。
4. 为数据库设置独立的持久化目录、部署用户的读写权限和备份方式；更新源码、重建前端不应删除数据库。
5. 新建服务器账号库，或按停服备份方式迁移已有数据库；配置文件和数据库通过单独的安全渠道提供，不放入 Git。
6. 验证注册、登录、退出、未登录拦截和真实数据采集；MySQL 部署必须先建立 `research_records`。只在一个后端实例开启早报调度，团队成员的本地配置保持关闭。

服务器域名、系统版本、部署路径和进程管理方式确定后，再补充可直接执行的部署配置。

## 8. 常见启动问题

| 现象 | 排查方法 |
| --- | --- |
| Python 环境缺失 | 确认在根目录创建了 `.venv` 并安装 `requirements.txt` |
| PowerShell 阻止运行 `npm.ps1` | 用 `npm.cmd ci`、`npm.cmd run build` 或 `npm.cmd run dev` 执行相同任务 |
| 8000 端口被占用 | 停止自己启动的旧服务，或使用 `start.ps1 -Port 8001` |
| 前端未构建 / 503 | 执行 `npm run build`，构建完成后重新启动后端 |
| 页面还是旧版本 | 重建前端、重启后端，再刷新浏览器 |
| 模型未配置或请求失败 | 检查 JSON 格式、地址、密钥、模型名称及服务商额度；先在请求实验室测试 |
| AI 请求返回 401 | 先登录；会话超时或退出后需要重新登录 |
| 请求返回 403 或 Invalid host header | 检查访问主机名、前端来源、代理协议与环境变量，修改后重启 |
| 换电脑后没有原来的用户 | 数据库不在 Git 中，需另行迁移数据库或重新注册 |
| 新闻实验提示样本文件不存在 | 查看第 6 节；独立实验样本没有随仓库分发 |

## 9. 课程文档

- [软件功能与实现方案](股票智能舆情分析平台_软件功能与实现方案.md)
- [软件功能与实现方案 PDF](股票智能舆情分析平台_软件功能与实现方案.pdf)
- [选题与立项报告](股票智能舆情分析系统_选题与立项报告.md)
- [团队分工与工作量难点](团队分工与工作量难点说明.md)
- [团队分工与工作流规划](项目团队分工与工作流规划.md)
- [软件工程实践](软件工程实践.docx)

`.gitignore` 排除了本地密钥、数据库、依赖、构建结果和运行缓存。仓库用于共享源码与课程资料；配置模板可以提交，真实密钥和用户数据保留在运行环境。

## 10. 2026-10-03：真实新闻分析与页面更新

### 实际操作

1. 更新依赖，构建前端并启动后端。共享 MySQL 的管理员先运行一次下面的增量建表命令；已在本开发机执行，其他成员连接相同数据库无需再次建表：

   ```powershell
   .\.venv\Scripts\python.exe -X utf8 -m scripts.migrate_research
   ```

   该脚本通过 SSH 执行 `sudo mysql`，仅新增研究表，不重建数据库、不重置账号。SQLite 模式在启动时自动建表。

2. 在 `http://localhost:8000/` 登录普通用户或管理员，点击顶部搜索或「我的自选」加号，输入名称、6 位代码或拼音缩写，点击「添加并采集」。新标的自动保存、切换到看板并采集近 30 天新闻、历史日线、清洗去重和最多 3 篇 AI 研判，不需要再手动点击采集。每个账号最多 20 只；只点击名称则切换查看，不触发收费采集。已关注股票可设置近 7/30/90 天，可选「含社区样本」，点击「采集并分析」更新。
3. 查看后台阶段：搜索新闻及行情 → 提取正文及清洗 → AI 结构化研判 → 可选社区分析 → 保存结果。退出页面不会主动撤销后台任务；服务停止会中断任务。
4. 新闻卡片展示来源、发布时间、正文/片段状态、评分和因果链。点击标题打开原文，「查看研判 / 追问」打开 AI 助手；「查看清洗记录」查看过滤、合并原因。右上下载按钮导出当前真实研究 JSON。
5. 「自选股早报」提供排序预览、生成并保存今日晨报、历史日期与主题/渠道设置。预览和手动生成不额外搜索、调用模型或发送消息；自动更新先采集共享数据再分别生成个人晨报。真实发送测试是独立按钮。

### 数据链路与保存位置

```text
Tavily Search + AkShare 东方财富新闻
    → Tavily Extract（失败时保留带标注的搜索片段）
    → 主体、时间、正文、乱码和重复事件过滤
    → 大模型 -2…+2 / 摘要 / 三项因果链 / 局限
    → Pydantic 严格 JSON 校验（失败最多修复一次）
    → MySQL 或 SQLite 的 research_records
    → 主看板、新闻追问、走势观察、自选股早报

AkShare 腾讯日线 → 同库保存 → 日/周 K、MA、MACD、RSI
```

新闻、清洗及分析保存在配置指定的数据库，不再仅存于临时 HTML。`namespace=collection` 为原始检索响应与清洗审计，`dashboard` 为最新快照，`analysis` 为按股票、标题、正文、Prompt 版本及模型缓存的结构化结果；`watchlist`、`job`、`subscription`、`briefing`、`delivery` 使用账号 owner。不会保存请求 Authorization 密钥。

Prompt 位于 `backend/prompts.py`。新闻分为公司事实、机构观点和行情快讯；来源材料被视为数据，不执行正文内的指令。对缺少内容、乱码标题、日期冲突、过期页面与无发布时间的材料进行隔离，不给它们虚构评分。评分只能是整数 -2～2，因果链必须为 3 项。

默认仅分析最近保留的 3 条，接口允许 1～8 条；相同材料复用缓存。后台同时最多 2 个采集任务，队列上限 8；每账号每个限流窗口（15 分钟）允许 12 次采集和 30 次单篇研判。重复提交同账号同股票时复用执行中的任务。

来源失败会显示 partial/failed 与错误说明。行情失败可保留上次日线并标 stale；没有旧数据则为空。两个新闻源都失败时保留上次新闻并提示；成功搜索返回空结果时不假造内容。清洗很保守，可能遗漏有效报道，不能保证完整覆盖。

### 社区与走势的边界

社区模块是**真实公开帖子的搜索样本版**，不是全量股吧爬虫。只统计包含目标股票、链接为独立帖子且有可用日期的样本。模型逐个返回 bull/bear/neutral，后端校验 ID 集合并计算三类比例；没有阅读/回复量则等权。85% 看多、80% 看空仅是样本阈值提示，不意味着价格必然反转。

新闻回溯以**发布日之后首个返回交易日的收盘价**为基准，观察后续 3/5 个交易日收盘变化。不足的交易日显示 `—`；日线未复权，未计费用，没有证明新闻与涨跌之间的因果关系。周 K 从真实日线按周一归组，周成交量求和；图表普通滚轮用于页面滚动，Ctrl + 滚轮用于缩放。

### 调度与推送配置

`config.example.json` 新增 `scheduler` 和 `notifications.smtp`。默认调度关闭，开发验证没有发送真实邮件或微信：

```json
"scheduler": { "enabled": false, "morning_time": "08:30" },
"notifications": {
  "smtp": { "host": "", "port": 465, "username": "", "password": "", "from_email": "" }
}
```

需要自动晨报时，在**指定调度后端**使用 `start.ps1 -Scheduler`，或将本地配置 `scheduler.enabled` 设为 true 后重启。管理员在管理中心设置时间和每天/周一至周五模式；用户可以只生成站内晨报，也可以订阅邮件/PushPlus。默认每天 08:30（上海时间）执行，含休市日，历史股价始终保留源数据日期。停用或关闭晨报的账号不参加每日任务；重启后补做今日已到期的任务，不补发往日任务。详见下面的每日晨报说明。

邮件配置用服务商 SMTP 授权码，465 使用 SSL，其他端口使用 STARTTLS；微信填写本人 PushPlus token。`sent` 表示 SMTP 接受，`accepted` 表示 PushPlus 接口接受，不保证手机终端实际收到；真实渠道尚待配置后的端到端验证。

| 新接口 | 用途 |
| --- | --- |
| `GET/POST /api/watchlist` | 读取/保存当前账号自选股；GET 还返回股票元信息和当前进程的未完成任务，POST 仅保存列表 |
| `POST /api/watchlist/add` | `{ "code": "600036" }`；验证股票、追加个人自选、自动启动默认研究任务，返回 202、codes、stock、job。重复添加不重复付费采集；需要登录和 CSRF |
| `POST /api/research/{code}/refresh` | `days`、`max_articles`、`include_community`；返回 202 和后台任务 ID |
| `GET /api/research/jobs/{id}` | 当前账号任务阶段、状态、计数与限制 |
| `GET /api/research/{code}/audit` | 当前快照的清洗审计 |
| `POST /api/news/{code}/{id}/analyze` | 按保存的真实材料分析、复用缓存并更新快照 |
| `GET /api/briefing/preview` | 当前账号早报预览 |
| `GET/POST /api/subscription` | 当前账号订阅与最近推送状态，token 不回显 |

### 自选股与股票库更新（2026-10-03）

- 股票范围：沪深主板、创业板、科创板，使用 `stock_info_sh_name_code` / `stock_info_sz_name_code` 交易所列表。当天实际取得 **5,224 只**；这是采集时数量，之后随列表更新变化。北交所、港股、美股暂不支持。
- 股票名称、代码与拼音缩写来自真实列表，不使用大模型生成股票代码。`pypinyin` 提供名称缩写；上交所列表未提供行业时显示「A 股」，不猜测行业。
- 列表保存在共享 `research_records` 的 `stock_catalog` 命名空间，按上海主板、科创板、深圳分别缓存。首次搜索按需拉取，各接口最多等待 45 秒，超过一天按需更新；失败保留上次成功列表，五分钟内不重复请求失败源，窗口显示限制。首次无缓存时只保留原 5 只基础元信息，不生成行情或新闻。
- 添加调用 `/api/watchlist/add`，保存个人自选后自动启动原有真实研究流水线：默认近 30 天、最多分析 3 篇，不默认抓社区。当前队列最多 40 个用户任务，最多同时采集 2 只股票，同一股票串行；相同参数的并发采集共享执行。页面按股票跟踪任务，可继续添加、切换浏览；重新加载会读回当前账号未完成的任务。
- 重复添加已关注股票只返回已有自选/进行中任务，不再次收费采集；需要更新时点击「采集并分析」。新闻清洗、AI 提示词、走势观察和早报均使用动态股票信息，无需再为每只股票修改代码。
- 新闻与行情快照在共享数据库共用；自选和任务权限按账号隔离。服务重启不会自动恢复中断采集，需要重新发起；列表更新失败和正文提取失败不使用假数据补齐。
- 本机从页面添加 **招商银行（600036）** 实测：自动保存 150 条日线（最新返回日期 2026-09-30）、3 篇结构化 AI 研判；随后补充报价/历史价格/财务表/企业资料页过滤并重新采集，最终保存 **6 条新闻**（12 条搜索结果中过滤 6 条）。自选保存和早报读取通过；重复添加未启动新任务。AkShare 东方财富新闻接口本次 SSL 错误，实际新闻由 Tavily 获取；部分正文未提取成功，相关条目标记为搜索片段。详细实测报告在本机 `.runtime/add-stock-verification.json`（不提交）。
- 本次前端构建通过；后端 59 项测试通过，覆盖搜索缓存、动态新闻主体、自动采集、鉴权、重复添加、数量限制、保存失败停止任务，以及报价资料页过滤而公告保留。

### 自动化与真实接口验证

```powershell
npm.cmd run build
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

原有账号、角色与清洗测试保留，新增股票库搜索/缓存恢复/源失败、动态股票主体清洗、添加自动采集、重复添加、登录与 CSRF 前置校验、20 只上限、保存失败不启动采集等测试；自动化测试不会消耗真实 API 额度或发送消息。

已有本机管理员凭据文件的项目负责人可以显式执行真实验证（消耗 Tavily 与模型用量）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.verify_research --codes 600519 300750 688981 --community --max-articles 2
```

验证报告写入 `.runtime/live-research-verification.json`，来源及研判结果同时保存到共享数据库。该命令依赖未提交的负责人凭据；普通成员直接通过自己的网页账号验证即可。

本轮实际验证（2026-10-03）：

| 股票 | 真实检索结果 | 清洗保留 | 已生成研判 | 历史日线 |
| --- | --- | --- | --- | --- |
| 贵州茅台 600519 | 12 | 3 | 2 | 150 |
| 宁德时代 300750 | 12 | 4（另合并 1 条） | 2 | 150 |
| 中芯国际 688981 | 12 | 5 | 2 | 150 |

以上最终快照已写入共享 MySQL，日线源最后返回日期为 2026-09-30。新闻本轮主要由 Tavily 提供；AkShare 东方财富新闻接口在本机网络下返回 SSL 错误，因此任务标为 partial。AkShare 腾讯日线成功，未关闭 TLS 验证。宁德时代有一条正文提取失败，保留并标明搜索片段。先前社区搜索未取得日期可核验的独立帖子，页面未生成模拟比例。邮件/微信适配仅使用模拟响应验证，没有发送真实消息。

前端构建与 49 项自动化测试通过。浏览器验证了实际 K 线和保存的研判，以及鼠标位于左侧时整页滚动。SSH 曾在多股票验证中断开；已完成的各股票结果单独保存，重试剩余股票后成功完成。

若需要在共享数据库不可达时隔离验证 API，可以显式使用下面的 SQLite 测试模式；只影响该测试进程，不修改网站配置，也不复制测试账号到共享数据库：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.verify_research --sqlite --codes 600519 --max-articles 2
```

网络断开时共享 MySQL 会返回 503，不会偷偷切换到 SQLite。后台 SSH 隧道增加自动重连；查看 `.runtime/mysql-tunnel.stderr.log`，也可以在独立终端运行 `powershell -File scripts/mysql-tunnel.ps1 -Reconnect`。若 SSH 无法完成认证或连接超时，先恢复 SSH 网络，再重试页面采集。

2026-10-03 连接复查：本机把 `airhust.cn` 解析为虚拟代理地址，域名路径和真实 IP 直连均出现过连接重置。本机现在让**这个项目的 SSH 隧道**通过已有的本地 SOCKS 代理转发，没有修改全局代理配置。可在 `database.ssh_proxy` 填写本地代理地址（如 `127.0.0.1:7897`），或使用 `scripts/mysql-tunnel.ps1 -Reconnect -SocksProxy 127.0.0.1:7897`。该字段只允许回环地址，需要 Git for Windows 自带的 `connect.exe`；脚本自动寻找它。其他开发机不需要代理时保留空值。

也可在 `database.ssh_address` 填写管理员确认的服务器真实 IP，或使用 `-DirectAddress <真实IP>` 绕过虚拟 DNS；留空使用原域名。真实 IP 变更后需同步修改。域名、直连和 SOCKS 路由均保留 SSH 主机密钥核验和原账号权限；切换路由前需停止旧的项目隧道及其重连进程，再重新启动。

数据库仅对网络异常造成的**初始连接失败**最多尝试 3 次，间隔 2/4 秒；账号密码错误不重试，已经执行的 SQL 和事务不自动重复提交。新增测试覆盖建连恢复、错误密码和中途断连不重复写入，全套 52 项测试通过。较长的外网中断仍可能返回 503，不能据此保证服务器网络永不掉线。

路由修复后，针对实际运行的 `localhost:8000` 完成一轮完整检查：管理员登录、共享数据库读写、真实模型请求、Tavily 采集、行情保存、清洗审计、早报预览、订阅读取、管理接口、退出登录和会话失效均通过。中芯国际本轮保留 4 条新闻，读取/保存 1 条研判和 150 条日线；模型连接测试额外调用真实服务并返回成功。AkShare 东方财富新闻仍报 SSL 错误，流程通过 Tavily 获取新闻并标为 partial。没有发送邮件或微信。本机检查记录保存在被 Git 忽略的 `.runtime/connection-check.json`。

## 11. 每日晨报、多人使用与自动推送（2026-10-04）

### 在页面检查

1. 登录后打开「自选股早报」。可以查看已保存的最近一期、选择历史日期、查看不保存的当前预览，或点击「生成今日晨报」保存当前数据的汇总。这两种手动操作不额外搜索或发送消息。
2. 设置新闻窗口（1–30 天）、推荐条数（3–20 条）和关注主题。支持业绩财报、公司公告、行业政策和风险事件。设置和历史按账号保存，不与其他账号共享。
3. 不填写邮箱/token 也能启用站内晨报。邮件/微信有独立开关；SMTP 配置在服务端，PushPlus token 在用户页面填写，接口只返回是否配置，不返回 token。
4. 管理员打开「管理中心 → 每日更新与晨报调度」，设置上海时间的执行时刻、每天或周一至周五、默认检索天数和每只股票的研判数量；可以手动提交一次真实每日更新。页面自动刷新股票更新状态、个人晨报数量和数据源限制。

### 推荐排序

`backend/recommendations.py` 实现可解释规则排序：时效指数衰减、自选股主体关联、事件主题/情绪影响强度、正文和日期核验质量、个人主题偏好。原始项上限合计 105 分，归一化为 0–100 优先分；排序还对已出现的公司/主题施加多样性惩罚，因此最终位次不一定严格按原始优先分递减。正负情绪使用绝对影响强度，利空不会被排除；没有采用训练模型或收益预测。

缺失、冲突、未来日期和窗口之外的新闻不参与。已缓存的报价、财务、公司资料及新闻列表页也会再次过滤；URL 与同公司、相近日期、标题相似的事件按清洗规则合并，编号和金额不同的事件保守区分。跨股票相同来源链接合并并保留关联股票；这些规则不能保证识别全部语义重复。每条推荐展示理由、原始分项、来源与研判局限，缺失/过期快照和数据源失败会提示。

### 指定一个后端运行每天的任务

```powershell
# Windows：默认每天 08:30，指定这个后端实例运行调度
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Scheduler
```

Linux 部署时设置环境变量 `FINPULSE_SCHEDULER_ENABLED=1`，并由进程管理器持续运行一个 Uvicorn worker。其他开发实例不启用调度。也可以用 `config.local.json` 的 `scheduler.enabled=true` 启用；环境变量优先。管理员页面的全局启停/时间设置保存在共享数据库；在指定实例上修改即时生效，通过其他开发实例修改时由指定实例每 15 分钟同步。

每日任务先读取所有启用晨报的活跃账号，合并自选股；共同关注的股票只更新一次，每只最多分析配置数量的新闻，默认不采集社区。检索窗口取全局设置与关注该股票用户设置的较大值。结果按日期/代码保存，再为每个用户生成独立的当日晨报，写入日期历史及 latest；关闭晨报的用户仍可手动生成。正常重跑保留当天结果，手动重跑复用当天已完成的股票并重新生成个人晨报。

全局任务和渠道发送使用 `research_records` 中的事务租约，SQLite/MySQL 都支持，无需新增表或管理员权限。每日任务有续租；实例重启后检查今日到期任务，复用当天已完成的股票，并继续未完成工作。历史日期不会补发；当天已完成的自动任务不会因重启重新采集。运行期间服务必须在线；当前仍按单个后端 worker 运行，用户手动研究任务重启后仍需重新发起。

### 推送与失败处理

`config.local.json` 中填写以下发信字段，勿提交真实密钥：

```json
"notifications": {
  "smtp": {
    "host": "smtp.your-provider.example",
    "port": 465,
    "username": "your-sender-account",
    "password": "your-smtp-authorization-code",
    "from_email": "your-sender@example.com",
    "from_name": "funplus"
  }
}
```

465 使用 SMTP SSL，其他端口使用 STARTTLS；`password` 通常是邮箱提供的 SMTP 授权码。填写后在页面保存接收邮箱并启用邮件，点击「发送真实测试推送」。微信填写自己的 PushPlus token 并启用渠道。此按钮会真的发送；保存设置、生成/预览晨报不会发送。

`from_name` 是邮件中显示的发件人名称，可以填写 `funplus`，中文名称也支持；留空显示邮箱地址。`from_email` 继续填写真实发件邮箱，SMTP 认证和实际发件地址不随显示名称变化。修改配置后，后续发送使用新的名称，已经发出的邮件不会改变。

每日发送按用户/日期/渠道记录：已发送或被受理的渠道不重复发送。明确失败的渠道至少间隔 15 分钟重试，最多实际尝试 3 次；未配置 SMTP 不计实际尝试。网络超时等结果不确定、或进程在发送时中断的请求不会自动重发，以避免重复消息，页面提示用户核实。邮件“已发送”表示 SMTP 服务接受发送；PushPlus“渠道已受理”表示 API 返回接受，不保证已到达收件箱/微信。外部渠道无法做到数据库与第三方发送之间的严格原子性。参考 [PushPlus 消息接口](https://www.pushplus.plus/doc/guide/api.html)。

### 多账号与验证边界

同一参数的并发股票采集在一个后台进程共享执行，各账号保留自己的任务 ID、进度与权限；队列上限 40，采集并行度 2，大模型同时请求上限 4，等待模型超过 30 秒会提示繁忙。远程数据库的研究/每日任务读写移到线程中，避免在模型等待期间阻塞整个页面。MySQL 同时连接上限 8，超出的数据库工作在后端排队，避免 SSH 隧道连接争用。限流按确定行、固定顺序锁定；过期会话清理移出新会话创建事务，避免并发注册的锁冲突。租约和限流的初始化使用重复键更新直接取得独占锁，避免 INSERT IGNORE 后升级锁。相关机制见 [MySQL InnoDB 锁说明](https://dev.mysql.com/doc/refman/8.4/en/innodb-locks-set.html)。

本轮前端构建及后端 75 项测试通过。自动化测试使用临时 SQLite、模拟搜索/模型和推送，覆盖推荐排序、去重和非新闻过滤、历史/主题/任务隔离、20 账号并发使用同一采集、每日共享更新、跨实例租约、重复手动提交合并、成功渠道去重、各渠道失败重试间隔/上限、结果不确定不重发、停用账号不发送、重启补做条件及跨实例调度设置同步。运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
npm run build
```

2026-10-04 的真实验证：每日更新采集了 3 只股票并生成 4 份个人晨报；20 个临时账号通过真实 HTTP 同时登录、保存不同自选股、生成/读取个人晨报、验证管理员权限限制并退出，全部成功，测试账号及私人记录已清理。当前开发环境通过 SSH 连接远程 MySQL，最慢一组完整操作耗时约 79 秒；该结果验证了并发正确性，不代表部署后的响应速度达标。部署后需要在后端与数据库同机或同内网的条件下再测速度。真实报告保存于本机 `.runtime/morning-verification.json`，不提交 Git。

管理员保存合法调度时间、拒绝非法时间、手动每日更新返回 202、复用当日 3 只股票而无额外采集、重新生成 4 份私人晨报也已通过真实 HTTP 验证，报告在 `.runtime/morning-final-verification.json`。浏览器检查后补充过滤 moomoo 报价页、东方财富公告列表，刷新了个人晨报；保留具体公告详情页。

2026-10-04 验证时，推送适配和状态机已验证，发信账号和 PushPlus 接收信息尚未配置。2026-10-05 本机已配置 QQ 邮箱，向用户指定的收件人发送了一封测试邮件，QQ SMTP 返回接受；最终送达由收件端确认。PushPlus 真实发送仍未验收。AkShare 新闻 SSL 问题仍可能使每日更新为 partial，已由 Tavily 补充并在页面提示。网站本次没有部署到远程服务器。

页面参考 [Yahoo Finance](https://finance.yahoo.com/) 的横向导航、行情卡片、主新闻栏与侧栏信息层级，保留 FinPulse 自己的内容和交互。API 实现参考 [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[AkShare 股票文档](https://akshare.akfamily.xyz/data/stock/stock.html)、[APScheduler](https://apscheduler.readthedocs.io/en/3.x/userguide.html)、[PushPlus 消息接口](https://www.pushplus.plus/doc/guide/api.html)。
