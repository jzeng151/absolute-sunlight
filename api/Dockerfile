# Build from the repository root: docker build -f api/Dockerfile .
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHON_DOTENV_DISABLED=1

WORKDIR /app/api
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system --gid 10001 scram \
    && useradd --system --uid 10001 --gid scram --create-home scram

COPY --chown=scram:scram api/app ./app
# The API loads the same rulebook, cases and baseline as the frontend.
COPY --chown=scram:scram web/src/fixtures /app/web/src/fixtures

USER scram
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import json, urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4))['sandbox_ready']"

# Runs, approval locks and sandbox ownership currently belong to one process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
