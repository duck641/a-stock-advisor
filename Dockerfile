# ── 第 1 层：基础系统 ──────────────────────
FROM python:3.12-slim

# 设置工作目录（后面所有操作都在 /app 下）
WORKDIR /app

# 安装系统级依赖（uv 需要，后面 COPY 需要）
RUN pip install --no-cache-dir uv

# ── 第 2 层：Python 依赖 ──────────────────
# 先只拷依赖清单，这样改代码不会触发重装
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# 把虚拟环境加到 PATH（后面 python/python3 自动用这个）
ENV PATH="/app/.venv/bin:$PATH"

# ── 第 3 层：源代码 ────────────────────────
COPY . .

# 运行时创建数据目录（不进镜像，挂载卷覆盖）
RUN mkdir -p /app/data /app/logs /app/cron/reports
