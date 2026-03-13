FROM python:3.12-slim

WORKDIR /app

# Install shared dagster-io package from sibling directory
COPY libs/dagster-io/ /tmp/dagster-io/
RUN pip install --no-cache-dir /tmp/dagster-io/ && rm -rf /tmp/dagster-io/

COPY packages/congress-data/pyproject.toml .
COPY packages/congress-data/src/ src/
RUN pip install --no-cache-dir .

ENV DAGSTER_HOME=/opt/dagster/dagster_home
RUN mkdir -p $DAGSTER_HOME

EXPOSE 4000

CMD ["dagster", "code-server", "start", \
     "--host", "0.0.0.0", \
     "--port", "4000", \
     "--module-name", "congress_data"]
