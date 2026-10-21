FROM python:3.12-slim

RUN useradd --uid 10001 --create-home reconcile \
 && mkdir -p /data && chown reconcile:reconcile /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY eb_storage.py eb_events.py eb_model.py eb_reconcile.py ./

USER reconcile
ENV PYTHONUNBUFFERED=1 EB_RECONCILE_CONFIG=/app/config.json
# Healthy = the status file was written by a successful step in the last 10 minutes.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30m \
  CMD python -c "import json,time,sys; s=json.load(open('/data/eb-reconcile.status.json')); sys.exit(0 if s['healthy'] and time.time()-s['at']<600 else 1)"
ENTRYPOINT ["python", "/app/eb_reconcile.py"]
CMD ["run"]
