# tulletogether.com daily site check. Build context = this folder (Railway service Root Directory = site_check).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 STATE_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY checks.py run.py config.json ./
CMD ["python", "run.py"]
