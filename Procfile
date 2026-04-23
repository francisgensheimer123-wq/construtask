release: python manage.py check --deploy && python manage.py migrate --noinput
web: python start_web.py
worker: python -m celery -A setup worker --loglevel=info --concurrency=2
worker2: python -m celery -A setup beat --loglevel=info
