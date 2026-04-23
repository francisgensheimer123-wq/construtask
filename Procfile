release: python manage.py check --deploy && python manage.py migrate --noinput
web: gunicorn setup.wsgi --bind 0.0.0.0:8080 --timeout 120 --workers 2 --log-level info
worker: python -m celery -A setup worker --loglevel=info --concurrency=2
worker2: python -m celery -A setup beat --loglevel=info
