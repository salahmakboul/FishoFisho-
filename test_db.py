import os
import sys

print("=== DATABASE TEST ===")
print(f"DATABASE_URL in env: {\"DATABASE_URL\" in os.environ}")
print(f"DATABASE_URL value: {os.environ.get(\"DATABASE_URL\", \"NOT SET\")[:50] if os.environ.get(\"DATABASE_URL\") else \"NOT SET\"}")

# Try to connect if URL exists
if os.environ.get("DATABASE_URL"):
    try:
        import dj_database_url
        db_config = dj_database_url.parse(os.environ["DATABASE_URL"])
        print(f"Database config: {db_config[\"ENGINE\"]}")
        print("SUCCESS: Database URL looks valid")
    except Exception as e:
        print(f"ERROR parsing DATABASE_URL: {e}")
else:
    print("ERROR: DATABASE_URL not set!")
    sys.exit(1)
