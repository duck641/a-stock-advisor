# 📊 A_stock Advisor

> 基于 LangGraph 的 A 股智能分析助手，支持终端对话、Web 界面、定时日报三种交互方式。

## 功能特性

- **ReAct 智能体**：LLM 自主调用工具获取数据 → 分析 → 给出投资建议
- **多数据源**：新浪、腾讯、同花顺、上交所官网，覆盖实时行情、历史日线、分时数据、财务指标、龙虎榜
- **并发调度**：基于依赖图的拓扑排序，独立工具并行执行
- **对话持久化**：SQLite 存储，支持多会话切换、token 感知自动压缩
- **用户自定义**：交易纪律、选股策略通过 Markdown 文件管理，无需改代码
- **渐进式技能**：LLM 按需加载技能模块（短线/长线/分析团队），避免 prompt 膨胀
- **三种运行模式**：终端交互 / Web UI / 定时日报

## 快速开始

### 1. 环境要求

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器

### 2. 安装

```bash
git clone https://github.com/yourname/a-stock-advisor.git
cd a-stock-advisor
uv sync
```

### 3. 配置

```bash
# 交互式配置向导（推荐）
.venv/bin/python3 cli.py setup

# 或手动复制模板编辑
cp .env.example .env
```

最少只需填 `LLM_PROVIDER` + `LLM_API_KEY`，其余参数自动补齐。

支持厂商：DeepSeek / OpenAI / 通义千问 / 智谱 GLM / Moonshot / Ollama 本地部署。

### 4. 运行

```bash
# 终端对话
.venv/bin/python3 cli.py chat

# Web 界面（浏览器打开 http://localhost:8000）
.venv/bin/python3 cli.py web

# 定时日报（立即生成一次）
.venv/bin/python3 cli.py cron --now
```

## 使用方式

### 终端对话

```
📊 A 股分析助手
  命令: /compress 压缩 /title <名称> 重命名 /new 新建 /list 切换 /delete 删除

>> 帮我分析一下赛力斯的短线机会
```

支持多会话管理、自动历史压缩、token 用量统计。

### Web 界面

浏览器打开 `http://localhost:8000`，侧栏管理多对话，支持重命名/删除，深色主题。

### 定时日报

每天早上 09:00 自动分析板块并生成 Markdown 报告到 `cron/reports/YYYY-MM-DD.md`。

## 项目结构

```
A_stock/
├── agent.py              # LangGraph ReAct 智能体（LLM + 工具 + 图）
├── chat.py               # 终端对话循环
├── cli.py                # CLI 入口（chat / web / cron / setup）
├── config.py             # LLM 配置（厂商预设 + 自动补齐）
├── prompts.py            # 模块化提示词（角色/规则/技能/工作流）
├── setup_wizard.py       # 交互式配置向导
├── logging_config.py     # 日志系统
├── debug_env.py          # 环境诊断工具
│
├── memory/               # 对话持久化
│   ├── storage.py        # SQLite CRUD
│   └── history.py        # token 估算 + 按轮压缩
│
├── tools/                # 股票分析工具包
│   ├── stock/            # 个股数据（分时/实时/日线/基本面/技术面）
│   ├── market/           # 市场环境（情绪/大盘）
│   ├── signal/           # 综合买卖信号
│   ├── info/             # 股票代码⇄名称补全
│   ├── stock_board/      # 行业板块（排名/成分股/历史走势）
│   ├── invest_kalendar/  # 投资日历
│   └── core/             # 并发调度/错误处理/技能管理/token追踪
│
├── skills/               # 渐进式技能模块
│   ├── short-term-trading/SKILL.md
│   ├── long-term-investing/SKILL.md
│   └── stock-analysis-team/SKILL.md
│
├── wechat/               # Web 服务
│   └── server.py         # FastAPI + WebSocket + 内嵌前端
│
├── cron/                 # 定时任务
│   └── cron.py           # 日报生成器
│
├── trading_rules.md      # 【用户可编辑】交易纪律
├── stock_selection.md    # 【用户可编辑】选股策略（短线+长线）
├── .env.example          # 配置模板
├── Dockerfile            # Docker 镜像
└── docker-compose.yml    # 一键部署
```

## 用户自定义文件

两个 Markdown 文件直接管理你的投资规则，无需改代码：

| 文件 | 内容 |
|------|------|
| `trading_rules.md` | 仓位管理、止损/止盈纪律、禁止行为、心态纪律 |
| `stock_selection.md` | 短线选股（技术面+资金面）\ 长线选股（基本面+估值） |

Agent 启动时自动加载这些文件拼入 system prompt。

## 技能系统

Agent 不会一次性加载所有技能，而是先看到技能索引，按需调用 `load_skill(name)` 加载详细内容：

- **短线交易**：入场/出场规则、仓位管理、三种策略（打板/低吸/突破）
- **长期投资**：价值白马/成长股/困境反转三种选股体系
- **分析团队**：多角色协作分析框架（待完善）

## Docker 部署

```bash
# 确保 .env 已配置
cp .env.example .env
# 编辑 .env 填入 API Key

# 启动
docker compose up -d

# 查看日志
docker compose logs -f
```

## 技术栈

- **Agent 框架**：LangGraph（StateGraph + ReAct 循环）
- **LLM**：ChatOpenAI 兼容接口（支持 6 家厂商）
- **数据源**：akshare（新浪/腾讯/同花顺/上交所）
- **持久化**：SQLite + WAL 模式
- **Web**：FastAPI + WebSocket + 原生 JS（无框架）
- **包管理**：uv

## License

MIT
