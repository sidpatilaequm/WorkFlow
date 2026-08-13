from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db

router = APIRouter(prefix="/api/vendor", tags=["Vendor Portal"])

@router.get("/material-list")
def get_vendor_material_list(request: Request, db: Session = Depends(get_db)):
    # Try to extract company_id/vendor_id from user if provided or available
    vendor_id = request.query_params.get("company_id")
    
    # Query real materials from database
    query = "SELECT * FROM material"
    params = {}
    vendor_info = {}
    if vendor_id:
        query += " WHERE vendor_id = :vendor_id"
        params["vendor_id"] = vendor_id
        
        # Get vendor details
        v_query = "SELECT bp_no, name FROM vendor_master WHERE vendor_id = :vendor_id"
        try:
            v_row = db.execute(text(v_query), {"vendor_id": vendor_id}).fetchone()
            if v_row:
                v_r = v_row._mapping
                vendor_info = {
                    "sapVendorCode": v_r.get("bp_no"),
                    "sapVendorName": v_r.get("name"),
                    "companyCode": vendor_id
                }
        except Exception as e:
            pass
        
    result = db.execute(text(query), params).fetchall()
    
    materials = []
    for row in result:
        r = row._mapping
        materials.append({
            "materialCode": r.get("material_code"),
            "description": r.get("description"),
            "uom": r.get("base_unit_of_measure"),
            "hsnCode": r.get("hsn_code"),
            "contractNumber": r.get("contract_number"),
            "contractPrice": r.get("price"),
            "paymentTerms": r.get("payment_terms"),
            "deliveryTerms": r.get("delivery_terms"),
            "status": "Inactive" if r.get("blocked") in [1, True, b'\x01'] else "Active",
            "companyCode": r.get("company_id"),
        })
        
    return {
        "vendorInfo": vendor_info,
        "summary": {
            "totalMaterials": len(materials),
        },
        "materials": materials
    }
