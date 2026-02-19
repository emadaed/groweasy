web: gunicorn main:app --bind 0.0.0.0:8080 --timeout 120 --workers 1 --preload
worker: celery -A app.services.tasks.celery worker --loglevel=info