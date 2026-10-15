#!/usr/bin/env bash
# Render build command. Exit on the first error so a broken build never deploys.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
