release: python manage.py migrate
web: gunicorn setup.wsgi:application --bind 0.0.0.0:$PORT
