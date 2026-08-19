from fastapi import APIRouter, Depends, Header, Query, HTTPException
from sqlalchemy.orm import Session
from collections import defaultdict
from database import get_db
from models import VendorMaster, PortalPurchaseOrder, PortalPurchaseOrderItem, VendorQuotation, VendorQuotationItem
from pydantic import BaseModel
from datetime import datetime

class PoCreateRequest(BaseModel):
    deliveryAddress: str = ""
    shippingInstructions: str = ""
    remarks: str = ""

router = APIRouter(prefix="/api/vendor", tags=["Vendor Portal"])

@router.get("/purchase-orders")
def get_purchase_orders(
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    vendor_code: str = Query(None, description="SAP Vendor Code (BP No)"),
    db: Session = Depends(get_db),
):
    if not vendor_code:
        # Fetch ALL purchase orders for Employee View
        vendor_info = {
            "sapVendorCode": "ALL",
            "sapVendorName": "All Vendors",
            "companyCode": "ALL",
        }
        pos = db.query(PortalPurchaseOrder).order_by(PortalPurchaseOrder.po_date.desc()).all()
    else:
        vendor = db.query(VendorMaster).filter(VendorMaster.bp_no == vendor_code).first()
        if not vendor:
            raise HTTPException(status_code=404, detail=f"Vendor {vendor_code} not found")

        vendor_info = {
            "sapVendorCode": vendor.bp_no,
            "sapVendorName": vendor.name,
            "companyCode": vendor.company_code,
        }

        from sqlalchemy import text
        cd_id = db.execute(text("SELECT company_id FROM company_details WHERE company_code = :bp_no"), {"bp_no": vendor.bp_no}).scalar()
        
        possible_vendor_ids = [vendor.vendor_id]
        if cd_id:
            possible_vendor_ids.append(cd_id)
            
        # Quotations might be saved with vendor_id=1 due to frontend mock, so we include it
        possible_vendor_ids.append(1)

        pos = db.query(PortalPurchaseOrder).filter(
            PortalPurchaseOrder.vendor_id.in_(possible_vendor_ids)
        ).order_by(PortalPurchaseOrder.po_date.desc()).all()

    if not pos:
        return {
            "vendorInfo": vendor_info,
            "summary": {"totalPOs": 0, "poIssued": 0, "poDelivered": 0, "poInProcess": 0},
            "orders": [],
        }

    total_pos = len(pos)
    po_issued = sum(1 for p in pos if p.status in ["CREATED", "ISSUED", "OPEN", "RELEASED"])
    po_delivered = sum(1 for p in pos if p.status in ["DELIVERED", "COMPLETED", "CLOSED", "GR DONE"])
    po_in_process = total_pos - po_delivered

    summary = {
        "totalPOs": total_pos,
        "poIssued": po_issued,
        "poDelivered": po_delivered,
        "poInProcess": po_in_process,
    }

    # confirmed_delivery_date lives on the PO header
    orders = [
        {
            "poId": p.id,
            "poNumber": p.po_number,
            "poDate": str(p.po_date) if p.po_date else None,
            "grandTotal": float(p.grand_total) if p.grand_total else 0.0,
            "poStatus": "RELEASED" if p.status in ["CREATED", "ISSUED", "OPEN"] else p.status,
            "requestedDeliveryDate": str(p.requested_delivery_date) if p.requested_delivery_date else None,
            "paymentTerms": {"name": "Net 30 Days"}
        }
        for p in pos
    ]

    return {"vendorInfo": vendor_info, "summary": summary, "orders": orders}

