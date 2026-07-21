import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:bala2808@localhost:5432/traffic-data")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    # Fail before the 3.5 s HTTP deadline instead of leaving cancelled request
    # threads queued behind a saturated pool.
    pool_timeout=0.5,
    # Fail fast on locked tables — never freeze the asyncio event loop waiting on ALTER
    connect_args={
        "connect_timeout": 1,
        "options": "-c lock_timeout=500 -c statement_timeout=2500",
    },
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency that provides a DB session and ensures locks are released."""
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            db.rollback()  # end open txn so we never leave idle-in-transaction
        except Exception:
            pass
        db.close()


def test_connection():
    """Quick connectivity check — called on startup."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[OK] Database connected: traffic-data @ localhost:5432")
        return True
    except Exception as e:
        print(f"[ERROR] Database connection failed: {e}")
        return False


def migrate_users_id_to_uuid() -> None:
    """Drop and recreate all user-dependent tables when users.id is still INTEGER.

    Runs on every startup but exits immediately once users.id is already UUID.
    All test data is wiped — this is intentional for the one-time schema migration.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'id'"
            )).fetchone()

        if row is None:
            return  # Table doesn't exist yet — create_all() will build it fresh
        if row[0].lower() == "uuid":
            return  # Already migrated

        print("[MIGRATE] users.id is INTEGER — dropping user tables to migrate to UUID…")
        _USER_TABLES = [
            "notifications",
            "route_share_tokens",
            "departure_alerts",
            "favorite_locations",
            "user_preferences",
            "trip_history",
            "saved_routes",
            "users",
        ]
        with engine.begin() as conn:
            for table in _USER_TABLES:
                conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        print("[MIGRATE] Done — create_all() will recreate tables with UUID primary key")
    except Exception as exc:
        print(f"[WARN] UUID migration check failed: {exc}")


def migrate_routes_id_to_uuid() -> None:
    """Drop and recreate saved_routes and its dependent tables when id is still INTEGER.

    Runs on every startup but exits immediately once saved_routes.id is already UUID.
    All existing route data is wiped — intentional one-time schema migration.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'saved_routes' AND column_name = 'id'"
            )).fetchone()

        if row is None:
            return  # Table doesn't exist yet — create_all() will build it fresh
        if row[0].lower() == "uuid":
            return  # Already migrated

        print("[MIGRATE] saved_routes.id is INTEGER — dropping dependent tables to migrate to UUID…")
        _ROUTE_TABLES = [
            "route_share_tokens",
            "notifications",
            "saved_routes",
        ]
        with engine.begin() as conn:
            for table in _ROUTE_TABLES:
                conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        print("[MIGRATE] Done — create_all() will recreate route tables with UUID primary key")
    except Exception as exc:
        print(f"[WARN] Routes UUID migration check failed: {exc}")


def migrate_favorites_id_to_uuid() -> None:
    """Drop and recreate favorite_locations when id is still INTEGER.

    Runs on every startup but exits immediately once id is already UUID.
    Existing favorites are wiped — intentional one-time schema migration.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'favorite_locations' AND column_name = 'id'"
            )).fetchone()

        if row is None:
            return  # Table doesn't exist yet — create_all() will build it fresh
        if row[0].lower() == "uuid":
            return  # Already migrated

        print("[MIGRATE] favorite_locations.id is INTEGER — dropping to migrate to UUID…")
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS favorite_locations CASCADE"))
        print("[MIGRATE] Done — create_all() will recreate favorite_locations with UUID primary key")
    except Exception as exc:
        print(f"[WARN] Favorites UUID migration check failed: {exc}")


def migrate_notifications_id_to_uuid() -> None:
    """Drop and recreate notifications table when id is still INTEGER."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'notifications' AND column_name = 'id'"
            )).fetchone()

        if row is None:
            return
        if row[0].lower() == "uuid":
            return

        print("[MIGRATE] notifications.id is INTEGER — dropping to migrate to UUID…")
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS notifications CASCADE"))
        print("[MIGRATE] Done — create_all() will recreate notifications with UUID primary key")
    except Exception as exc:
        print(f"[WARN] Notifications UUID migration check failed: {exc}")


