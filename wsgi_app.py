# wsgi_app.py
from fastapi import FastAPI
from fastapi.middleware.wsgi import WSGIMiddleware
from my_sqm_service import app  # your FastAPI app with /process

# Wrap FastAPI app for WSGI
application = WSGIMiddleware(app)

# Wrap FastAPI as WSGI
#wsgi_app = WSGIMiddleware(app)

