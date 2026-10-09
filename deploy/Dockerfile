FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AIFUNDOS_ROOT=/app \
    AIFUNDOS_PYTHON=/usr/local/bin/python

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
RUN chmod +x /app/scripts/run_service_loop.sh

CMD ["/app/scripts/run_service_loop.sh"]