def migrate_alerts_id_to_uuid() -> None:
    """Drop and recreate departure_alerts table when id is still INTEGER."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'departure_alerts' AND column_name = 'id'"
            )).fetchone()

        if row is None:
            return
        if row[0].lower() == "uuid":
            return

        print("[MIGRATE] departure_alerts.id is INTEGER — dropping to migrate to UUID…")
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS departure_alerts CASCADE"))
        print("[MIGRATE] Done — create_all() will recreate departure_alerts with UUID primary key")
    except Exception as exc:
        print(f"[WARN] Alerts UUID migration check failed: {exc}")


def migrate_trips_id_to_uuid() -> None:
    """Drop and recreate trip_history table when id is still INTEGER."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'trip_history' AND column_name = 'id'"
            )).fetchone()

        if row is None:
            return
        if row[0].lower() == "uuid":
            return

        print("[MIGRATE] trip_history.id is INTEGER — dropping to migrate to UUID…")
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS trip_history CASCADE"))
        print("[MIGRATE] Done — create_all() will recreate trip_history with UUID primary key")
    except Exception as exc:
        print(f"[WARN] Trips UUID migration check failed: {exc}")


def seed_admin_user() -> None:
    """Create the default admin account if no admin user exists yet.

    Credentials (change in production via env vars):
      ADMIN_EMAIL    — default: admin@flowcast.in
      ADMIN_PASSWORD — default: Admin@1234

    If admin@flowcast.in already exists as a regular user (e.g. registered via
    /auth/register before seed ran), it is promoted to admin automatically.
    """
    import os
    admin_email    = os.getenv("ADMIN_EMAIL",    "admin@flowcast.in")
    admin_password = os.getenv("ADMIN_PASSWORD", "Admin@1234")

    try:
        from app.models.user import User
        from app.services.auth_service import hash_password

        db = SessionLocal()
        try:
            # If any admin already exists, nothing to do
            if db.query(User).filter(User.is_admin.is_(True)).first():
                return

            # Promote the target email if it was registered as a regular user
            existing = db.query(User).filter(User.email == admin_email).first()
            if existing:
                existing.is_admin   = True
                existing.last_login = existing.last_login or datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
                print(f"[OK] Promoted existing user to admin → {admin_email}")
                return

            # Create a fresh admin account
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            admin = User(
                full_name       = "FlowCast Admin",
                email           = admin_email,
                hashed_password = hash_password(admin_password),
                is_active       = True,
                is_admin        = True,
                last_login      = now,
            )
            db.add(admin)
            db.commit()
            print(f"[OK] Admin user created → email: {admin_email}  password: {admin_password}")
        finally:
            db.close()
    except Exception as exc:
        print(f"[WARN] Admin seed failed: {exc}")


