import os
print("=== DATABASE TEST ===")
print("DATABASE_URL in env:", "DATABASE_URL" in os.environ)
db_url = os.environ.get("DATABASE_URL")
if db_url:
    print("DATABASE_URL length:", len(db_url))
    print("First 50 chars:", db_url[:50] if len(db_url) > 50 else db_url)
else:
    print("ERROR: DATABASE_URL is empty!")
    exit(1)
