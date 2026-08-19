# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, Header, Query, HTTPException, BackgroundTasks
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
# pyrefly: ignore [missing-import]
from sqlalchemy import text, or_
from collections import defaultdict
from database import get_db
from models import VendorMaster, PurchaseRequisition, PurchaseRequisitionItem, PurchaseRequisitionItemVendor, VendorQuotation
import asyncio
from services.rfq_email_helper import send_rfq_invitation

router = APIRouter(prefix="/api/vendor", tags=["Vendor Portal (Mock)"])

@router.get("/create-pr-options")
def get_create_pr_options(db: Session = Depends(get_db)):
    try:
        locations = db.execute(text("SELECT location_id, location_name, city, state, country, is_active FROM location")).fetchall()
        
        def parse_bit(b):
            if isinstance(b, bytes):
                return b != b'\x00'
            return bool(b)
            
        loc_data = [{"id": r[0], "locationId": r[0], "locationName": r[1], "city": r[2], "state": r[3], "country": r[4], "isActive": parse_bit(r[5])} for r in locations]

        materials = db.execute(text("SELECT material_id, material_code, description, base_unit_of_measure, price FROM material")).fetchall()
        mat_data = [{"id": r[0], "materialId": r[0], "materialCode": r[1], "sku": r[1], "description": r[2], "name": r[2], "baseUnit": r[3], "uom": r[3], "unitPrice": r[4]} for r in materials]

        # Also return departments for the requester dropdown
        try:
            depts = db.execute(text("SELECT dept_code, dept_name FROM department ORDER BY dept_name")).fetchall()
            dept_data = [{"deptCode": r[0], "deptName": r[1]} for r in depts]
        except Exception:
            dept_data = []

        return {
            "locations": loc_data,
            "materials": mat_data,
            "departments": dept_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/purchase-requisition")
@router.get("/purchase-requisitions")
def get_purchase_requisitions(
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    vendor_code: str = Query(None, description="SAP Vendor Code (BP No)"),
    vendor_id: int = Query(None, description="Vendor ID"),
    exclude_quoted: bool = Query(False),
    db: Session = Depends(get_db)
):
    # 1. Fetch Vendor Info
    if not vendor_id and not vendor_code:
        vendor_code = "BP-MARK-01"

    if vendor_id:
        vendor = db.query(VendorMaster).filter(VendorMaster.vendor_id == vendor_id).first()
        if not vendor:
            raise HTTPException(status_code=404, detail=f"Vendor with ID {vendor_id} not found")
        vendor_code = vendor.bp_no
    else:
        vendor = db.query(VendorMaster).filter(VendorMaster.bp_no == vendor_code).first()
        if not vendor:
            raise HTTPException(status_code=404, detail=f"Vendor {vendor_code} not found")

    vendor_info = {
        "sapVendorCode": vendor.bp_no,
        "sapVendorName": vendor.name,
        "companyCode": vendor.company_code
    }

    # Retrieve the company_id which might be used as vendor_id by the frontend API
    company_id = db.execute(
        text("SELECT company_id FROM company_details WHERE company_code = :code"),
        {"code": vendor.bp_no}
    ).scalar()

    vendor_ids_to_check = [vendor.vendor_id]
    if company_id:
        vendor_ids_to_check.append(company_id)

    # 2. Find all Vendor Assignments for this Vendor
    vendor_assignments = db.query(PurchaseRequisitionItemVendor).filter(
        or_(
            PurchaseRequisitionItemVendor.vendor_id.in_(vendor_ids_to_check),
            PurchaseRequisitionItemVendor.bp_no == vendor.bp_no
        )
    ).all()

    if not vendor_assignments:
        # Return empty structure if no assignments found
        return {
            "vendorInfo": vendor_info,
            "summary": {"prApproved": 0, "quoteToPR": 0, "quoteWon": 0, "quotationRejected": 0, 
                        "prNotResponded": 0, "prRespondedLate": 0, "slaAdherence": 0.0, "winRate": 0.0, "notRespondedPct": 0.0},
            "prs": []
        }

    # Extract unique Item IDs and calculate base metrics
    item_ids = [a.purchase_requisition_item_id for a in vendor_assignments]
    quote_to_pr = len(vendor_assignments)
    quote_won = sum(1 for a in vendor_assignments if a.status == 'ACCEPTED')
    quotation_rejected = sum(1 for a in vendor_assignments if a.status == 'REJECTED')
    pr_not_responded = sum(1 for a in vendor_assignments if a.status == 'SENT')

    # 3. Fetch Line Items and PR Headers
    pr_items = db.query(PurchaseRequisitionItem).filter(
        PurchaseRequisitionItem.id.in_(item_ids)
    ).all()
    
    pr_ids = list(set([item.purchase_requisition_id for item in pr_items]))
    prs = db.query(PurchaseRequisition).filter(PurchaseRequisition.id.in_(pr_ids)).all()

    # Create a map of PR ID to PR object
    pr_map = {pr.id: pr for pr in prs}
    
    # Create a map of Item ID to Vendor Assignment Status
    item_status_map = {a.purchase_requisition_item_id: a.status for a in vendor_assignments}

    # 4. Calculate Summary Metrics
    pr_approved = sum(1 for pr in prs if pr.status in ['RELEASED', 'PARTIALLY_RELEASED'])
    total_prs = len(prs)

    sla_adherence = round((quote_to_pr / (quote_to_pr + pr_not_responded)) * 100, 1) if (quote_to_pr + pr_not_responded) > 0 else 0.0
    win_rate = round((quote_won / quote_to_pr) * 100, 1) if quote_to_pr > 0 else 0.0
    not_responded_pct = round((pr_not_responded / (quote_to_pr + pr_not_responded)) * 100, 1) if (quote_to_pr + pr_not_responded) > 0 else 0.0

    summary = {
        "prApproved": pr_approved,
        "quoteToPR": quote_to_pr,
        "quoteWon": quote_won,
        "quotationRejected": quotation_rejected,
        "prNotResponded": pr_not_responded,
        "prRespondedLate": 0, # Placeholder
        "slaAdherence": sla_adherence,
        "winRate": win_rate,
        "notRespondedPct": not_responded_pct
    }

    # 5. Build PR List with Nested Items (Matching Frontend JSON)
    pr_list_data = defaultdict(list)
    
    for item in pr_items:
        pr_id = item.purchase_requisition_id
        vendor_status = item_status_map.get(item.id, 'SENT')
        
        # Map DB status to Frontend status
        frontend_status = "Pending"
        if vendor_status == 'ACCEPTED': frontend_status = "Acknowledged"
        elif vendor_status == 'REJECTED': frontend_status = "Rejected"
        elif vendor_status == 'SENT': frontend_status = "Pending"

        pr_list_data[pr_id].append({
            "itemCode": item.sku or f"MAT-{item.material_id}",
            "itemDescription": f"Standard Material {item.sku or item.material_id}",
            "itemQuantity": float(item.quantity) if item.quantity else 0.0,
            "requestedDeliveryDate": str(pr_map[pr_id].required_date) if pr_map[pr_id].required_date else "",
            "requestedPaymentTerms": "Net 30 days",
            "status": frontend_status
        })

    prs_list = []
    
    # Check which PRs already have a quotation by this vendor
    quoted_prs = db.query(VendorQuotation.pr_id).filter(
        or_(
            VendorQuotation.vendor_id == vendor.vendor_id,
            VendorQuotation.bp_no == vendor.bp_no
        )
    ).all()
    quoted_pr_ids = {q[0] for q in quoted_prs}

    for pr_id, items in pr_list_data.items():
        if exclude_quoted and pr_id in quoted_pr_ids:
            continue
        
        pr = pr_map.get(pr_id)
        if not pr: continue
        
        # Determine overall PR status based on items
        if pr_id in quoted_pr_ids:
            overall_status = 'Closed'
        else:
            item_statuses = [i['status'] for i in items]
            if 'Acknowledged' in item_statuses: overall_status = 'Acknowledged'
            elif all(s == 'Rejected' for s in item_statuses): overall_status = 'Rejected'
            elif all(s == 'Pending' for s in item_statuses): overall_status = 'Pending'
            else: overall_status = 'Pending'

        prs_list.append({
            "id": pr.id,
            "prNumber": pr.pr_number,
            "prDate": str(pr.created_at) if pr.created_at else "",
            "requestedDeliveryDate": str(pr.required_date) if pr.required_date else "",
            "requestedPaymentTerms": "Net 30 days",
            "status": overall_status,
            "items": items
        })

    return {
        "vendorInfo": vendor_info,
        "summary": summary,
        "prs": prs_list
    }

@router.get("/purchase-requisitions/details")
def get_purchase_requisitions_details(
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    vendor_code: str = Query(None, description="SAP Vendor Code (BP No)"),
    vendor_id: int = Query(None, description="Vendor ID"),
    db: Session = Depends(get_db)
):
    result = get_purchase_requisitions(
        x_employee_id=x_employee_id,
        vendor_code=vendor_code,
        vendor_id=vendor_id,
        db=db
    )
    return {"content": result.get("prs", [])}

# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import Optional

class VendorActionBody(BaseModel):
    vendor_id: Optional[int] = None
    vendor_code: Optional[str] = None

# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import List

class CreateRfqBody(BaseModel):
    vendor_ids: List[int]

@router.post("/purchase-requisitions/{pr_id}/create-rfq")
def create_rfq(
    pr_id: str,
    body: CreateRfqBody,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    try:
        # Find the PR ID
        pr_record = db.execute(
            text("SELECT id FROM purchase_requisitions WHERE pr_number = :pr_id"),
            {"pr_id": pr_id}
        ).fetchone()
        
        if not pr_record:
            raise HTTPException(status_code=404, detail="PR not found")
            
        actual_pr_id = pr_record[0]
        
        # Generate RFQ Number
        rfq_number = f"RFQ-{pr_id.replace('PR-', '')}"
        
        # Check if RFQ already exists
        existing = db.execute(
            text("SELECT rfq_id FROM rfq WHERE pr_id = :pr_id"),
            {"pr_id": actual_pr_id}
        ).fetchone()
        
        if not existing:
            db.execute(
                text("""
                INSERT INTO rfq (rfq_number, status, pr_id, created_at)
                VALUES (:rfq_num, 'SENT', :pr_id, NOW())
                """),
                {"rfq_num": rfq_number, "pr_id": actual_pr_id}
            )
            db.commit()
            rfq_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        else:
            rfq_id = existing[0]

        # Insert vendors
        for vendor_id in body.vendor_ids:
            # Check if already assigned
            v_exist = db.execute(
                text("SELECT id FROM rfq_vendors WHERE rfq_id = :rfq AND vendor_id = :vid"),
                {"rfq": rfq_id, "vid": vendor_id}
            ).fetchone()
            if v_exist:
                raise HTTPException(status_code=400, detail="RFQ is already created for one or more selected vendors on this PR.")
                
            if not v_exist:
                db.execute(
                    text("""
                    INSERT INTO rfq_vendors (rfq_id, vendor_id, status, sent_at)
                    VALUES (:rfq, :vid, 'PENDING', NOW())
                    """),
                    {"rfq": rfq_id, "vid": vendor_id}
                )
                
                # Also create PR items vendor mapping for the vendor portal logic
                # This ensures that when the vendor logs in, they see the PR in their list!
                pr_items = db.execute(
                    text("SELECT id FROM purchase_requisition_items WHERE purchase_requisition_id = :pr_id"),
                    {"pr_id": actual_pr_id}
                ).fetchall()
                
                bp_no = db.execute(
                    text("SELECT bp_no FROM vendor_master WHERE vendor_id = :vid"),
                    {"vid": vendor_id}
                ).scalar()
                
                for item in pr_items:
                    db.execute(
                        text("""
                        INSERT INTO purchase_requisition_item_vendors 
                        (purchase_requisition_item_id, vendor_id, bp_no, status, sent_at)
                        VALUES (:item_id, :vid, :bp_no, 'OPEN', NOW())
                        """),
                        {"item_id": item[0], "vid": vendor_id, "bp_no": bp_no}
                    )
                
                # Trigger the RFQ Invitation Email
                background_tasks.add_task(send_rfq_invitation, actual_pr_id, rfq_number, vendor_id)
                    
        db.commit()
        return {"status": "success", "message": "RFQ created successfully", "rfq_number": rfq_number}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/purchase-requisitions/{pr_id}/{action}")
def action_purchase_requisition(
    pr_id: int,
    action: str,
    body: VendorActionBody,
    db: Session = Depends(get_db)
):
    if action not in ["accept", "reject"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    new_status = "ACCEPTED" if action == "accept" else "REJECTED"

    # Find the PR items
    pr_items = db.query(PurchaseRequisitionItem).filter(PurchaseRequisitionItem.purchase_requisition_id == pr_id).all()
    if not pr_items:
        raise HTTPException(status_code=404, detail="PR not found")
        
    item_ids = [item.id for item in pr_items]
    
    vendor_ids_to_check = []
    if body.vendor_id: vendor_ids_to_check.append(body.vendor_id)
    
    if body.vendor_code:
        company_id = db.execute(
            text("SELECT company_id FROM company_details WHERE company_code = :code"),
            {"code": body.vendor_code}
        ).scalar()
        if company_id:
            vendor_ids_to_check.append(company_id)

    # Update item vendors
    query = db.query(PurchaseRequisitionItemVendor).filter(
        PurchaseRequisitionItemVendor.purchase_requisition_item_id.in_(item_ids)
    )
    
    if vendor_ids_to_check and body.vendor_code:
        query = query.filter(
            or_(
                PurchaseRequisitionItemVendor.vendor_id.in_(vendor_ids_to_check),
                PurchaseRequisitionItemVendor.bp_no == body.vendor_code
            )
        )
    elif vendor_ids_to_check:
        query = query.filter(PurchaseRequisitionItemVendor.vendor_id.in_(vendor_ids_to_check))
    elif body.vendor_code:
        query = query.filter(PurchaseRequisitionItemVendor.bp_no == body.vendor_code)

    assignments = query.all()
    if not assignments:
        raise HTTPException(status_code=404, detail="Vendor assignment not found for this PR")
        
    for a in assignments:
        a.status = new_status
        
    db.commit()
    
    return {"status": "success", "message": f"PR {pr_id} {new_status} successfully"}



@router.get("/all-vendors")
def get_all_vendors_for_rfq(db: Session = Depends(get_db)):
    vendors = db.query(VendorMaster).all()
    return [{"vendor_id": v.vendor_id, "bp_no": v.bp_no, "vendor_name": v.name, "email": v.email} for v in vendors]

import random

# pyrefly: ignore [missing-import]
from sqlalchemy import text

@router.get("/selection-list")
def get_vendor_selection_list(pr_number: str = None, material_code: str = None, material_codes: str = None, db: Session = Depends(get_db)):
    try:
        if material_codes or material_code:
            # Use material_codes if provided, otherwise fallback to material_code
            codes = [c.strip() for c in (material_codes or material_code).split(',')]
            
            # Build query checking both material_code (for string codes) and material_id (for numeric IDs)
            where_clauses = []
            params = {}
            for i, code in enumerate(codes):
                if code.isdigit():
                    where_clauses.append(f"material_id = :mc{i}")
                else:
                    where_clauses.append(f"material_code = :mc{i}")
                params[f"mc{i}"] = code
                
            where_str = " OR ".join(where_clauses)
            query_str = f"SELECT vendor_id FROM material WHERE {where_str}"
            
            result = db.execute(text(query_str), params).fetchall() if where_str else []
            valid_vendor_ids = [row[0] for row in result]
            eligible_vendors_db = db.query(VendorMaster).filter(VendorMaster.vendor_id.in_(valid_vendor_ids)).all() if valid_vendor_ids else []
        else:
            eligible_vendors_db = db.query(VendorMaster).all()

        all_vendors_db = db.query(VendorMaster).all()

        def format_vendor(v):
            return {
                "vendor_id": v.vendor_id,
                "bp_no": v.bp_no,
                "vendor_name": v.name,
                "email": v.email,
                "response_rate": "100% in SLA",
                "avg_quote_time": f"{round(random.uniform(0.5, 2.5), 1)} days",
                "price_index": str(round(random.uniform(0.8, 1.2), 2)),
                "compliance": "No data"
            }

        all_vendors = [format_vendor(v) for v in all_vendors_db]
        eligible_vendors = [format_vendor(v) for v in eligible_vendors_db]
                
        return {
            "all_vendors": all_vendors,
            "eligible_vendors": eligible_vendors
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
