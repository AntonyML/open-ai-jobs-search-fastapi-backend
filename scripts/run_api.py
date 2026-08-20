"""Start the API principal server."""

import sys

import uvicorn

sys.path.insert(0, ".")
from app.main import create_app

uvicorn.run(create_app(), port=8000, host="127.0.0.1", log_level="info")
