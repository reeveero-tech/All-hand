# R-089 (D-042): Node 22 (Node 20 EOL 2026-04-30); clean install from the lockfile,
# engines enforced — no silent fallback to `npm install`.
FROM node:22-slim AS frontend-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --engine-strict --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=frontend-build /app/web/dist /app/web/dist
# R-100 (D-044): run as an unprivileged user. Sandbox rlimits (hard == soft)
# cannot be raised again by a non-root child, and test_runner code cannot
# write to the application directory.
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin agos
USER agos
ENV PORT=8000
# SEC-03 (R-139): the image is production — it refuses to start without
# AGOS_TENANT_KEYS / AGOS_API_KEY. Local development: -e AGOS_ENV=development.
ENV AGOS_ENV=production
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
