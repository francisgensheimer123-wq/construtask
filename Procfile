web: python manage.py collectstatic --noinput --clear && gunicorn setup.wsgi --bind 0.0.0.0:8080 --timeout 120 --workers 2 --log-level debug0
