"""Start the API principal server."""
import sys, asyncio
sys.path.insert(0, '.')
asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from app.main import create_app
import uvicorn
uvicorn.run(create_app(), port=8000, host='127.0.0.1', log_level='info')
