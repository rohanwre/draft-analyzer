# snowflake_config.py
#
# Separate from config.py's DB_CONFIG (MySQL) since this is a second, independent
# target for the same production summary tables - see load_snowflake.py.

from dotenv import load_dotenv
import os

load_dotenv()

SNOWFLAKE_CONFIG = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT"),
    "user": os.getenv("SNOWFLAKE_USER"),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
    "database": os.getenv("SNOWFLAKE_DATABASE", "DRAFT_ANALYZER"),
    "schema": os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"),
}

# Key-pair auth takes precedence over password: Snowflake accounts with MFA enforced
# reject password logins from scripts, so SNOWFLAKE_PRIVATE_KEY_PATH is the normal path.
if os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH"):
    SNOWFLAKE_CONFIG["private_key_file"] = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
else:
    SNOWFLAKE_CONFIG["password"] = os.getenv("SNOWFLAKE_PASSWORD")
