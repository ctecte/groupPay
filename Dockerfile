# Stage 1: Build the node application
FROM node:lts-alpine AS build
WORKDIR /app/splitwize-spark
COPY splitwize-spark/package.json splitwize-spark/package-lock.json ./
RUN npm install
COPY splitwize-spark ./
RUN npx vite build

# Stage 2: Actual bot running logic
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gcc g++ libc6-dev \
 && pip install -r "/app/requirements.txt" \
 && mkdir -p --mode=0755 /usr/share/keyrings \
 && curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null \
 && echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | tee /etc/apt/sources.list.d/cloudflared.list \
 && apt-get update && apt-get install -y --no-install-recommends cloudflared \
 && apt-get purge -y gcc g++ libc6-dev --auto-remove \
 && rm -rf /var/lib/apt/lists/* 
COPY --from=build /app/splitwize-spark/dist/ ./splitwize-spark/dist/
COPY *.py entrypoint.sh ./
RUN chmod +x entrypoint.sh
ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]