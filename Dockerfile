FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
RUN mkdir -p /data/statements
ENV FLASK_APP=app.py
ENV FLASK_DEBUG=0
EXPOSE 8080
# One worker (SQLite writes and the embedded scheduler need a single process), 8 threads so a
# slow request — a photo upload on mobile data, a scanned statement, an SMS call — doesn't make
# everyone else wait. SQLite (WAL, one connection per call) serialises the writes itself.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--worker-class", "gthread", "--threads", "8", "--timeout", "120", "app:app"]
