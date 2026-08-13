from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from database import get_db
from models import VendorMaster, User
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Query, HTTPException

# API Router Setup
router = APIRouter(prefix="/api/dashboard/onboarding-compliance", tags=["Dashboard 1: Onboarding Compliance"])

@router.get("/summary")
def get_summary(db: Session = Depends(get_db), vendor_id: Optional[int] = Query(None)):
    try:
        query_base = db.query(VendorMaster)
        
        # If a vendor logs in (passed from frontend), only show their own data
        if vendor_id:
            query_base = query_base.filter(VendorMaster.vendor_id == vendor_id)
        else:
            # Only include vendors who have registered user accounts
            vendor_ids_with_accounts = db.query(User.company_id).filter(User.role == 'VENDOR').distinct()
            query_base = query_base.filter(VendorMaster.vendor_id.in_(vendor_ids_with_accounts))
            
        total_vendors = query_base.with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0
        
        valid_gst = query_base.filter(
            VendorMaster.company_code.isnot(None),
            func.length(VendorMaster.company_code) == 15
        ).with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0
        
        valid_pan = query_base.filter(
            VendorMaster.pan.isnot(None),
            VendorMaster.pan != 'IN',
            func.length(VendorMaster.pan) == 10
        ).with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0
        
        valid_name = query_base.filter(
            VendorMaster.name.isnot(None),
            VendorMaster.name != ''
        ).with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0
        
        fully_compliant = query_base.filter(
            VendorMaster.company_code.isnot(None),
            func.length(VendorMaster.company_code) == 15,
            VendorMaster.pan.isnot(None),
            VendorMaster.pan != 'IN',
            func.length(VendorMaster.pan) == 10,
            VendorMaster.name.isnot(None),
            VendorMaster.name != ''
        ).with_entities(func.count(VendorMaster.vendor_id)).scalar() or 0
        
        missing_docs = query_base.filter(
            or_(
                VendorMaster.company_code.is_(None),
                func.length(VendorMaster.company_code) != 15,
                VendorMaster.pan.is_(None),
                VendorMaster.pan == 'IN',
                func.length(VendorMaster.pan) != 10,
                VendorMaster.name.is_(None),
                VendorMaster.name == ''
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
        query_base = db.query(VendorMaster)
        
        if vendor_id:
            query_base = query_base.filter(VendorMaster.vendor_id == vendor_id)
        else:
            vendor_ids_with_accounts = db.query(User.company_id).filter(User.role == 'VENDOR').distinct()
            query_base = query_base.filter(VendorMaster.vendor_id.in_(vendor_ids_with_accounts))
            
        missing_vendors = query_base.filter(
            or_(
                VendorMaster.company_code.is_(None),
                VendorMaster.company_code == '',
                VendorMaster.pan.is_(None),
                VendorMaster.pan == 'IN',
                VendorMaster.pan == ''
            )
        ).limit(10).all()
        
        for v in missing_vendors:
            missing_docs = []
            if not v.company_code or len(str(v.company_code)) != 15:
                missing_docs.append("GST Registration")
            if not v.pan or v.pan == 'IN' or len(str(v.pan)) != 10:
                missing_docs.append("PAN Card")
            
            if missing_docs:
                alerts.append({
                    "vendor": v.name if v.name else f"Vendor {v.bp_no}",
                    "document": f"{missing_docs[0]} — not on file",
                    "status": "MISSING",
                    "notification": "Email · esc: 5d"
                })
        
        recent_vendors = query_base.filter(
            VendorMaster.sys_created_date >= datetime.now() - timedelta(days=90)
        ).limit(5).all()
        
        # In a real scenario, this would check actual expiry dates from another table.
        # Since VendorMaster currently lacks document expiry dates, we omit these dummy alerts to keep it real.
        
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
    vendor = db.query(VendorMaster).filter(VendorMaster.vendor_id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
        
    missing_docs = []
    
    if not vendor.company_code or len(str(vendor.company_code)) != 15:
        missing_docs.append("GST Registration")
    if not vendor.pan or vendor.pan == 'IN' or len(str(vendor.pan)) != 10:
        missing_docs.append("PAN Card")
    if not vendor.name or vendor.name.strip() == '':
        missing_docs.append("Certificate of Incorporation")
        
    return {
        "vendor_id": vendor.vendor_id,
        "name": vendor.name,
        "bp_no": vendor.bp_no,
        "gst_number": vendor.company_code,
        "pan": vendor.pan,
        "is_fully_compliant": len(missing_docs) == 0,
        "missing_documents": missing_docs,
        "sys_created_date": vendor.sys_created_date
    }
