"""One-time migration: backfill notification.location from title for all users."""
from app.database import engine
from sqlalchemy import text

# Step 1 — show what's still null (should only be system/departure notifications)
CHECK_SQL = """
SELECT title, notification_type, severity, location
FROM notifications
WHERE location IS NULL
ORDER BY created_at DESC;
"""

# Step 2 — extract location from title using SQL regex for ALL users at once
UPDATE_SQL = """
UPDATE notifications
SET location = TRIM(REGEXP_REPLACE(SPLIT_PART(title, ' — ', 2), ' → .+$', ''))
WHERE location IS NULL
  AND title LIKE '% — %'
  AND LOWER(TRIM(SPLIT_PART(title, ' — ', 2))) NOT LIKE 'leave in%'
  AND LOWER(TRIM(SPLIT_PART(title, ' — ', 2))) NOT LIKE '%minute%'
  AND LOWER(TRIM(SPLIT_PART(title, ' — ', 2))) NOT LIKE 'weekly%'
  AND LOWER(TRIM(SPLIT_PART(title, ' — ', 2))) NOT LIKE 'real-time%'
  AND LOWER(TRIM(SPLIT_PART(title, ' — ', 2))) NOT LIKE 'scheduled%';
"""

with engine.connect() as conn:
    print("=== Null-location notifications (before migration) ===")
    rows = conn.execute(text(CHECK_SQL)).fetchall()
    if rows:
        for r in rows:
            print(f"  title={r[0]!r:55s} type={r[1]} loc={r[3]!r}")
    else:
        print("  (none — all locations already populated)")

    print(f"\nTotal null-location rows: {len(rows)}")

    if rows:
        print("\nRunning UPDATE …")
        result = conn.execute(text(UPDATE_SQL))
        conn.commit()
        print(f"Updated {result.rowcount} rows.")

        null_remaining = conn.execute(
            text("SELECT COUNT(*) FROM notifications WHERE location IS NULL")
        ).scalar()
        print(f"Remaining null (system/departure — expected): {null_remaining}")
    else:
        print("\nNothing to migrate — all done.")
