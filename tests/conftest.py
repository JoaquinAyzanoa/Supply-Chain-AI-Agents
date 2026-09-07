"""Root test configuration."""

from sc_core.app.run import use_selector_loop_on_windows

# psycopg async needs a selector loop on Windows; harmless elsewhere.
use_selector_loop_on_windows()
