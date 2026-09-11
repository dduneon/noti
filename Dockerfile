FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[web]"

ENV NOTI_DB_PATH=/data/noti.db NOTI_CONFIG_PATH=/app/config.yaml
EXPOSE 8765
VOLUME ["/data"]

CMD ["noti", "run"]
