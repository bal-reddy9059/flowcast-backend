"""Custom alert rules engine endpoints."""

import logging
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.rule import AlertRule, RuleEvaluation
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/rules", tags=["Alert Rules Engine"])
logger = logging.getLogger(__name__)

_VALID_METRICS = {"congestion_level", "average_speed", "vehicle_count"}
_VALID_OPERATORS = {">=", "<=", "==", ">", "<"}
_CONGESTION_NUM = {"low": 1, "medium": 2, "high": 3}


class RuleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    location: str = Field(..., min_length=2, max_length=200)
    condition_metric: str = Field("congestion_level")
    condition_operator: str = Field(">=")
    condition_value: str = Field("high")
    duration_minutes: int = Field(5, ge=1, le=120)
    action_type: str = Field("notify", pattern="^(notify|webhook|both)$")
    action_webhook_id: Optional[uuid.UUID] = None
    cooldown_minutes: int = Field(30, ge=5, le=1440)
    org_id: Optional[uuid.UUID] = None


class RuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    location: Optional[str] = Field(None, min_length=2, max_length=200)
    condition_metric: Optional[str] = None
    condition_operator: Optional[str] = None
    condition_value: Optional[str] = None
    duration_minutes: Optional[int] = Field(None, ge=1, le=120)
    action_type: Optional[str] = None
    cooldown_minutes: Optional[int] = Field(None, ge=5, le=1440)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: RuleCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Create a custom alert rule.

    **Examples:**
    - Alert when `congestion_level >= high` at Silk Board for 10+ minutes
    - Alert when `average_speed <= 15` km/h at Gachibowli for 5+ minutes
    - Alert when `vehicle_count >= 2000` at MG Road for 5+ minutes
    """
    if payload.condition_metric not in _VALID_METRICS:
        raise HTTPException(status_code=400, detail=f"condition_metric must be one of {_VALID_METRICS}")
    if payload.condition_operator not in _VALID_OPERATORS:
        raise HTTPException(status_code=400, detail=f"condition_operator must be one of {_VALID_OPERATORS}")
    if payload.action_type in ("webhook", "both") and not payload.action_webhook_id:
        raise HTTPException(status_code=400, detail="action_webhook_id required when action_type includes webhook")

    rule = AlertRule(
        user_id=current_user.id,
        org_id=payload.org_id,
        name=payload.name,
        location=payload.location,
        condition_metric=payload.condition_metric,
        condition_operator=payload.condition_operator,
        condition_value=payload.condition_value,
        duration_minutes=payload.duration_minutes,
        action_type=payload.action_type,
        action_webhook_id=payload.action_webhook_id,
        cooldown_minutes=payload.cooldown_minutes,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    logger.info("Alert rule '%s' created by user %s", rule.name, current_user.id)
    return _rule_dict(rule)


@router.get("", status_code=status.HTTP_200_OK)
def list_rules(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """List all alert rules for the current user."""
    rules = db.query(AlertRule).filter(AlertRule.user_id == current_user.id).all()
    return {"rules": [_rule_dict(r) for r in rules], "total": len(rules)}


@router.get("/{rule_id}", status_code=status.HTTP_200_OK)
def get_rule(
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Get rule details with last 10 trigger events."""
    rule = _get_rule_or_404(rule_id, current_user.id, db)
    evals = (
        db.query(RuleEvaluation)
        .filter(RuleEvaluation.rule_id == rule_id)
        .order_by(RuleEvaluation.triggered_at.desc())
        .limit(10)
        .all()
    )
    return {
        **_rule_dict(rule),
        "recent_triggers": [
            {
                "triggered_at": e.triggered_at.isoformat(),
                "metric_value": e.metric_value,
                "location": e.location,
            }
            for e in evals
        ],
    }


@router.put("/{rule_id}", status_code=status.HTTP_200_OK)
def update_rule(
    rule_id: uuid.UUID,
    payload: RuleUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Update an alert rule."""
    rule = _get_rule_or_404(rule_id, current_user.id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    return _rule_dict(rule)


@router.put("/{rule_id}/toggle", status_code=status.HTTP_200_OK)
def toggle_rule(
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Enable or disable an alert rule."""
    rule = _get_rule_or_404(rule_id, current_user.id, db)
    rule.is_active = not rule.is_active
    db.commit()
    return {"rule_id": str(rule.id), "is_active": rule.is_active, "message": "Rule " + ("enabled" if rule.is_active else "disabled")}


@router.delete("/{rule_id}", status_code=status.HTTP_200_OK)
def delete_rule(
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Delete an alert rule and all its evaluation history."""
    rule = _get_rule_or_404(rule_id, current_user.id, db)
    db.delete(rule)
    db.commit()
    return {"message": f"Rule '{rule.name}' deleted"}


@router.get("/{rule_id}/history", status_code=status.HTTP_200_OK)
def rule_history(
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Full trigger history for a rule (last 100 events)."""
    rule = _get_rule_or_404(rule_id, current_user.id, db)
    evals = (
        db.query(RuleEvaluation)
        .filter(RuleEvaluation.rule_id == rule_id)
        .order_by(RuleEvaluation.triggered_at.desc())
        .limit(100)
        .all()
    )
    return {
        "rule_id": str(rule.id),
        "rule_name": rule.name,
        "triggers": [{"triggered_at": e.triggered_at.isoformat(), "metric_value": e.metric_value} for e in evals],
        "total": len(evals),
    }


# ── Condition evaluator (used by background rule engine) ──────────────────────

def eval_condition(metric: str, operator: str, threshold: str, actual_value) -> bool:
    if actual_value is None:
        return False
    if metric == "congestion_level":
        actual_num = _CONGESTION_NUM.get(str(actual_value).lower(), 0)
        threshold_num = _CONGESTION_NUM.get(str(threshold).lower(), 0)
        a, b = actual_num, threshold_num
    else:
        try:
            a = float(actual_value)
            b = float(threshold)
        except (ValueError, TypeError):
            return False
    if operator == ">=":   return a >= b
    if operator == "<=":   return a <= b
    if operator == "==":   return a == b
    if operator == ">":    return a > b
    if operator == "<":    return a < b
    return False


def _get_rule_or_404(rule_id, user_id, db) -> AlertRule:
    r = db.query(AlertRule).filter(AlertRule.id == rule_id, AlertRule.user_id == user_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Rule not found")
    return r


def _rule_dict(rule: AlertRule) -> dict:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "location": rule.location,
        "condition": f"{rule.condition_metric} {rule.condition_operator} {rule.condition_value} for {rule.duration_minutes} min",
        "condition_metric": rule.condition_metric,
        "condition_operator": rule.condition_operator,
        "condition_value": rule.condition_value,
        "duration_minutes": rule.duration_minutes,
        "action_type": rule.action_type,
        "cooldown_minutes": rule.cooldown_minutes,
        "is_active": rule.is_active,
        "last_triggered_at": rule.last_triggered_at.isoformat() if rule.last_triggered_at else None,
        "created_at": rule.created_at.isoformat(),
    }
