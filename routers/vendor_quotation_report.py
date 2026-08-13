from fastapi import APIRouter, Depends, Header, Query, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import VendorMaster, VendorQuotation, VendorQuotationItem, PurchaseRequisition
from sqlalchemy import or_

router = APIRouter(prefix="/api/vendor", tags=["Vendor Portal (Mock)"])

@router.get("/quotation-report")
def get_quotation_report(
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    vendor_code: str = Query(None, description="SAP Vendor Code (BP No)"),
    vendor_id: int = Query(None, description="Vendor ID"),
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

    # 2. Fetch Quotations for this vendor
    fetch_vendor_id = vendor.vendor_id
    quotations_db = db.query(VendorQuotation).filter(
        or_(
            VendorQuotation.vendor_id.in_([fetch_vendor_id, 1]),
            VendorQuotation.bp_no == vendor.bp_no
        )
    ).all()

    # 3. Calculate Summary Metrics (Mapped to REAL DB statuses)
    requested = len(quotations_db)
    acknowledged = sum(1 for q in quotations_db if q.status in ['SUBMITTED', 'AWARDED'])
    raised = sum(1 for q in quotations_db if q.status in ['SUBMITTED', 'AWARDED'])
    won = sum(1 for q in quotations_db if q.status == 'AWARDED')
    rejected = sum(1 for q in quotations_db if q.status == 'REJECTED')

    # Apply Formulas
    sla = round((acknowledged / requested) * 100, 1) if requested > 0 else 0.0
    win = round((won / raised) * 100, 1) if raised > 0 else 0.0
    not_responded = round(((requested - acknowledged) / requested) * 100, 1) if requested > 0 else 0.0

    summary = {
        "requested": requested,
        "acknowledged": acknowledged,
        "raised": raised,
        "won": won,
        "rejected": rejected,
        "sla": sla,
        "win": win,
        "notResponded": not_responded
    }

    # 4. Build Quotations List
    quotations_list = []
    for q in quotations_db:
        # Fetch items for this quotation
        items_db = db.query(VendorQuotationItem).filter(
            VendorQuotationItem.quotation_id == q.quotation_id
        ).all()
        
        items = []
        for i in items_db:
            items.append({
                "itemCode": i.item_code or "",
                "itemQuantity": float(i.pr_qty) if i.pr_qty else 0.0,
                "itemPrice": float(i.unit_price) if i.unit_price else 0.0,
                "itemFreight": float(i.freight_amount) if i.freight_amount else 0.0,
                "itemDiscount": 0.0,
                "itemDeliveryDate": str(i.delivery_date) if i.delivery_date else "",
                "itemPaymentTerms": "Net 30 days"
            })

        # Compliance placeholder
        compliance = [
            {"category": "Price", "standard": "Market Standard", "compliance": "Compliant", "status": "Pass"},
            {"category": "Delivery", "standard": "As per PR", "compliance": "Met", "status": "Pass"}
        ]

        # Fetch the actual PR Number from DB
        pr = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == q.pr_id).first()
        actual_pr_number = pr.pr_number if pr else f"PR-{q.pr_id}"

        quotations_list.append({
            "quoteNo": q.quotation_number,
            "quoteDate": str(q.quotation_date) if q.quotation_date else "",
            "prNumber": actual_pr_number,
            "prDate": str(q.created_date) if q.created_date else "",
            "quoteStatus": "WON" if q.status == "AWARDED" else (q.status or "Pending"),
            "companyCode": vendor.company_code,
            "items": items,
            "compliance": compliance
        })

    return {
        "vendorInfo": vendor_info,
        "summary": summary,
        "quotations": quotations_list
    }

@router.get("/quotations")
def get_vendor_quotations(
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    vendor_code: str = Query(None, description="SAP Vendor Code (BP No)"),
    vendor_id: int = Query(None, description="Vendor ID"),
    db: Session = Depends(get_db)
):
    # Reuse get_quotation_report to fetch the data
    report_data = get_quotation_report(
        x_employee_id=x_employee_id,
        vendor_code=vendor_code,
        vendor_id=vendor_id,
        db=db
    )
    return report_data.get("quotations", [])

from fastapi import Request
import datetime

