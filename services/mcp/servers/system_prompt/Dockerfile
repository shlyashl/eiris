FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/mcp/servers/system_prompt/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/mcp/servers/system_prompt/server.py /app/server.py
COPY services/__init__.py /app/services/__init__.py
COPY services/eiris /app/services/eiris

CMD ["python", "/app/server.py"]
