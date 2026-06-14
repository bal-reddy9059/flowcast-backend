"""Organization and team management endpoints."""

import logging
import re
import uuid
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.org import OrgMembership, Organization
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/org", tags=["Organizations"])
logger = logging.getLogger(__name__)

_ROLE_ORDER = {"member": 0, "admin": 1, "owner": 2}


# ── Role dependency ────────────────────────────────────────────────────────────

def _require_role(min_role: str):
    def _dep(
        org_id: uuid.UUID,
        current_user: Annotated[User, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> OrgMembership:
        membership = (
            db.query(OrgMembership)
            .filter(OrgMembership.org_id == org_id, OrgMembership.user_id == current_user.id)
            .first()
        )
        if not membership:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this organization")
        if _ROLE_ORDER.get(membership.role, -1) < _ROLE_ORDER.get(min_role, 0):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Requires {min_role} role or higher")
        return membership
    return _dep


# ── Schemas ────────────────────────────────────────────────────────────────────

class OrgCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    plan: str = Field("free", pattern="^(free|pro|enterprise)$")


class OrgUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    plan: Optional[str] = Field(None, pattern="^(free|pro|enterprise)$")


class InviteRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: str = Field("member", pattern="^(member|admin)$")


class RoleUpdate(BaseModel):
    role: str = Field(..., pattern="^(member|admin|owner)$")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
def create_org(
    payload: OrgCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Create a new organization. The creator becomes the owner."""
    slug = re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-")
    if db.query(Organization).filter(Organization.slug == slug).first():
        raise HTTPException(status_code=409, detail="An organization with this name already exists")

    org = Organization(
        name=payload.name,
        slug=slug,
        plan=payload.plan,
        created_by=current_user.id,
    )
    db.add(org)
    db.flush()

    membership = OrgMembership(org_id=org.id, user_id=current_user.id, role="owner")
    db.add(membership)
    db.commit()
    db.refresh(org)

    logger.info("Organization '%s' created by user %s", org.name, current_user.id)
    return _org_dict(org, membership)


def _primary(user_id, db) -> tuple:
    """Return (Organization, OrgMembership) for the user's first active org, or (None, None)."""
    membership = (
        db.query(OrgMembership)
        .filter(OrgMembership.user_id == user_id)
        .order_by(OrgMembership.joined_at.asc())
        .first()
    )
    if not membership:
        return None, None
    org = db.query(Organization).filter(
        Organization.id == membership.org_id, Organization.is_active == True
    ).first()
    return org, membership


def _ensure_primary(user_id, db, current_user) -> tuple:
    """Return (org, membership), auto-creating a personal org when user has none."""
    org, membership = _primary(user_id, db)
    if org and membership:
        return org, membership

    # Build a name unique to this user so it never collides with the UNIQUE constraint
    display = (getattr(current_user, "full_name", None) or
               getattr(current_user, "email", "user").split("@")[0]).strip().title()
    uid_suffix = str(user_id)[:6].upper()
    name = f"{display}'s Workspace"
    # Guarantee uniqueness even if two users share the same display name
    if db.query(Organization).filter(Organization.name == name).first():
        name = f"{display}'s Workspace ({uid_suffix})"

    slug = f"ws-{str(user_id)[:8]}"
    if db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"ws-{str(user_id)[:12]}"

    org = Organization(
        name=name,
        slug=slug,
        plan="enterprise",
        created_by=user_id,
    )
    db.add(org)
    db.flush()
    membership = OrgMembership(org_id=org.id, user_id=user_id, role="owner")
    db.add(membership)
    db.commit()
    db.refresh(org)
    logger.info("Personal workspace '%s' auto-created for user %s", name, user_id)
    return org, membership


# ── Primary-org convenience endpoints (no org_id in URL) ──────────────────────
# IMPORTANT: must be declared before GET /org/{org_id} to avoid path collision.

@router.get("", status_code=status.HTTP_200_OK)
def get_my_org(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Return the current user's primary organization (auto-creates one if needed)."""
    org, membership = _ensure_primary(current_user.id, db, current_user)
    return {
        "id":       str(org.id),
        "name":     org.name,
        "plan":     org.plan.capitalize(),
        "my_role":  membership.role,
        "your_role": membership.role,
    }


@router.get("/members", status_code=status.HTTP_200_OK)
def list_my_org_members(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """List members of the current user's primary organization."""
    org, _ = _primary(current_user.id, db)
    if not org:
        return {"members": [], "total": 0}

    memberships = db.query(OrgMembership).filter(OrgMembership.org_id == org.id).all()
    members = []
    for m in memberships:
        user = db.query(User).filter(User.id == m.user_id).first()
        if user:
            members.append({
                "id":        str(m.user_id),
                "user_id":   str(m.user_id),
                "full_name": user.full_name or user.email,
                "email":     user.email,
                "role":      m.role,
                "joined_at": m.joined_at.isoformat(),
                "is_active": bool(user.is_active),
            })
    return {"members": members, "total": len(members)}


@router.post("/invite", status_code=status.HTTP_201_CREATED)
def invite_to_my_org(
    payload: InviteRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Invite a registered user to the current user's primary organization (admin+)."""
    org, membership = _primary(current_user.id, db)
    if not org:
        raise HTTPException(status_code=404, detail="You are not a member of any organization")
    if _ROLE_ORDER.get(membership.role, 0) < _ROLE_ORDER["admin"]:
        raise HTTPException(status_code=403, detail="Requires admin role or higher")

    invitee = db.query(User).filter(User.email == payload.email, User.is_active == True).first()
    if not invitee:
        raise HTTPException(status_code=404, detail="No active user found with that email")
    if db.query(OrgMembership).filter(
        OrgMembership.org_id == org.id, OrgMembership.user_id == invitee.id
    ).first():
        raise HTTPException(status_code=409, detail="User is already a member")

    new_m = OrgMembership(org_id=org.id, user_id=invitee.id, role=payload.role, invited_by=current_user.id)
    db.add(new_m)
    db.commit()
    logger.info("User %s invited to org %s by %s", invitee.id, org.id, current_user.id)
    return {
        "id":        str(invitee.id),
        "full_name": invitee.full_name,
        "email":     invitee.email,
        "role":      payload.role,
        "joined_at": new_m.joined_at.isoformat(),
        "is_active": True,
    }


@router.put("/members/{user_id}", status_code=status.HTTP_200_OK)
def change_role_in_my_org(
    user_id: uuid.UUID,
    payload: RoleUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Change a member's role in the current user's primary organization (admin+)."""
    org, membership = _primary(current_user.id, db)
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")
    if _ROLE_ORDER.get(membership.role, 0) < _ROLE_ORDER["admin"]:
        raise HTTPException(status_code=403, detail="Requires admin role or higher")

    target = db.query(OrgMembership).filter(
        OrgMembership.org_id == org.id, OrgMembership.user_id == user_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.role == "owner" and payload.role != "owner":
        raise HTTPException(status_code=403, detail="Cannot change the owner's role")
    target.role = payload.role
    db.commit()
    return {"message": f"Role updated to {payload.role}", "user_id": str(user_id)}


@router.delete("/members/{user_id}", status_code=status.HTTP_200_OK)
def remove_from_my_org(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Remove a member from the current user's primary organization (admin+)."""
    org, membership = _primary(current_user.id, db)
    if not org:
        raise HTTPException(status_code=404, detail="No organization found")
    if _ROLE_ORDER.get(membership.role, 0) < _ROLE_ORDER["admin"]:
        raise HTTPException(status_code=403, detail="Requires admin role or higher")
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    target = db.query(OrgMembership).filter(
        OrgMembership.org_id == org.id, OrgMembership.user_id == user_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot remove the organization owner")
    db.delete(target)
    db.commit()
    return {"message": "Member removed", "user_id": str(user_id)}


@router.get("/mine", status_code=status.HTTP_200_OK)
def list_my_orgs(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """List all organizations the current user belongs to."""
    memberships = (
        db.query(OrgMembership)
        .filter(OrgMembership.user_id == current_user.id)
        .all()
    )
    results = []
    for m in memberships:
        org = db.query(Organization).filter(Organization.id == m.org_id, Organization.is_active == True).first()
        if org:
            results.append(_org_dict(org, m))
    return {"organizations": results, "total": len(results)}


@router.get("/{org_id}", status_code=status.HTTP_200_OK)
def get_org(
    org_id: uuid.UUID,
    membership: Annotated[OrgMembership, Depends(_require_role("member"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Get organization details (members only)."""
    org = db.query(Organization).filter(Organization.id == org_id, Organization.is_active == True).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    member_count = db.query(OrgMembership).filter(OrgMembership.org_id == org_id).count()
    return {**_org_dict(org, membership), "member_count": member_count}


@router.put("/{org_id}", status_code=status.HTTP_200_OK)
def update_org(
    org_id: uuid.UUID,
    payload: OrgUpdate,
    membership: Annotated[OrgMembership, Depends(_require_role("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Update organization name or plan (admin+)."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if payload.name:
        org.name = payload.name
        org.slug = re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-")
    if payload.plan:
        org.plan = payload.plan
    db.commit()
    db.refresh(org)
    return _org_dict(org, membership)


@router.delete("/{org_id}", status_code=status.HTTP_200_OK)
def delete_org(
    org_id: uuid.UUID,
    membership: Annotated[OrgMembership, Depends(_require_role("owner"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Soft-delete an organization (owner only)."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.is_active = False
    db.commit()
    return {"message": f"Organization '{org.name}' deactivated"}


@router.post("/{org_id}/members/invite", status_code=status.HTTP_201_CREATED)
def invite_member(
    org_id: uuid.UUID,
    payload: InviteRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[OrgMembership, Depends(_require_role("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Invite a registered user by email (admin+)."""
    invitee = db.query(User).filter(User.email == payload.email, User.is_active == True).first()
    if not invitee:
        raise HTTPException(status_code=404, detail="No active user found with that email")
    existing = db.query(OrgMembership).filter(
        OrgMembership.org_id == org_id, OrgMembership.user_id == invitee.id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="User is already a member")
    new_membership = OrgMembership(
        org_id=org_id, user_id=invitee.id, role=payload.role, invited_by=current_user.id
    )
    db.add(new_membership)
    db.commit()
    logger.info("User %s invited to org %s by %s", invitee.id, org_id, current_user.id)
    return {
        "message": f"{invitee.full_name} added as {payload.role}",
        "user_id": str(invitee.id),
        "email": invitee.email,
        "role": payload.role,
    }


@router.get("/{org_id}/members", status_code=status.HTTP_200_OK)
def list_members(
    org_id: uuid.UUID,
    membership: Annotated[OrgMembership, Depends(_require_role("member"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """List all members with their roles."""
    memberships = db.query(OrgMembership).filter(OrgMembership.org_id == org_id).all()
    members = []
    for m in memberships:
        user = db.query(User).filter(User.id == m.user_id).first()
        members.append({
            "user_id": str(m.user_id),
            "full_name": user.full_name if user else "Unknown",
            "email": user.email if user else "",
            "role": m.role,
            "joined_at": m.joined_at.isoformat(),
        })
    return {"members": members, "total": len(members)}


@router.put("/{org_id}/members/{target_user_id}/role", status_code=status.HTTP_200_OK)
def change_member_role(
    org_id: uuid.UUID,
    target_user_id: uuid.UUID,
    payload: RoleUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[OrgMembership, Depends(_require_role("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Change a member's role (admin+). Cannot demote the owner."""
    target = db.query(OrgMembership).filter(
        OrgMembership.org_id == org_id, OrgMembership.user_id == target_user_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.role == "owner" and payload.role != "owner":
        raise HTTPException(status_code=403, detail="Cannot change the owner's role")
    target.role = payload.role
    db.commit()
    return {"message": f"Role updated to {payload.role}", "user_id": str(target_user_id)}


@router.delete("/{org_id}/members/{target_user_id}", status_code=status.HTTP_200_OK)
def remove_member(
    org_id: uuid.UUID,
    target_user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[OrgMembership, Depends(_require_role("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Remove a member from the organization (admin+). Cannot remove the owner."""
    if target_user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    target = db.query(OrgMembership).filter(
        OrgMembership.org_id == org_id, OrgMembership.user_id == target_user_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="Cannot remove the organization owner")
    db.delete(target)
    db.commit()
    return {"message": "Member removed", "user_id": str(target_user_id)}


# ── Helper ─────────────────────────────────────────────────────────────────────

def _org_dict(org: Organization, membership: OrgMembership) -> dict:
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "plan": org.plan,
        "is_active": org.is_active,
        "your_role": membership.role,
        "created_at": org.created_at.isoformat(),
    }
