FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin agent
USER agent
ENV PORT=8000
EXPOSE 8000
# exec: uvicorn must be PID 1 so it receives SIGTERM on deploy and can record interrupted runs.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --timeout-graceful-shutdown 20"]
