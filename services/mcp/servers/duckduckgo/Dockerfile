FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/mcp/servers/duckduckgo/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/mcp/servers/duckduckgo/server.py /app/server.py

EXPOSE 9002

CMD ["python", "/app/server.py"]
