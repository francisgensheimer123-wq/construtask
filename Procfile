release: python manage.py collectstatic --noinput
web: gunicorn setup.wsgi:application --bind 0.0.0.0:$PORT
