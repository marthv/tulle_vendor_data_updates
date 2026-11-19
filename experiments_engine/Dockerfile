# experiments-engine (FRAMEWORK.md section 5). Build context = repo root; Railway service
# variable RAILWAY_DOCKERFILE_PATH=experiments_engine/Dockerfile (or railway.experiments.toml).
#
# Lives in experiments_engine/, NOT at the repo root: a root-level "Dockerfile" is auto-detected
# by Railway for every service built from "/" (Tulle Admin Dash, health-report-cron, batch-*,
# tulle-slack-qa, weweb-deploy-notify-cron ...) and would replace their Railpack builds.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EXP_DATA_DIR=/data/experiments

WORKDIR /app

COPY experiments_engine/requirements.txt experiments_engine/requirements.txt
RUN pip install -r experiments_engine/requirements.txt

# Only the engine and the registry: nothing else from the repo is in the image.
COPY experiments_engine/ experiments_engine/
COPY experiments/ experiments/

EXPOSE 8080
# One worker: the scheduler thread lives in the process. "::" listens on IPv6 + IPv4, which
# Railway private networking (experiments-engine.railway.internal) needs.
CMD ["python", "-m", "experiments_engine.run"]
