"""
One-shot script to create or promote a FlowCast admin user.

Usage:
    python create_admin.py
    python create_admin.py admin2@flowcast.in MyPassword123
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Allow passing email/password as command-line args
EMAIL    = sys.argv[1] if len(sys.argv) > 1 else os.getenv("ADMIN_EMAIL",    "admin@flowcast.in")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else os.getenv("ADMIN_PASSWORD", "Admin@1234")

from app.database import SessionLocal
# Import all related models so SQLAlchemy relationship resolution works
from app.models.user import User                                        # noqa: F401
from app.models.route import SavedRoute                                 # noqa: F401
from app.models.notification import Notification                        # noqa: F401
from app.models.predictor import TrafficRecord, Incident, PredictionResult  # noqa: F401
from app.models.favorite import FavoriteLocation                        # noqa: F401
from app.models.preferences import UserPreferences                      # noqa: F401
from app.models.trip import TripHistory                                 # noqa: F401
from app.models.alert import DepartureAlert                             # noqa: F401
from app.models.share import RouteShareToken                            # noqa: F401
from app.services.auth_service import hash_password

from datetime import datetime

db = SessionLocal()
try:
    user = db.query(User).filter(User.email == EMAIL).first()
    if user:
        user.is_admin   = True
        user.is_active  = True
        user.last_login = user.last_login or datetime.utcnow()
        db.commit()
        print(f"[OK] Promoted '{EMAIL}' to admin. Log in with your existing password.")
    else:
        now = datetime.utcnow()
        db.add(User(
            full_name       = "FlowCast Admin",
            email           = EMAIL,
            hashed_password = hash_password(PASSWORD),
            is_active       = True,
            is_admin        = True,
            last_login      = now,
        ))
        db.commit()
        print(f"[OK] Admin created  →  email: {EMAIL}   password: {PASSWORD}")
finally:
    db.close()
