FROM docker:27-cli

RUN apk add --no-cache python3 py3-pip

WORKDIR /app

COPY services/mcp/servers/compose_restart/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY services/mcp/servers/compose_restart/server.py /app/server.py

ENV PYTHONUNBUFFERED=1
CMD ["python3", "/app/server.py"]
