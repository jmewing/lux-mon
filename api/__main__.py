"""Entry point for the lux-mon REST API server."""

import os
import sys
from pathlib import Path

import uvicorn

HOST = os.getenv("LUX_API_HOST", "0.0.0.0")
PORT = int(os.getenv("LUX_API_PORT", "8080"))


def main():
    uvicorn.run(
        "api:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
