FROM node:22-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Taipei \
    PATH="/opt/venv/bin:${PATH}" \
    RUN_SERVICES_DOCKER=1

RUN apt-get update \
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
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && corepack enable \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
        amd64) cloudflared_arch="amd64" ;; \
        arm64) cloudflared_arch="arm64" ;; \
        *) echo "Unsupported architecture for cloudflared: $arch" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${cloudflared_arch}.deb" -o /tmp/cloudflared.deb \
    && apt-get install -y /tmp/cloudflared.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/cloudflared.deb

WORKDIR /app

COPY backend-futures-py/requirements.txt backend-futures-py/requirements.txt
RUN pip install --no-cache-dir -r backend-futures-py/requirements.txt

COPY backend-heyu-node/package.json backend-heyu-node/pnpm-lock.yaml backend-heyu-node/
COPY frontend-vue/package.json frontend-vue/pnpm-lock.yaml frontend-vue/
RUN cd backend-heyu-node && pnpm install --frozen-lockfile \
    && cd /app/frontend-vue && pnpm install --frozen-lockfile

COPY . .

RUN chmod +x /app/run-services.sh /app/run-trade-services.sh /app/scripts/run-services-docker.sh

EXPOSE 8080 5050 5173

ENTRYPOINT ["tini", "--"]
CMD ["bash", "/app/run-services.sh"]
