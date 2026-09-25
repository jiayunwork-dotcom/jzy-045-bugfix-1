# 酶促动力学计算服务 —— 运行时锁定 Python 3.12
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENZYME_STORE_PATH=/data/enzymes.json

# 非 root 运行；/data 为酶参数档落地目录（挂载卷即可持久化）。
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py ./

USER appuser
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import json,urllib.request,sys; \
r=urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3); \
sys.exit(0 if r.status==200 and json.loads(r.read())['status']=='ok' else 1)"

# waitress 单进程多线程：计算无共享状态，酶仓库内部带锁串行化落盘。
CMD ["waitress-serve", "--call", "--host=0.0.0.0", "--port=8080", "wsgi:app"]
