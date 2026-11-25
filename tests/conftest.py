import os
import tempfile

# Settings are read at import time, so point storage at a throwaway file before anything imports them.
os.environ["HT_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.sqlite3")
os.environ.pop("HT_LEADERBOARD_URL", None)