def cleanup_stale_db_backends(max_idle_seconds: int = 30) -> int:
    """Terminate backends that wedge traffic_records (idle-in-transaction / stuck ALTER).

    Multiple reloads used to queue AccessExclusiveLock ALTERs behind abandoned
    sessions, freezing every traffic query. Safe: only targets this database and
    never the current connection.
    """
    killed = 0
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT pid, state, left(query, 80) AS q
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid()
                      AND (
                        (state LIKE 'idle in transaction%'
                         AND xact_start < NOW() - (:idle * INTERVAL '1 second'))
                        OR (wait_event_type = 'Lock'
                            AND query ILIKE '%ALTER TABLE traffic_records%'
                            AND query_start < NOW() - INTERVAL '5 seconds')
                        OR (wait_event_type = 'Lock'
                            AND query ILIKE '%CREATE INDEX%traffic_records%'
                            AND query_start < NOW() - INTERVAL '5 seconds')
                      )
                    """
                ),
                {"idle": max_idle_seconds},
            ).fetchall()
            for row in rows:
                try:
                    ok = conn.execute(
                        text("SELECT pg_terminate_backend(:pid)"),
                        {"pid": row.pid},
                    ).scalar()
                    if ok:
                        killed += 1
                        print(f"[OK] Terminated stale backend pid={row.pid} state={row.state}")
                except Exception as exc:
                    print(f"[WARN] Could not terminate pid={row.pid}: {type(exc).__name__}")
        if killed:
            print(f"[OK] Cleared {killed} stale DB session(s)")
    except Exception as exc:
        print(f"[WARN] Stale-backend cleanup skipped: {type(exc).__name__}")
    return killed


def run_column_migrations():
    """Add missing columns only. Skip ALTER when the column already exists.

    PostgreSQL takes AccessExclusiveLock even for ADD COLUMN IF NOT EXISTS,
    which used to freeze HTTP while traffic_records was locked.
    Index creation is deferred — never run at boot on a live table.
    """
    all_columns = [
        ("traffic_records",    "record_uuid",    "VARCHAR(36)"),
        ("traffic_records",    "data_source",    "VARCHAR(20) DEFAULT 'manual'"),
        ("prediction_results", "prediction_uuid", "VARCHAR(36)"),
        ("incidents",          "incident_uuid",   "VARCHAR(36)"),
        ("users", "auth_provider", "VARCHAR(20) DEFAULT 'local' NOT NULL"),
        ("users", "google_id",     "VARCHAR(255)"),
        ("users", "picture_url",   "VARCHAR(500)"),
        ("incidents", "reported_by", "VARCHAR(36)"),
        ("incidents", "upvotes",     "INTEGER DEFAULT 0 NOT NULL"),
        ("incidents", "downvotes",   "INTEGER DEFAULT 0 NOT NULL"),
        ("incidents", "expires_at",  "TIMESTAMPTZ"),
        ("webhooks",  "name",        "VARCHAR(200)"),
    ]
    try:
        cleanup_stale_db_backends(max_idle_seconds=20)

        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("SET lock_timeout = '1500ms'"))
            conn.execute(text("SET statement_timeout = '5000ms'"))
            for table, col, dtype in all_columns:
                exists = conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
                ), {"t": table, "c": col}).fetchone()
                if exists:
                    continue
                try:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {dtype}"
                    ))
                    print(f"[OK] Added {table}.{col}")
                except Exception as col_exc:
                    print(f"[WARN] Skip {table}.{col}: {type(col_exc).__name__}")
        print("[OK] Column migrations finished")
    except Exception as e:
        print(f"[WARN] Column migration skipped: {e}")


def ensure_traffic_indexes() -> None:
    """Create query-critical indexes without blocking traffic writes."""
    try:
        cleanup_stale_db_backends(max_idle_seconds=20)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("SET lock_timeout = '2s'"))
            conn.execute(text("SET statement_timeout = '30s'"))
            for ddl in (
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_traffic_records_location_created "
                "ON traffic_records (location, created_at DESC)",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_traffic_records_created_at "
                "ON traffic_records (created_at DESC)",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_traffic_records_timestamp "
                "ON traffic_records (timestamp DESC)",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_traffic_records_location_timestamp "
                "ON traffic_records (location, timestamp DESC)",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidents_active_reported "
                "ON incidents (is_active, reported_at DESC)",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidents_reported_at "
                "ON incidents (reported_at DESC)",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidents_active_expiry "
                "ON incidents (expires_at) WHERE is_active = true AND expires_at IS NOT NULL",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidents_created_at "
                "ON incidents (created_at DESC)",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_prediction_active_created "
                "ON prediction_results (is_active, created_at DESC)",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_zone_alerts_zone_triggered "
                "ON zone_alerts (zone_id, triggered_at DESC)",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rule_evaluations_triggered "
                "ON rule_evaluations (triggered_at DESC)",
            ):
                try:
                    conn.execute(text(ddl))
                    index_pos = ddl.split().index("EXISTS") + 1
                    print(f"[OK] Index ready: {ddl.split()[index_pos]}")
                except Exception as idx_exc:
                    print(f"[WARN] Index create skipped: {type(idx_exc).__name__}")
    except Exception as e:
        print(f"[WARN] ensure_traffic_indexes skipped: {e}")


def run_startup_migrations() -> None:
    """All boot migrations in one worker thread — call before background monitors."""
    cleanup_stale_db_backends(max_idle_seconds=15)
    _run = (
        migrate_users_id_to_uuid,
        migrate_routes_id_to_uuid,
        migrate_favorites_id_to_uuid,
        migrate_notifications_id_to_uuid,
        migrate_trips_id_to_uuid,
        migrate_alerts_id_to_uuid,
        run_column_migrations,
    )
    for fn in _run:
        try:
            fn()
        except Exception as exc:
            print(f"[WARN] {fn.__name__} failed: {type(exc).__name__}: {exc}")
    # Indexes after columns — still best-effort, never blocks API
    try:
        ensure_traffic_indexes()
    except Exception:
        pass



def backfill_uuid_columns(batch_size: int = 2000) -> None:
    """Optional maintenance — backfill missing UUIDs in batches (not run on startup)."""
    uuid_cols = [
        ("traffic_records",    "record_uuid"),
        ("prediction_results", "prediction_uuid"),
        ("incidents",          "incident_uuid"),
    ]
    try:
        with engine.begin() as conn:
            for table, col in uuid_cols:
                needs = conn.execute(text(
                    f"SELECT 1 FROM {table} WHERE {col} IS NULL LIMIT 1"
                )).fetchone()
                if not needs:
                    continue
                conn.execute(text(
                    f"UPDATE {table} SET {col} = gen_random_uuid()::text "
                    f"WHERE id IN ("
                    f"  SELECT id FROM {table} WHERE {col} IS NULL LIMIT {batch_size}"
                    f")"
                ))
    except Exception as e:
        print(f"[WARN] UUID backfill skipped: {e}")
