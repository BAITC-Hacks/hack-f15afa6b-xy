"""Pulse 109 — operator queue listing: views, filters, pagination, stable order.

Ordering rule: a human-confirmed urgent complaint goes first, then complaints by
their event time (received_at when known, otherwise ingested_at), oldest first,
with the id as a deterministic tie-break. Confirmed status alone does not make a
complaint urgent: only the human-confirmed priority value counts.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query

VIEWS = {"pending", "clarification", "confirmed", "all"}
VIEW_STATUS = {"pending": "pending", "clarification": "needs_clarification", "confirmed": "confirmed"}
PRIORITY_FILTERS = {"urgent", "normal", "unknown"}

ORDER_BY = (
    "ORDER BY CASE WHEN decision_status = 'confirmed' AND priority = 'urgent' THEN 0 ELSE 1 END, "
    "COALESCE(received_at, ingested_at) ASC, id ASC"
)


def build_queue_router(get_connection: Callable[[], Any], banner_text: str, valid_region_ids: set[str]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/complaints")
    def list_complaints(
        view: str = Query("all"),
        region_id: Optional[str] = Query(None),
        priority: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=50),
    ):
        if view not in VIEWS:
            raise HTTPException(status_code=422, detail=f"Invalid view: {view}")
        if region_id is not None and region_id not in valid_region_ids:
            raise HTTPException(status_code=422, detail=f"Unknown region_id: {region_id}")
        if priority is not None and priority not in PRIORITY_FILTERS:
            raise HTTPException(status_code=422, detail=f"Invalid priority filter: {priority}")

        filter_where: list[str] = []
        filter_params: list[Any] = []
        if region_id is not None:
            filter_where.append("region_id = ?")
            filter_params.append(region_id)
        if priority == "urgent":
            filter_where.append("priority = 'urgent'")
        elif priority == "normal":
            filter_where.append("priority = 'normal'")
        elif priority == "unknown":
            filter_where.append("(priority IS NULL OR priority NOT IN ('normal', 'urgent'))")
        filter_clause = (" WHERE " + " AND ".join(filter_where)) if filter_where else ""

        view_where = list(filter_where)
        view_params = list(filter_params)
        if view != "all":
            view_where.append("decision_status = ?")
            view_params.append(VIEW_STATUS[view])
        view_clause = (" WHERE " + " AND ".join(view_where)) if view_where else ""

        with get_connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM complaints{view_clause}", view_params).fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            current = min(page, pages)
            rows = conn.execute(
                f"SELECT * FROM complaints{view_clause} {ORDER_BY} LIMIT ? OFFSET ?",
                (*view_params, page_size, (current - 1) * page_size),
            ).fetchall()
            by_status = {
                r[0]: r[1]
                for r in conn.execute(
                    f"SELECT decision_status, COUNT(*) FROM complaints{filter_clause} GROUP BY decision_status",
                    filter_params,
                ).fetchall()
            }
        view_counts = {
            "pending": by_status.get("pending", 0),
            "clarification": by_status.get("needs_clarification", 0),
            "confirmed": by_status.get("confirmed", 0),
            "all": sum(by_status.values()),
        }
        return {
            "banner": banner_text,
            "view": view,
            "filters": {"region_id": region_id, "priority": priority},
            "items": [dict(r) for r in rows],
            "page": current,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "view_counts": view_counts,
        }

    return router
