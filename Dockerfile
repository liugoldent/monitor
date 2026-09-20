FROM node:22-bookworm

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Taipei \
    PATH="/opt/venv/bin:${PATH}" \
    RUN_SERVICES_DOCKER=1

RUN sed -i \
        -e 's|http://deb.debian.org/debian-security|https://mirror.twds.com.tw/debian-security|g' \
        -e 's|http://deb.debian.org/debian|https://mirror.twds.com.tw/debian|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=30 update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        python3 \
        python3-pip \
        python3-venv \
        tini \
        tzdata \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --index-url "$PIP_INDEX_URL" --retries 5 --timeout 60 --upgrade pip setuptools wheel \
    && rm -rf /var/lib/apt/lists/*

RUN corepack enable \
    && corepack prepare pnpm@10.24.0 --activate

WORKDIR /app

COPY backend-futures-py/requirements.txt backend-futures-py/requirements.txt
RUN pip install --index-url "$PIP_INDEX_URL" --no-cache-dir --retries 5 --timeout 60 -r backend-futures-py/requirements.txt

COPY backend-heyu-node/package.json backend-heyu-node/pnpm-lock.yaml backend-heyu-node/
COPY frontend-vue/package.json frontend-vue/pnpm-lock.yaml frontend-vue/
RUN cd backend-heyu-node && pnpm install --no-frozen-lockfile \
    && cd /app/frontend-vue && pnpm install --no-frozen-lockfile

COPY . .

RUN sed -i 's/\r$//' /app/run-services.sh /app/run-trade-services.sh /app/scripts/run-services-docker.sh \
    && chmod +x /app/run-services.sh /app/run-trade-services.sh /app/scripts/run-services-docker.sh

EXPOSE 8080 5050 5173

ENTRYPOINT ["tini", "--"]
CMD ["bash", "/app/run-services.sh"]
