web: gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --worker-class gthread --workers ${WEB_CONCURRENCY:-1} --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-180}
