from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import date, datetime
import uuid

from database import get_db
from models import VendorMaster, VendorAddress, SupplierRegistration

router = APIRouter(prefix="/api/vendors", tags=["Vendors"])

class VendorAddressCreate(BaseModel):
    address_id: Optional[str] = None
    address_type: Optional[str] = "HEAD_OFFICE"
    city_name: Optional[str] = None
    country: Optional[str] = "India"
    postal_code: Optional[str] = None
    street_and_house_number: Optional[str] = None
    street_name_1: Optional[str] = None
    is_default: bool = False

class VendorMasterCreate(BaseModel):
    bp_no: str
    addresses: List[VendorAddressCreate] = []

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_vendor_master(vendor_data: VendorMasterCreate, db: Session = Depends(get_db)):
    # Check if BP no already exists
    existing = db.query(VendorMaster).filter(VendorMaster.bp_no == vendor_data.bp_no).first()
    if existing:
        raise HTTPException(status_code=400, detail="Vendor with this BP number already exists")

    # Name/GST/PAN/company code no longer live on this table — a vendor created here has no
    # SupplierRegistration to link to (that's the Become-a-Supplier flow's own provisioning,
    # owned by backend_java), so it's just a bare bp_no record until/unless something links it.
    new_vendor = VendorMaster(
        bp_no=vendor_data.bp_no,
        sys_created_date=datetime.utcnow()
    )

    db.add(new_vendor)
    db.commit()
    db.refresh(new_vendor)

    # Create associated addresses
    for addr in vendor_data.addresses:
        # Generate a unique address_id if not provided
        addr_id = addr.address_id or f"ADDR-{uuid.uuid4().hex[:8].upper()}"

        new_address = VendorAddress(
            address_id=addr_id,
            address_type=addr.address_type,
            city_name=addr.city_name,
            country=addr.country,
            postal_code=addr.postal_code,
            street_and_house_number=addr.street_and_house_number,
            street_name_1=addr.street_name_1,
            is_default=addr.is_default,
            vendor_id=new_vendor.vendor_id
        )
        db.add(new_address)

    if vendor_data.addresses:
        db.commit()

    return {"message": "Vendor created successfully", "vendor_id": new_vendor.vendor_id}

@router.get("/all")
def get_all_vendors(db: Session = Depends(get_db)):
    rows = db.query(VendorMaster, SupplierRegistration).outerjoin(
        SupplierRegistration, VendorMaster.supplier_registration_id == SupplierRegistration.id
    ).all()
    return [
        {
            "vendor_id": v.vendor_id,
            "bp_no": v.bp_no,
            "name": reg.vendor_name if reg else None,
            "email": reg.email if reg else None,
        }
        for v, reg in rows
    ]

@router.get("/{vendor_id}")
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    row = db.query(VendorMaster, SupplierRegistration).outerjoin(
        SupplierRegistration, VendorMaster.supplier_registration_id == SupplierRegistration.id
    ).filter(VendorMaster.vendor_id == vendor_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Vendor not found")
    vendor, reg = row

    return {
        "vendor_id": vendor.vendor_id,
        "bp_no": vendor.bp_no,
        "name": reg.vendor_name if reg else None,
        "gst_number": reg.gst_number if reg else None,
        "pan": reg.pan_number if reg else None,
    }
