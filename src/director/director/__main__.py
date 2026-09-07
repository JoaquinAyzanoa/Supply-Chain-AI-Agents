"""Run the director service with ``python -m director``."""

import uvicorn


def run() -> None:
    """Start uvicorn on the default development port."""
    uvicorn.run("director.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