@router.post("/quotations")
async def create_quotation(
    request: Request,
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    db: Session = Depends(get_db)
):
    payload = await request.json()
    
    # Extract data
    pr_id = payload.get("pr_id", 0)
    header = payload.get("quotation_header", {})
    quotation_number = header.get("quotation_number", f"QTN-{datetime.datetime.now().timestamp()}")
    quotation_date = header.get("quotation_date", datetime.date.today().isoformat())
    currency = header.get("currency", "INR")
    valid_until = header.get("valid_until")
    
    vendor_code = payload.get("vendor_code")
    if not vendor_code:
        vendor_code = "BP-MARK-01"
        
    from sqlalchemy import text
    company_id_result = db.execute(
        text("SELECT company_id FROM company_details WHERE company_code = :bp_no LIMIT 1"),
        {"bp_no": vendor_code}
    ).scalar()
    
    vendor_id = company_id_result if company_id_result else 2

    # Insert VendorQuotation
    new_quote = VendorQuotation(
        pr_id=pr_id,
        vendor_id=vendor_id,
        bp_no=payload.get("vendor_code"),
        quotation_number=quotation_number,
        quotation_date=quotation_date,
        status="SUBMITTED",
        currency=currency,
        subtotal_amount=0,
        gst_total_amount=0,
        freight_amount=0,
        grand_total_amount=0,
        valid_until=valid_until,
        created_date=datetime.datetime.now(),
        modified_date=datetime.datetime.now()
    )
    db.add(new_quote)
    db.commit()
    db.refresh(new_quote)

    # Insert line items
    line_items = payload.get("line_items", [])
    subtotal = 0
    gst_total = 0
    freight_total = payload.get("freight_details", {}).get("freight_amount", 0)

    for item in line_items:
        qty = float(item.get("quoted_qty", 0))
        price = float(item.get("unit_price", 0))
        gst_pct = float(item.get("gst_percent", 0))
        
        line_total = qty * price
        item_gst = line_total * (gst_pct / 100)
        
        subtotal += line_total
        gst_total += item_gst

        new_item = VendorQuotationItem(
            quotation_id=new_quote.quotation_id,
            pr_line_id=item.get("pr_line_id", 0),
            item_code=item.get("item_code", ""),
            description=item.get("description", ""),
            pr_qty=float(item.get("pr_qty", 0)),
            quoted_qty=qty,
            uom=item.get("uom", "EA"),
            unit_price=price,
            gst_percent=gst_pct,
            gst_amount=item_gst,
            freight_amount=0,
            line_total=line_total,
            delivery_date=item.get("delivery_date")
        )
        db.add(new_item)

    # Update totals
    new_quote.subtotal_amount = subtotal
    new_quote.gst_total_amount = gst_total
    new_quote.freight_amount = freight_total
    new_quote.grand_total_amount = subtotal + gst_total + float(freight_total)
    
    db.commit()

    return {"status": "success", "message": "Quotation submitted successfully", "quotation_id": new_quote.quotation_id}

@router.get("/all")
def get_all_quotations(db: Session = Depends(get_db)):
    quotations_db = db.query(VendorQuotation).all()
    quotations_list = []
    for q in quotations_db:
        pr = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == q.pr_id).first()
        actual_pr_number = pr.pr_number if pr else f"PR-{q.pr_id}"
        
        vendor = None
        if q.bp_no:
            vendor = db.query(VendorMaster).filter(VendorMaster.bp_no == q.bp_no).first()
        if not vendor:
            vendor = db.query(VendorMaster).filter(VendorMaster.vendor_id == q.vendor_id).first()
            
        vendor_name = vendor.name if vendor else f"Vendor-{q.vendor_id}"
        
        # Calculate totals and build line_items
        items_db = db.query(VendorQuotationItem).filter(VendorQuotationItem.quotation_id == q.quotation_id).all()
        grand_total = 0.0
        line_items_list = []
        for i in items_db:
            price = float(i.unit_price) if i.unit_price else 0.0
            qty = float(i.quoted_qty) if i.quoted_qty else (float(i.pr_qty) if i.pr_qty else 0.0)
            gst = float(i.gst_percent) if i.gst_percent else 0.0
            freight = float(i.freight_amount) if i.freight_amount else 0.0
            sub = price * qty
            line_total = sub + (sub * gst / 100) + freight
            grand_total += line_total
            
            line_items_list.append({
                "item_code": i.item_code,
                "description": i.description,
                "quoted_qty": qty,
                "uom": i.uom,
                "unit_price": price,
                "gst_percent": gst,
                "freight_amount": freight,
                "line_total": line_total
            })
            
        quotations_list.append({
            "id": q.quotation_id,
            "quoteNo": q.quotation_number,
            "quoteDate": str(q.quotation_date) if q.quotation_date else "",
            "prNumber": actual_pr_number,
            "prDate": str(q.created_date) if q.created_date else "",
            "quoteStatus": q.status or "Pending",
            "grandTotal": grand_total,
            "validUntil": str(q.valid_until) if q.valid_until else "",
            "description": f"Quotation against {actual_pr_number}",
            "vendorName": vendor_name,
            "line_items": line_items_list
        })
    return quotations_list
