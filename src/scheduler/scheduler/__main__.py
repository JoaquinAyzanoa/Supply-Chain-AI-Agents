"""Run the scheduler service with ``python -m scheduler``."""

from sc_core.app.run import serve

if __name__ == "__main__":
    serve("scheduler")
