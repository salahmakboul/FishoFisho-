import os
import sys

print("=== DATABASE TEST ===")
in_env = "DATABASE_URL" in os.environ
print(f"DATABASE_URL in env: {in_env}")
db_url = os.environ.get("DATABASE_URL", "NOT SET")
print(f"DATABASE_URL value: {db_url[:50] if len(db_url) > 50 else db_url}")

# Try to connect if URL exists
if db_url != "NOT SET":
    try:
        import dj_database_url
        db_config = dj_database_url.parse(db_url)
        print(f"Database engine: {db_config.get(
ENGINE, unknown)}")
        print(f"Database name: {db_config.get(NAME, unknown)}")
        print("SUCCESS: Database URL looks valid")
    except Exception as e:
        print(f"ERROR parsing DATABASE_URL: {e}")
        sys.exit(1)
else:
    print("ERROR: DATABASE_URL not set!")
    sys.exit(1)
