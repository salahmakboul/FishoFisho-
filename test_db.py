import os
print("=== ALL RAILWAY ENV VARS ===")
for key, value in sorted(os.environ.items()):
    if "RAILWAY" in key or "DATABASE" in key or "URL" in key or key in ["PORT", "DEBUG", "SECRET_KEY"]:
        print(f"{key}: {value[:50] if value else value}")
print("=== END ===")
