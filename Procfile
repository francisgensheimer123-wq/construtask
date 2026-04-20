web: gunicorn setup.wsgi:application --bind 0.0.0.0:$PORT
release: python manage.py migrate
release: python manage.py collectstatic --noinput
