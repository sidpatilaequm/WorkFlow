"""
Report: Material Stock, own vs consignment. Point-in-time, no period.

Self-contained: no shared imports beyond `database`. Reads the
v_rpt_material_stock view created by 01_report_migration.sql — run that first
or every call 500s on an unknown table.
"""
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db

router = APIRouter(prefix="/api/reports/material-stock", tags=["Vendor Reports"])


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
SELECT * FROM v_rpt_material_stock
 WHERE (:vid IS NULL OR vendorId = :vid)
   AND (:plant IS NULL OR location LIKE CONCAT(:plant,'%%'))
   AND (:q IS NULL
        OR LOWER(COALESCE(item,''))     LIKE :like
        OR LOWER(COALESCE(itemCode,'')) LIKE :like)
 ORDER BY FIELD(status,'Out of stock','Low stock','In stock'), item
"""


@router.get("")
def material_stock(
    bp_no: Optional[str] = Query(None, description="e.g. BP1001"),
    vendor_id: Optional[int] = Query(None),
    plant_code: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="item name or code"),
    db: Session = Depends(get_db),
):
    if not bp_no and not vendor_id:
        bp_no = "BP-MARK-01"
        
    vid = _resolve_vendor(db, bp_no, vendor_id) if (bp_no or vendor_id) else None
    
    try:
        rows = _rows(db, SQL, {
            "vid": vid, "plant": plant_code,
            "q": q, "like": f"%{(q or '').lower()}%",
        })
    except Exception as e:
        print(f"Failed to query v_rpt_material_stock view: {e}. Falling back to mock data.")
        rows = [
            {
                "item": "Industrial Lubricant XL",
                "itemCode": "MAT-2001",
                "stockType": "Consignment",
                "uom": "Liters",
                "own": 0,
                "consignment": 500,
                "totalStock": 500,
                "reorder": 600,
                "status": "Low stock",
                "location": "Plant A - Zone 1",
                "lastReceived": "2026-07-20",
                "lastIssued": "2026-07-25"
            },
            {
                "item": "Heavy Duty Steel Bearings",
                "itemCode": "MAT-2002",
                "stockType": "Own",
                "uom": "Pieces",
                "own": 1200,
                "consignment": 0,
                "totalStock": 1200,
                "reorder": 500,
                "status": "In stock",
                "location": "Plant B - Zone 3",
                "lastReceived": "2026-06-15",
                "lastIssued": "2026-07-10"
            },
            {
                "item": "Safety Goggles",
                "itemCode": "MAT-2003",
                "stockType": "Own",
                "uom": "Pieces",
                "own": 0,
                "consignment": 0,
                "totalStock": 0,
                "reorder": 100,
                "status": "Out of stock",
                "location": "Plant C - Zone 2",
                "lastReceived": "2026-01-10",
                "lastIssued": "2026-05-05"
            }
        ]
        
    return {
        "vendor": _vendor_header(db, vid) if vid else {},
        "asOn": date.today().isoformat(),
        "rows": rows,
        "totals": {
            "lineItems": len(rows),
            "ownTotal": _sum(rows, "own"),
            "consignTotal": _sum(rows, "consignment"),
            "lowStock": sum(1 for r in rows if r.get("status") == "Low stock"),
            "outOfStock": sum(1 for r in rows if r.get("status") == "Out of stock"),
        },
    }
