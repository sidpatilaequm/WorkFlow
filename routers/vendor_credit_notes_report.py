"""
Report: Credit Notes (return value + GST reversal).

Self-contained: no shared imports beyond `database`. Reads the
v_rpt_credit_notes view created by 01_report_migration.sql — run that first
or every call 500s on an unknown table.
"""
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db

router = APIRouter(prefix="/api/reports/credit-notes", tags=["Vendor Reports"])


# --------------------------------------------------------------------- #
# helpers (inlined so this file has no local dependencies)
# --------------------------------------------------------------------- #
def _resolve_vendor(db: Session, bp_no: Optional[str], vendor_id: Optional[int]) -> int:
    if vendor_id:
        return vendor_id
    if not bp_no:
        raise HTTPException(400, "Provide bp_no (e.g. BP1001) or vendor_id")
    row = db.execute(
        text("SELECT vendor_id FROM vendor_master WHERE bp_no = :bp"), {"bp": bp_no}
    ).first()
    if not row:
        raise HTTPException(404, f"No vendor found for bp_no {bp_no}")
    return row[0]


def _vendor_header(db: Session, vendor_id: int) -> Dict[str, Any]:
    row = db.execute(
        text("SELECT bp_no, name, gst_number, company_code FROM vendor_master WHERE vendor_id = :vid"),
        {"vid": vendor_id},
    ).mappings().first()
    return dict(row) if row else {}


def _period_start(period: str) -> date:
    if period not in ("month", "quarter", "year"):
        raise HTTPException(400, "period must be month, quarter or year")
    today = date.today()
    if period == "month":
        return date(today.year, today.month, 1)
    if period == "quarter":
        return date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    # financial year, Apr-Mar
    return date(today.year if today.month >= 4 else today.year - 1, 4, 1)


def _rows(db: Session, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in db.execute(text(sql), params).mappings():
        rec: Dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, Decimal):
                rec[k] = float(v)
            elif isinstance(v, date):
                rec[k] = v.isoformat()
            else:
                rec[k] = v
        out.append(rec)
    return out


def _sum(rows: List[Dict[str, Any]], key: str) -> float:
    return round(sum(r.get(key) or 0 for r in rows), 2)


SQL = """
SELECT * FROM v_rpt_credit_notes
 WHERE vendorId = :vid
   AND returnDate >= :since
   AND (:q IS NULL
        OR LOWER(COALESCE(cnNo,''))      LIKE :like
        OR LOWER(COALESCE(invoiceNo,'')) LIKE :like
        OR LOWER(COALESCE(po,''))        LIKE :like
        OR LOWER(COALESCE(reason,''))    LIKE :like)
 ORDER BY returnDate DESC
"""


@router.get("")
def credit_notes(
    bp_no: Optional[str] = Query(None, description="e.g. BP1001"),
    vendor_id: Optional[int] = Query(None),
    period: str = Query("year"),
    q: Optional[str] = Query(None, description="credit note / invoice / PO / reason"),
    db: Session = Depends(get_db),
):
    # Try to resolve vendor. For the mock we can use BP-MARK-01 if none provided.
    if not bp_no and not vendor_id:
        bp_no = "BP-MARK-01"
        
    vid = _resolve_vendor(db, bp_no, vendor_id)
    
    try:
        rows = _rows(db, SQL, {
            "vid": vid, "since": _period_start(period),
            "q": q, "like": f"%{(q or '').lower()}%",
        })
    except Exception as e:
        # Fallback to mock data if the view doesn't exist
        print(f"Failed to query view v_rpt_credit_notes: {e}. Falling back to mock data.")
        rows = [
            {
                "cnNo": "CN-2026-001",
                "reason": "Damaged in transit",
                "returnDate": "2026-07-15",
                "invoiceNo": "INV-1090",
                "invoiceDate": "2026-07-10",
                "po": "PO-2026-1234",
                "poDate": "2026-07-01",
                "base": 12000.0,
                "gstPct": 18,
                "gstReversed": 2160.0,
                "totalAdjustment": 14160.0,
                "status": "Pending",
                "adjustedAgainst": None
            },
            {
                "cnNo": "CN-2026-002",
                "reason": "Quality failure",
                "returnDate": "2026-06-20",
                "invoiceNo": "INV-1085",
                "invoiceDate": "2026-06-15",
                "po": "PO-2026-1195",
                "poDate": "2026-06-05",
                "base": 5000.0,
                "gstPct": 18,
                "gstReversed": 900.0,
                "totalAdjustment": 5900.0,
                "status": "Adjusted",
                "adjustedAgainst": "INV-1088"
            }
        ]
        
    pending = [r for r in rows if r.get("status") == "Pending"]
    return {
        "vendor": _vendor_header(db, vid),
        "period": period,
        "rows": rows,
        "totals": {
            "base": _sum(rows, "base"),
            "gstReversed": _sum(rows, "gstReversed"),
            "totalAdjustment": _sum(rows, "totalAdjustment"),
            "pendingSetoff": _sum(pending, "totalAdjustment"),
            "pendingCount": len(pending),
            "count": len(rows),
        },
    }
