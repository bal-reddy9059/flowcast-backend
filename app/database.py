import os
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:bala2808@localhost:5432/traffic-data")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,       # Checks connection health before using
    pool_size=10,             # Number of connections to keep
    max_overflow=20,          # Extra connections allowed beyond pool_size
    echo=False                # Set True to log all SQL queries
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency that provides a DB session and ensures it's closed after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
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
                existing.last_login = existing.last_login or datetime.utcnow()
                db.commit()
                print(f"[OK] Promoted existing user to admin → {admin_email}")
                return

            # Create a fresh admin account
            now = datetime.utcnow()
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


def run_column_migrations():
    """Add UUID columns to existing tables and backfill NULLs.

    Safe to run on every startup — ADD COLUMN IF NOT EXISTS is a no-op
    when the column already exists.
    """
    migrations = [
        ("traffic_records",    "record_uuid",    "VARCHAR(36)"),
        ("prediction_results", "prediction_uuid", "VARCHAR(36)"),
        ("incidents",          "incident_uuid",   "VARCHAR(36)"),
        # Google OAuth columns on users
        ("users", "auth_provider", "VARCHAR(20) DEFAULT 'local' NOT NULL"),
        ("users", "google_id",     "VARCHAR(255)"),
        ("users", "picture_url",   "VARCHAR(500)"),
    ]
    try:
        with engine.begin() as conn:
            for table, col, dtype in migrations:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {dtype}"
                ))
            for table, col, _ in migrations:
                conn.execute(text(
                    f"UPDATE {table} SET {col} = gen_random_uuid()::text WHERE {col} IS NULL"
                ))
        print("[OK] Column migrations applied (UUID columns ready)")
    except Exception as e:
        print(f"[WARN] Column migration skipped: {e}")
