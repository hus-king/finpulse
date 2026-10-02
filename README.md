# FinPulse 智能股票舆情平台

FinPulse 是软件工程课程项目，目前提供可在本机运行的网站 Demo：React + TypeScript + ECharts 前端、FastAPI 后端、MySQL / SQLite 账号与登录会话，以及兼容 Chat Completions 的大模型调用。

**团队共享 MySQL 与四个管理员的操作说明见 [数据库与管理员登录说明](数据库与管理员登录说明.md)。** 当前开发机已连接服务器 `finpulse_dev`；配置模板默认 SQLite，方便独立试运行。新成员共享服务器数据时，需要填写 MySQL 配置并具有 SSH 访问权限。

正式运行时，前端和后端共用 **http://localhost:8000/**。浏览器只访问本地后端，模型 API 密钥由后端读取。

## 1. 当前能做什么

| 功能 | 当前状态 |
| --- | --- |
| 暗色看板、股票检索、自选股添加与切换 | 已实现；股票库为 5 只演示股票，自选股保存在浏览器 localStorage |
| K 线、MA5/10/20、成交量、MACD、缩放与新闻标记 | 已实现展示与计算；输入行情为程序生成的模拟数据 |
| 新闻卡片、情绪温度、多空比例与热词 | 固定示例内容，尚未接入主看板的真实数据管道 |
| 注册、登录、退出、刷新后保持登录 | 已实现；支持共享 MySQL 与独立 SQLite |
| 管理员登录与管理中心 | 已实现；角色校验、普通用户启停、管理记录、修改自己的网站密码 |
| AI 问答、新闻研判、模型连接测试 | 登录后调用真实模型；研判输入仍为示例新闻 |
| Tavily 新闻清洗 + AkShare 历史日线 | 独立实验脚本；尚未接入主网站，运行需要已有搜索样本 |
| 实时行情、股吧情绪采集、定时早报、邮件/微信推送、回测 | 尚未实现主网站集成 |

**主看板中的股价、涨跌幅、K 线和新闻不是实时市场数据。** 行情日期固定为 2026-09-30；MA 与 MACD 根据模拟 K 线计算。AI 请求才会使用配置中的真实模型服务并产生相应 API 用量。

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
| `tavily_api_key` | 可选；独立新闻实验使用，启动主网站不需要它 |
| `tavily_base_url` | Tavily 地址，默认 `https://api.tavily.com` |

不要在 JSON 中添加注释或末尾多余的逗号。模型配置会在请求时重新读取，修改模型地址、密钥或模型名称无需重启。

没有可用模型配置时，可以浏览示例看板和使用账号功能，但 AI 请求无法完成。`config.local.json` 已被 Git 忽略，不随仓库分发。

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
4. 返回看板，选择示例新闻，点击「AI 研判」，或者打开 AI 助手提问。
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
- 退出或切换账号时清空当前 AI 对话及最近响应。已实现管理员页面与修改自己密码；当前没有服务端聊天记录、账号间同步自选股或密码找回。
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
| `backend/demo_data.py` | 主看板的模拟行情、股票与新闻 |
| `backend/news_cleaning.py` / `backend/market_data.py` | 新闻清洗规则与 AkShare 适配 |
| `src/App.tsx` / `src/PriceChart.tsx` | 看板、请求实验室、K 线与技术指标 |
| `src/AuthContext.tsx` / `src/AuthDialog.tsx` / `src/AccountMenu.tsx` | 登录状态、注册登录表单与账号菜单 |
| `src/AiDrawer.tsx` | AI 问答与结构化研判 |

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 服务及模型配置状态；配置完成不等于真实请求已验证 |
| `GET /api/stocks` | 演示股票库 |
| `GET /api/dashboard/{code}` | 演示看板数据 |
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

这个实验与主网站分开。已有样本会补充 Tavily 提取的正文，过滤非主体页面、旧文章和日期冲突，合并重复事件，再关联 AkShare 返回的真实历史日线。最新报价接口失败会单独记录，历史收盘价不会被标为实时价格。

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
6. 验证注册、登录、退出、未登录拦截和真实模型连接，再接入真实行情、新闻与其他业务功能。

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
