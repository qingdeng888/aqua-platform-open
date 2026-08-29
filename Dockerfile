# ============================================================================
# AQUA Gateway 容器镜像（多阶段构建）
# ----------------------------------------------------------------------------
# builder 阶段只负责装依赖到独立 venv，runtime 阶段只带运行期文件，
# 不含编译器与 pip 缓存；最终以非 root 用户运行。
#
# 构建：docker build -t aqua-gateway:12.1.0 .
# 运行：见 docker-compose.yml（推荐）或 README「Docker 部署」
# ============================================================================

FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 只复制依赖清单：依赖未变时这一层命中缓存，改代码不会触发重新装包
COPY gateway/requirements.txt ./requirements.txt

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv

# 应用代码放 /app/gateway：main.py 会尝试加载 <repo根>/.env，
# 对应容器内 /app/.env——需要时可 bind mount 到该路径，
# 但容器环境变量优先级更高（load_dotenv 不覆盖已有变量）。
WORKDIR /app/gateway
COPY gateway/ /app/gateway/

# 非 root 运行：代码目录只读即可，无运行期写盘需求
RUN useradd --create-home --uid 10001 aqua \
    && chown -R aqua:aqua /app
USER aqua

EXPOSE 8000

# 健康检查直接复用公开的 /healthz（无需鉴权），用镜像自带 python 发请求，不装 curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4).status==200 else 1)"]

# 硬约束：17 算法调度器与代理池轮询游标均为进程内状态，禁止 --workers（必须单 worker）
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