@router.get("/purchase-orders/{po_id}")
def get_purchase_order_details(
    po_id: int,
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    db: Session = Depends(get_db)
):
    items = db.query(PortalPurchaseOrderItem).filter(
        PortalPurchaseOrderItem.po_id == po_id
    ).all()

    items_list = []
    for item in items:
        items_list.append({
            "lineNumber": item.line_number,
            "materialNumber": item.material_number,
            "materialDescription": item.material_description or "",
            "quantity": float(item.quantity) if item.quantity else 0.0,
            "uom": item.uom or "EA",
            "unitPrice": float(item.unit_price) if item.unit_price else 0.0,
            "netValue": float(item.net_value) if item.net_value else 0.0,
            "totalValue": float(item.total_value) if item.total_value else 0.0,
            "confirmedDeliveryDate": None # Will map from PO in frontend if needed
        })
        
    return {"items": items_list}

@router.post("/purchase-orders/from-awarded-quotation/{qtn_id}")
def create_po_from_quotation(
    qtn_id: str,
    request: PoCreateRequest,
    db: Session = Depends(get_db)
):
    import re
    numeric_qtn_id = int(re.sub(r'\D', '', qtn_id)) if re.sub(r'\D', '', qtn_id) else 0
    
    # Find Quotation
    from sqlalchemy import or_
    qtn = db.query(VendorQuotation).filter(
        or_(
            VendorQuotation.quotation_id == numeric_qtn_id,
            VendorQuotation.quotation_number == qtn_id
        )
    ).first()
    if not qtn:
        raise HTTPException(status_code=404, detail="Quotation not found")
        
    # Mark as awarded if it isn't already
    qtn.status = "AWARDED"
    db.add(qtn)
    
    # Calculate totals from quotation items
    q_items = db.query(VendorQuotationItem).filter(VendorQuotationItem.quotation_id == qtn.quotation_id).all()
    grand_total = sum(
        (float(i.quoted_qty or i.pr_qty or 0) * float(i.unit_price or 0)) 
        * (1 + float(i.gst_percent or 0) / 100) 
        + float(i.freight_amount or 0) 
        for i in q_items
    )
    
    # Resolve vendor_id to company_id if available (since POs use company_id)
    from sqlalchemy import text
    cd_id = db.execute(text("SELECT company_id FROM company_details WHERE company_code = :bp_no"), {"bp_no": qtn.bp_no}).scalar()
    po_vendor_id = cd_id if cd_id else qtn.vendor_id

    # Create PO
    po = PortalPurchaseOrder(
        pr_id=qtn.pr_id,
        quotation_id=qtn.quotation_id,
        vendor_id=po_vendor_id,
        po_number=f"PO-{datetime.utcnow().year}-{(qtn.quotation_id * 13) % 9999 + 1000}",
        po_date=datetime.utcnow(),
        status="RELEASED",
        grand_total=grand_total,
        currency=qtn.currency or "INR"
    )
    db.add(po)
    db.commit()
    db.refresh(po)
    
    # Create PO items
    for idx, i in enumerate(q_items):
        qty = float(i.quoted_qty or i.pr_qty or 0)
        price = float(i.unit_price or 0)
        po_item = PortalPurchaseOrderItem(
            po_id=po.id,
            line_number=str((idx + 1) * 10),
            material_number=i.item_code or f"MAT-{i.pr_line_id}",
            material_description=i.description,
            quantity=qty,
            uom=i.uom,
            unit_price=price,
            net_value=qty * price,
            total_value=qty * price * (1 + float(i.gst_percent or 0) / 100)
        )
        db.add(po_item)
        
    db.commit()
    
    return {"message": "Purchase Order created successfully", "po_id": po.id}

@router.post("/purchase-orders/{po_id}/{action}")
def update_purchase_order_status(
    po_id: int,
    action: str,
    db: Session = Depends(get_db)
):
    po = db.query(PortalPurchaseOrder).filter(PortalPurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase Order not found")
        
    if action == "acknowledge":
        po.status = "ACKNOWLEDGED"
    elif action == "reject":
        po.status = "REJECTED"
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
        
    db.add(po)
    db.commit()
    return {"status": "success", "message": f"PO {action}d successfully", "new_status": po.status}
