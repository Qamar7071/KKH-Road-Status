# KKH Road Status Predictor: production image
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY kkh ./kkh
COPY app ./app
COPY data ./data
COPY models ./models

# RETRAIN=1 rebuilds the dataset from real sources (Open-Meteo, NASA, news) and retrains
# during the build. If the sources are unreachable it falls back to the bootstrap data.
ARG RETRAIN=0
RUN if [ "$RETRAIN" = "1" ]; then python -m kkh.dataset --mode auto && python -m kkh.model; fi

RUN useradd --create-home kkh && mkdir -p /app/var && chown -R kkh /app/data /app/var
USER kkh
ENV KKH_DB_PATH=/app/var/app.db PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health')"
CMD gunicorn "app.main:create_app()" --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 60 --access-logfile -
