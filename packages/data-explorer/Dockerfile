FROM python:3.12-slim

WORKDIR /app

# Install shared dagster-io package from sibling directory
COPY libs/dagster-io/ /tmp/dagster-io/
RUN pip install --no-cache-dir /tmp/dagster-io/ && rm -rf /tmp/dagster-io/

COPY packages/data-explorer/pyproject.toml .
COPY packages/data-explorer/src/ src/
RUN pip install --no-cache-dir .

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "src/data_explorer/streamlit/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
