from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from database import get_db
from models import VendorMaster, SupplierRegistration, User
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Query, HTTPException

# API Router Setup
router = APIRouter(prefix="/api/dashboard/onboarding-compliance", tags=["Dashboard 1: Onboarding Compliance"])


def _base_query(db: Session, vendor_id: Optional[int]):
    """VendorMaster left-joined to its SupplierRegistration — left, not inner, so a vendor with
    no link (legacy/SAP-imported) still counts toward totals, same as a vendor with blank fields
    always did before this table stopped storing name/GST/PAN itself."""
    q = db.query(VendorMaster, SupplierRegistration).outerjoin(
        SupplierRegistration, VendorMaster.supplier_registration_id == SupplierRegistration.id
    )
    if vendor_id:
        q = q.filter(VendorMaster.vendor_id == vendor_id)
    else:
        # Only include vendors who have registered user accounts
        vendor_ids_with_accounts = db.query(User.company_id).filter(User.role == 'VENDOR').distinct()
        q = q.filter(VendorMaster.vendor_id.in_(vendor_ids_with_accounts))
    return q


@router.get("/summary")
def get_summary(db: Session = Depends(get_db), vendor_id: Optional[int] = Query(None)):
    try:
        query_base = _base_query(db, vendor_id)

        total_vendors = query_base.with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0

        # GST here now reads the real gst_number on the linked registration — this used to check
        # VendorMaster.company_code, which was never actually a GSTIN in any provisioning path (it
        # held the generated VEND-XXXXXXXX code, or was blank for SAP-imported vendors), so every
        # Become-a-Supplier vendor was previously miscounted as GST-non-compliant regardless of
        # their real GST status.
        valid_gst = query_base.filter(
            SupplierRegistration.gst_number.isnot(None),
            func.length(SupplierRegistration.gst_number) == 15
        ).with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0

        valid_pan = query_base.filter(
            SupplierRegistration.pan_number.isnot(None),
            SupplierRegistration.pan_number != 'IN',
            func.length(SupplierRegistration.pan_number) == 10
        ).with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0

        valid_name = query_base.filter(
            SupplierRegistration.vendor_name.isnot(None),
            SupplierRegistration.vendor_name != ''
        ).with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0

        fully_compliant = query_base.filter(
            SupplierRegistration.gst_number.isnot(None),
            func.length(SupplierRegistration.gst_number) == 15,
            SupplierRegistration.pan_number.isnot(None),
            SupplierRegistration.pan_number != 'IN',
            func.length(SupplierRegistration.pan_number) == 10,
            SupplierRegistration.vendor_name.isnot(None),
            SupplierRegistration.vendor_name != ''
        ).with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0

        missing_docs = query_base.filter(
            or_(
                SupplierRegistration.gst_number.is_(None),
                func.length(SupplierRegistration.gst_number) != 15,
                SupplierRegistration.pan_number.is_(None),
                SupplierRegistration.pan_number == 'IN',
                func.length(SupplierRegistration.pan_number) != 10,
                SupplierRegistration.vendor_name.is_(None),
                SupplierRegistration.vendor_name == ''
            )
        ).with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0

        expiring_soon = 0 # Dynamic calculation can be added later

        compliance_pct = round((fully_compliant / total_vendors * 100), 2) if total_vendors > 0 else 0
        coi_pct = round((valid_name / total_vendors * 100), 2) if total_vendors > 0 else 0
        gst_pct = round((valid_gst / total_vendors * 100), 2) if total_vendors > 0 else 0
        pan_pct = round((valid_pan / total_vendors * 100), 2) if total_vendors > 0 else 0

        missing_gst = total_vendors - valid_gst
        missing_pan = total_vendors - valid_pan
        missing_name = total_vendors - valid_name

        top_missing_list = [
            {"name": "GST Registration", "count": missing_gst},
            {"name": "PAN Card", "count": missing_pan},
            {"name": "Certificate of Incorporation", "count": missing_name},
        ]
        top_missing_list.sort(key=lambda x: x["count"], reverse=True)
        top_missing_list = [x for x in top_missing_list if x["count"] > 0][:3]

        return {
            "total": total_vendors,
            "fully_compliant": fully_compliant,
            "compliance_percentage": compliance_pct,
            "missing_docs": missing_docs,
            "expiring_soon": expiring_soon,
            "coi_percentage": coi_pct,
            "gst_percentage": gst_pct,
            "pan_percentage": pan_pct,
            "top_missing": top_missing_list
        }
    except Exception as e:
        print(f"Error: {e}")
        return {}

@router.get("/alerts")
def get_compliance_alerts(db: Session = Depends(get_db), vendor_id: Optional[int] = Query(None)):
    try:
        alerts = []
        query_base = _base_query(db, vendor_id)

        missing_rows = query_base.filter(
            or_(
                SupplierRegistration.gst_number.is_(None),
                SupplierRegistration.gst_number == '',
                SupplierRegistration.pan_number.is_(None),
                SupplierRegistration.pan_number == 'IN',
                SupplierRegistration.pan_number == ''
            )
        ).limit(10).all()

        for v, reg in missing_rows:
            gst = reg.gst_number if reg else None
            pan = reg.pan_number if reg else None
            name = reg.vendor_name if reg else None

            missing_items = []
            if not gst or len(str(gst)) != 15:
                missing_items.append("GST Registration")
            if not pan or pan == 'IN' or len(str(pan)) != 10:
                missing_items.append("PAN Card")

            if missing_items:
                alerts.append({
                    "vendor": name if name else f"Vendor {v.bp_no}",
                    "document": f"{missing_items[0]} — not on file",
                    "status": "MISSING",
                    "notification": "Email · esc: 5d"
                })

        # In a real scenario, this would check actual expiry dates from another table.
        # Since nothing currently tracks document expiry dates here, we omit dummy alerts to keep it real.

        alerts.sort(key=lambda x: 0 if x['status'] == 'MISSING' else 1)

        return {
            "alerts": alerts[:10],
            "total_missing": len([a for a in alerts if a['status'] == 'MISSING']),
            "total_expiring": len([a for a in alerts if a['status'] == 'EXPIRING'])
        }
    except Exception as e:
        print(f"Error fetching alerts: {e}")
        return {"alerts": [], "total_missing": 0, "total_expiring": 0}

@router.get("/vendor/{vendor_id}")
def get_vendor_details(vendor_id: int, db: Session = Depends(get_db)):
    """
    Get detailed compliance data for a SINGLE vendor.
    """
    row = db.query(VendorMaster, SupplierRegistration).outerjoin(
        SupplierRegistration, VendorMaster.supplier_registration_id == SupplierRegistration.id
    ).filter(VendorMaster.vendor_id == vendor_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Vendor not found")
    vendor, reg = row

    gst = reg.gst_number if reg else None
    pan = reg.pan_number if reg else None
    name = reg.vendor_name if reg else None

    missing_docs = []
    if not gst or len(str(gst)) != 15:
        missing_docs.append("GST Registration")
    if not pan or pan == 'IN' or len(str(pan)) != 10:
        missing_docs.append("PAN Card")
    if not name or name.strip() == '':
        missing_docs.append("Certificate of Incorporation")

    return {
        "vendor_id": vendor.vendor_id,
        "name": name,
        "bp_no": vendor.bp_no,
        "gst_number": gst,
        "pan": pan,
        "is_fully_compliant": len(missing_docs) == 0,
        "missing_documents": missing_docs,
        "sys_created_date": vendor.sys_created_date
    }
