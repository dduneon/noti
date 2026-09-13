FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[web]"

# 설정은 /config, 상태(SQLite)는 /data. 둘 다 볼륨으로 붙인다.
ENV NOTI_DB_PATH=/data/noti.db NOTI_CONFIG_PATH=/config/config.yaml
EXPOSE 8765
VOLUME ["/data", "/config"]

CMD ["noti", "run"]
