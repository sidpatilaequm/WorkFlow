from fastapi import APIRouter, Depends, Header, File, UploadFile, Form, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import PortalASN, PortalASNItem
import json
import os
import shutil
import uuid
from datetime import datetime

router = APIRouter(prefix="/api/vendor/asns", tags=["Vendor ASNs"])

UPLOAD_DIR = "uploads/asns"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def save_upload_file(upload_file: UploadFile, prefix: str) -> str:
    if not upload_file:
        return ""
    ext = os.path.splitext(upload_file.filename)[1]
    filename = f"{prefix}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return file_path

@router.get("")
def list_asns(
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    db: Session = Depends(get_db)
):
    asns = db.query(PortalASN).order_by(PortalASN.created_at.desc()).all()
    
    if not asns:
        return {"data": {"asns": []}}

    result = []
    for asn in asns:
        items = db.query(PortalASNItem).filter(PortalASNItem.asn_id == asn.id).all()
        result.append({
            "id": asn.id,
            "asn_number": asn.asn_number,
            "display_number": asn.asn_number,
            "delivery_note": "",
            "po_reference": asn.po_id,
            "despatch_date": asn.dispatch_date,
            "expected_delivery": asn.expected_delivery,
            "carrier": asn.transporter_code or "—",
            "lr_number": asn.lr_number or "—",
            "packages": asn.packaging or "—",
            "gross_weight": "TBD",
            "eway_bill": asn.eway_bill or "—",
            "eway_validity": asn.ewb_valid_to or "—",
            "invoice_number": asn.invoice_number or "—",
            "status": asn.status,
            "status_slug": asn.status.lower(),
            "status_badge": "success" if asn.status == "IN_TRANSIT" else "warning",
            "items": [{"quantityShipped": float(i.quantity_shipped) if i.quantity_shipped else 0} for i in items]
        })

    return result

@router.post("")
def create_asn(
    asnData: str = Form(...),
    taxInvoiceAttached: UploadFile = File(None),
    ewayBillAttached: UploadFile = File(None),
    packingListAttached: UploadFile = File(None),
    pdirAttached: UploadFile = File(None),
    deviationAttached: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    try:
        data = json.loads(asnData)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON in asnData")
        
    shipment = data.get("shipment_details", {})
    
    tax_invoice_path = save_upload_file(taxInvoiceAttached, "tax_inv")
    eway_bill_path = save_upload_file(ewayBillAttached, "eway_bill")
    packing_list_path = save_upload_file(packingListAttached, "pack_list")
    pdir_path = save_upload_file(pdirAttached, "pdir")
    deviation_path = save_upload_file(deviationAttached, "dev")
    
    new_asn = PortalASN(
        asn_number=f"ASN-{datetime.utcnow().year}-{uuid.uuid4().hex[:6].upper()}",
        po_id=data.get("po_id"),
        vendor_bpno=data.get("vendor_bpno"),
        invoice_number=shipment.get("invoice_number"),
        irn=shipment.get("irn"),
        eway_bill=shipment.get("eway_bill"),
        ewb_valid_to=shipment.get("ewb_valid_to"),
        vehicle_number=shipment.get("vehicle_number"),
        transporter_code=shipment.get("transporter_code"),
        lr_number=shipment.get("lr_number"),
        dispatch_date=shipment.get("dispatch_date"),
        expected_delivery=shipment.get("expected_delivery"),
        packaging=shipment.get("packaging"),
        tax_invoice_file=tax_invoice_path,
        eway_bill_file=eway_bill_path,
        packing_list_file=packing_list_path,
        pdir_file=pdir_path,
        deviation_file=deviation_path,
        status="IN_TRANSIT"
    )
    
    db.add(new_asn)
    db.commit()
    db.refresh(new_asn)
    
    items = data.get("items", [])
    for item in items:
        asn_item = PortalASNItem(
            asn_id=new_asn.id,
            line_number=item.get("line_number"),
            part_number=item.get("part_number"),
            quantity_shipped=item.get("quantity_shipped", 0),
            batch_heat_number=item.get("batch_heat_number")
        )
        db.add(asn_item)
        
    db.commit()
    
    return {"status": "success", "message": "ASN created successfully", "asn_id": new_asn.id, "asn_number": new_asn.asn_number}

from sqlalchemy import text

@router.get("/{asn_id}")
def get_asn(asn_id: str, db: Session = Depends(get_db)):
    extracted_id = None
    if asn_id.isdigit():
        extracted_id = int(asn_id)
    elif asn_id.startswith("ASN-") and asn_id[4:].isdigit():
        extracted_id = int(asn_id[4:])
        
    if extracted_id is None:
        raise HTTPException(status_code=404, detail="ASN not found")
        
    asn = db.execute(text("SELECT * FROM asns WHERE id = :id"), {"id": extracted_id}).mappings().first()
    
    if not asn:
        raise HTTPException(status_code=404, detail="ASN not found")
        
    items_query = """
        SELECT a.*, p.line_number 
        FROM asn_items a 
        LEFT JOIN portal_purchase_order_items p ON a.po_item_id = p.id 
        WHERE a.asn_id = :id
    """
    items = db.execute(text(items_query), {"id": extracted_id}).mappings().all()
    
    po_row = db.execute(text("SELECT po_number FROM portal_purchase_orders WHERE id = :po_id"), {"po_id": asn.get("po_id")}).mappings().first()
    po_reference = po_row["po_number"] if po_row else str(asn.get("po_id"))
    
    return {
        "id": asn["id"],
        "asn_number": f"ASN-{asn['id']}",
        "po_reference": po_reference,
        "despatch_date": str(asn.get("dispatch_date")) if asn.get("dispatch_date") else None,
        "expected_delivery": str(asn.get("expected_delivery")) if asn.get("expected_delivery") else None,
        "is_partial": False,
        "despatch_address": "Ambuja Cements, Survey No 47, Surat-Magdalla Road, Surat, Gujarat 395007",
        "deliver_address": "Plot 47, Peenya Industrial Area, Bangalore 560058",
        "despatch_state": "27 — Gujarat",
        "delivery_state": "29 — Karnataka",
        "transport_mode": "Road",
        "carrier": asn.get("transporter_code") or '—',
        "vehicle_no": asn.get("vehicle_number") or '—',
        "lr_number": asn.get("lr_number") or '—',
        "packages": asn.get("packaging") or '—',
        "gross_weight": "TBD",
        "eway_bill": asn.get("eway_bill") or '—',
        "eway_validity": str(asn.get("ewb_valid_to")) if asn.get("ewb_valid_to") else '—',
        "invoice_number": asn.get("invoice_number") or '—',
        "status": asn.get("status"),
        "status_slug": asn.get("status", "").lower() if asn.get("status") else "",
        "status_badge": "success" if asn.get("status") == "IN_TRANSIT" else "warning",
        "lines": [
            {
                "lineNo": str(i.get("line_number") or i.get("po_item_id") or "10"),
                "description": i.get("part_number"),
                "hsn": "N/A",
                "despatchQty": float(i.get("quantity_shipped") or 0),
                "uom": "EA",
                "batchNo": i.get("batch_heat_number") or '—',
                "sloc": "SL01"
            } for i in items
        ],
        "documents": [
            {"name": "Tax Invoice", "status": "Uploaded" if asn.get("tax_invoice_url") else "Pending", "mandatory": True},
            {"name": "E-Way Bill", "status": "Uploaded" if asn.get("eway_bill_url") else "Pending", "mandatory": True},
            {"name": "Packing List", "status": "Uploaded" if asn.get("packing_list_url") else "Pending", "mandatory": True}
        ]
    }
