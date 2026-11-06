"""ورودی cPanel «Setup Python App» (Application startup file = passenger_wsgi.py, entry point = application)."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app import create_app  # noqa: E402

application = create_app()
