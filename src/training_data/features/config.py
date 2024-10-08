import os
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv(override=True)

# The number of days used to compile historical data for feature extraction
HISTORY_LIMIT = True
HISTORY_RANGE_DAYS = int(os.getenv('HISTORY_WINDOW') or '60')

# Number of days in a year
DAYS_PER_YEAR = 365.25

# Default merge ratio
DEFAULT_MERGE_RATIO = 0.5

# Same current time reference for feature calculations
DATETIME_NOW = datetime.now(timezone.utc)

# DB Preload Config
LOAD_PRS = 100
LOAD_PROCESSES = int(os.getenv('PREFILL_PROCESSES') or '2')