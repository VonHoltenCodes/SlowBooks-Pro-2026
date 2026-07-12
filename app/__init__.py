# Single source of truth for the application version: app/main.py reports
# it on the FastAPI app and /health, /api/system serves it to the frontend
# footer, and the Windows release workflow parses it for the installer.
__version__ = "2.1.0"
