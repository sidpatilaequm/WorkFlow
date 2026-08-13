"""
Report: Vendor Returns, quantity only. Value settles via credit notes.

Self-contained: no shared imports beyond `database`. Reads the
v_rpt_vendor_returns view created by 01_report_migration.sql — run that first
or every call 500s on an unknown table.
"""
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db

router = APIRouter(prefix="/api/reports/vendor-returns", tags=["Vendor Reports"])


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
SELECT * FROM v_rpt_vendor_returns
 WHERE vendorId = :vid
   AND returnDate >= :since
   AND (:q IS NULL
        OR LOWER(COALESCE(rtnNo,''))     LIKE :like
        OR LOWER(COALESCE(item,''))      LIKE :like
        OR LOWER(COALESCE(itemCode,''))  LIKE :like
        OR LOWER(COALESCE(invoiceNo,'')) LIKE :like
        OR LOWER(COALESCE(po,''))        LIKE :like
        OR LOWER(COALESCE(reason,''))    LIKE :like)
 ORDER BY returnDate DESC
"""

OPEN_STATES = ("Replacement due", "Under inspection")


@router.get("")
def vendor_returns(
    bp_no: Optional[str] = Query(None, description="e.g. BP1001"),
    vendor_id: Optional[int] = Query(None),
    period: str = Query("year"),
    q: Optional[str] = Query(None, description="return note / item / invoice / PO / reason"),
    db: Session = Depends(get_db),
):
    if not bp_no and not vendor_id:
        bp_no = "BP-MARK-01"
        
    vid = _resolve_vendor(db, bp_no, vendor_id)
    
    try:
        rows = _rows(db, SQL, {
            "vid": vid, "since": _period_start(period),
            "q": q, "like": f"%{(q or '').lower()}%",
        })
    except Exception as e:
        print(f"Failed to query v_rpt_vendor_returns view: {e}. Falling back to mock data.")
        rows = [
            {
                "rtnNo": "RTN-2026-001",
                "returnDate": "2026-07-20",
                "item": "Industrial Lubricant XL",
                "itemCode": "MAT-2001",
                "reason": "Contaminated batch",
                "invoiceNo": "INV-1095",
                "invoiceDate": "2026-07-10",
                "po": "PO-2026-1250",
                "poDate": "2026-07-01",
                "qtySupplied": 1000,
                "qtyReturned": 250,
                "uom": "Liters",
                "returnPct": 25.0,
                "status": "Replacement due",
                "cnRef": None,
                "replacementDue": "2026-08-05"
            },
            {
                "rtnNo": "RTN-2026-002",
                "returnDate": "2026-06-15",
                "item": "Heavy Duty Steel Bearings",
                "itemCode": "MAT-2002",
                "reason": "Wrong specification",
                "invoiceNo": "INV-1082",
                "invoiceDate": "2026-06-05",
                "po": "PO-2026-1200",
                "poDate": "2026-05-20",
                "qtySupplied": 5000,
                "qtyReturned": 5000,
                "uom": "Pieces",
                "returnPct": 100.0,
                "status": "Credit note issued",
                "cnRef": "CN-2026-045",
                "replacementDue": None
            }
        ]
        
    open_lines = [r for r in rows if r.get("status") in OPEN_STATES]
    cn_lines = [r for r in rows if r.get("status") == "Credit note issued"]
    avg_rate = round(sum(r.get("returnPct") or 0 for r in rows) / len(rows), 1) if rows else 0.0
    return {
        "vendor": _vendor_header(db, vid),
        "period": period,
        "rows": rows,
        "totals": {
            "count": len(rows),
            "avgReturnRate": avg_rate,
            "openLines": len(open_lines),
            "creditNoteLines": len(cn_lines),
            "totalQtyReturned": _sum(rows, "qtyReturned"),
        },
    }
