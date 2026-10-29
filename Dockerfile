FROM node:22-alpine AS checagem
WORKDIR /chk
COPY app/static/js/*.js ./
RUN for f in *.js; do node --check "$f" || exit 1; done && touch /chk/ok

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=checagem /chk/ok /tmp/js-ok
COPY app ./app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
# 1 worker: o cache de links e a fila de cliques ficam em memória
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
