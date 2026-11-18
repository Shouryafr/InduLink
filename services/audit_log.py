"""Append-only audit trail (brief sections 5 and 6, item 12).

Every important change records actor, time, action, target, reason and the
before/after version references. The table refuses UPDATE and DELETE, both at
the ORM level (event listener) and in the database (triggers created by the
initial migration), so history cannot be rewritten by application code.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session

from data.models import AuditEvent, User


class AuditImmutableError(RuntimeError):
    """Raised when anything attempts to modify or delete an audit event."""


def record(
    session: Session,
    *,
    actor: User | None,
    action: str,
    target_type: str = "",
    target_id: str | int | None = "",
    reason: str = "",
    before_ref: str = "",
    after_ref: str = "",
    detail: dict | None = None,
    ip: str = "",
) -> AuditEvent:
    """Append one audit event to the current transaction."""
    if not action or not action.strip():
        raise ValueError("An audit event needs an action.")
    event_row = AuditEvent(
        actor_id=getattr(actor, "id", None),
        actor_role=getattr(actor, "role", "anonymous") or "anonymous",
        action=action.strip()[:80],
        target_type=(target_type or "")[:60],
        target_id="" if target_id is None else str(target_id)[:60],
        reason=(reason or "")[:2000],
        before_ref=(before_ref or "")[:120],
        after_ref=(after_ref or "")[:120],
        detail=detail or {},
        ip=(ip or "")[:64],
    )
    session.add(event_row)
    return event_row


def recent(session: Session, *, limit: int = 50, target_type: str | None = None) -> list[AuditEvent]:
    query = session.query(AuditEvent).order_by(AuditEvent.id.desc())
    if target_type:
        query = query.filter(AuditEvent.target_type == target_type)
    return list(query.limit(limit))


@event.listens_for(Session, "before_flush")
def _block_audit_mutation(session: Session, _flush_context, _instances) -> None:
    """Refuse any ORM-level update or delete of an audit row."""
    for obj in session.dirty:
        if isinstance(obj, AuditEvent) and session.is_modified(obj, include_collections=False):
            raise AuditImmutableError("Audit events are append-only and cannot be modified.")
    for obj in session.deleted:
        if isinstance(obj, AuditEvent):
            raise AuditImmutableError("Audit events are append-only and cannot be deleted.")


@event.listens_for(Session, "do_orm_execute")
def _block_bulk_audit_mutation(execute_state) -> None:  # pragma: no cover - defensive
    if not execute_state.is_update and not execute_state.is_delete:
        return
    statement = execute_state.statement
    table = getattr(statement, "table", None)
    if table is not None and getattr(table, "name", "") == "audit_events":
        raise AuditImmutableError("Audit events are append-only and cannot be modified.")
