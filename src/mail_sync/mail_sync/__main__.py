"""Run the mail_sync service with ``python -m mail_sync``."""

from sc_core.app.run import serve

if __name__ == "__main__":
    serve("mail_sync")
