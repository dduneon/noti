FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV NOTI_DB_PATH=/data/noti.db NOTI_CONFIG_PATH=/app/config.yaml
VOLUME ["/data"]

CMD ["noti", "run"]
