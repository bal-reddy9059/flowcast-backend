import os
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
