from fastapi import APIRouter, Depends, Header, Query, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import text
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional
from database import get_db
from models import (
    PurchaseRequisition,
    PurchaseRequisitionItem,
    VendorQuotation,
    VendorQuotationItem,
    PortalPurchaseOrder,
)
from models import Employee, User

def resolve_employee(db, emp_id: str):
    user = db.query(User).filter(User.id == emp_id).first()
    if user:
        return user.id, {
            "employeeName": user.name or user.username,
            "employeeCode": getattr(user, "employee_code", f"E{user.id}"),
            "title": getattr(user, "role", "Employee")
        }
    return emp_id, {"employeeName": "Unknown", "employeeCode": emp_id, "title": ""}


router = APIRouter(prefix="/api/employee", tags=["Employee Portal"])

DEFAULT_WEIGHTS = {"compliance": 20, "cost": 30, "quality": 20, "terms": 15, "delivery": 15}

def safe_rows(db, sql, params=None):
    try:
        return db.execute(text(sql), params or {}).mappings().all()
    except Exception:
        return []

def resolve_vendor_names(db, vendor_ids):
    out = {}
    if not vendor_ids:
        return out
    for r in safe_rows(db, "SELECT vendor_id, bp_no, name FROM vendor_master WHERE vendor_id IN :v",
                       {"v": tuple(vendor_ids)}):
        out[r["vendor_id"]] = {"code": r["bp_no"], "name": r["name"]}
    missing = [v for v in vendor_ids if v not in out]
    if missing:
        for col_pair in [("company_id", "company_name"), ("company_id", "name")]:
            if not missing:
                break
            rows = safe_rows(db, f"SELECT {col_pair[0]} AS id, {col_pair[1]} AS name FROM company_details WHERE {col_pair[0]} IN :v",
                             {"v": tuple(missing)})
            for r in rows:
                out[r["id"]] = {"code": f"C{r['id']}", "name": r["name"] or f"Vendor #{r['id']}"}
            missing = [v for v in missing if v not in out]
    for v in missing:
        out[v] = {"code": f"V{v}", "name": f"Vendor #{v}"}
    return out

def parse_weights(weights_param):
    if not weights_param:
        return dict(DEFAULT_WEIGHTS)
    try:
        parts = [float(x) for x in weights_param.split(",")]
        if len(parts) == 5 and abs(sum(parts) - 100) < 0.01:
            keys = ["compliance", "cost", "quality", "terms", "delivery"]
            return dict(zip(keys, parts))
    except Exception:
        pass
    return dict(DEFAULT_WEIGHTS)

@router.get("/quote-comparison")
def quote_comparison(
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    employee_id: str = Query(None, description="user_id, employee_code, or email"),
    pr_id: Optional[int] = Query(None),
    pr_number: Optional[str] = Query(None),
    weights: Optional[str] = Query(None, description="5 comma-separated weights summing to 100: compliance,cost,quality,terms,delivery"),
    db: Session = Depends(get_db),
):
    uid, employee_info = resolve_employee(db, employee_id or x_employee_id)
    W = parse_weights(weights)

    counts = defaultdict(int)
    for (pid,) in (db.query(VendorQuotation.pr_id)
                   .join(PurchaseRequisition, PurchaseRequisition.id == VendorQuotation.pr_id).all()):
        counts[pid] += 1

    pr_options = []
    if counts:
        for pr in db.query(PurchaseRequisition).filter(PurchaseRequisition.id.in_(list(counts))).all():
            pr_options.append({"prId": pr.id, "prNumber": pr.pr_number, "quoteCount": counts[pr.id]})
        pr_options.sort(key=lambda x: (-x["quoteCount"], x["prNumber"]))

    if not pr_options:
        return {"employeeInfo": employee_info, "prOptions": [], "weights": W, "comparison": None}

    resolved_pr_id = pr_id
    if pr_number:
        # Find pr_id corresponding to pr_number from pr_options
        for opt in pr_options:
            if opt["prNumber"] == pr_number:
                resolved_pr_id = opt["prId"]
                break

    selected = resolved_pr_id if resolved_pr_id in counts else pr_options[0]["prId"]
    pr = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == selected).first()

    invited = db.execute(text("""
        SELECT COUNT(DISTINCT priv.vendor_id) FROM purchase_requisition_item_vendors priv
        JOIN purchase_requisition_items pri ON pri.id = priv.purchase_requisition_item_id
        WHERE pri.purchase_requisition_id = :p
    """), {"p": selected}).scalar() or 0

    quotes = db.query(VendorQuotation).filter(VendorQuotation.pr_id == selected).all()
    vendor_ids = list({vq.vendor_id for vq in quotes})
    vendor_names = resolve_vendor_names(db, vendor_ids)
    quote_ids = [vq.quotation_id for vq in quotes]

    items_by_quote = defaultdict(list)
    if quote_ids:
        for it in db.query(VendorQuotationItem).filter(VendorQuotationItem.quotation_id.in_(quote_ids)).all():
            items_by_quote[it.quotation_id].append(it)

    pr_items = db.query(PurchaseRequisitionItem).filter(
        PurchaseRequisitionItem.purchase_requisition_id == selected).all()
    total_pr_qty = sum(float(i.quantity or 0) for i in pr_items)
    item_codes = [i.sku for i in pr_items if i.sku]

    comp_by_vendor = defaultdict(list)
    for r in safe_rows(db, "SELECT vendor_id, cert_name, status FROM vendor_compliance WHERE vendor_id IN :v",
                       {"v": tuple(vendor_ids) if vendor_ids else (0,)}):
        comp_by_vendor[r["vendor_id"]].append(r)

    qual_by_vendor = {r["vendor_id"]: float(r["reject_rate_percent"]) for r in
                      safe_rows(db, "SELECT vendor_id, reject_rate_percent FROM vendor_quality WHERE vendor_id IN :v",
                                {"v": tuple(vendor_ids) if vendor_ids else (0,)})}

    native_terms = {r["quotation_id"]: r for r in
                    safe_rows(db, "SELECT quotation_id, advance_required_percent, payment_terms_id, lead_time_days, quoted_delivery_date FROM vendor_quotations WHERE quotation_id IN :q",
                              {"q": tuple(quote_ids) if quote_ids else (0,)})}
    seeded_terms = {r["quotation_id"]: r for r in
                    safe_rows(db, "SELECT quotation_id, credit_days, advance_required_percent FROM vendor_quotation_terms WHERE quotation_id IN :q",
                              {"q": tuple(quote_ids) if quote_ids else (0,)})}

    otif_by_vendor = {}
    if vendor_ids:
        agg = defaultdict(lambda: [0, 0])
        for p in db.query(PortalPurchaseOrder).filter(
                PortalPurchaseOrder.vendor_id.in_(vendor_ids),
                PortalPurchaseOrder.status.in_(["DELIVERED", "COMPLETED", "CLOSED", "GR DONE"]),
                PortalPurchaseOrder.requested_delivery_date.isnot(None),
                PortalPurchaseOrder.confirmed_delivery_date.isnot(None)).all():
            agg[p.vendor_id][1] += 1
            if p.confirmed_delivery_date <= p.requested_delivery_date:
                agg[p.vendor_id][0] += 1
        for vid_, (ok, tot) in agg.items():
            otif_by_vendor[vid_] = ok / tot if tot else None

    min_total = min((float(vq.grand_total_amount or 0) for vq in quotes if vq.grand_total_amount), default=0)

    vendors_out = []
    for vq in quotes:
        vn = vendor_names.get(vq.vendor_id, {"code": f"V{vq.vendor_id}", "name": f"Vendor #{vq.vendor_id}"})
        flags, award_blocked = [], False

        certs = comp_by_vendor.get(vq.vendor_id, [])
        if certs:
            missing = [c for c in certs if c["status"] in ("MISSING", "EXPIRED")]
            expiring = [c for c in certs if c["status"] == "EXPIRING"]
            comp_score = max(0, W["compliance"] - 0.55 * W["compliance"] * len(missing) - 0.3 * W["compliance"] * len(expiring))
            comp_score = round(comp_score, 1)
            if missing:
                award_blocked = True
                flags.append(f"{missing[0]['cert_name']} missing — blocks award until filed")
            if expiring:
                flags.append(f"{expiring[0]['cert_name']} expiring soon — renew before delivery window")
            comp_detail = "all valid" if not missing and not expiring else \
                          (f"{missing[0]['cert_name']} missing" if missing else f"{expiring[0]['cert_name']} expiring")
        else:
            comp_score, comp_detail = 0, "no data"

        total = float(vq.grand_total_amount or 0)
        cost_score = round(W["cost"] * min_total / total, 1) if total and min_total else 0
        cost_detail = f"₹{total:,.0f}"

        reject = qual_by_vendor.get(vq.vendor_id)
        if reject is not None:
            qual_score = round(max(0.0, W["quality"] * (1 - reject / 10)), 1)
            qual_detail = f"{reject}% reject"
        else:
            qual_score, qual_detail = 0, "no data"

        nt, st = native_terms.get(vq.quotation_id), seeded_terms.get(vq.quotation_id)
        advance = float((nt and nt["advance_required_percent"]) or (st and st["advance_required_percent"]) or 0)
        credit = int((st and st["credit_days"]) or 0)
        if nt or st:
            term_score = round(W["terms"] * min(credit, 60) / 60, 1)
            if advance > 0:
                term_score = max(0.0, term_score - round(advance / 10, 1))
                term_detail = f"{advance:.0f}% advance" + (f", {credit}d credit" if credit else "")
            else:
                term_detail = f"{credit}d credit" if credit else "standard terms"
        else:
            term_score, term_detail = 0, "no data"

        otif = otif_by_vendor.get(vq.vendor_id)
        if otif is not None:
            del_score = round(W["delivery"] * otif, 1)
            del_detail = f"{otif*100:.0f}% OTIF"
        else:
            q_dates = [it.delivery_date for it in items_by_quote.get(vq.quotation_id, []) if getattr(it, 'delivery_date', None)]
            if nt and nt["quoted_delivery_date"]:
                q_dates.append(nt["quoted_delivery_date"])
            if q_dates and pr.required_date:
                latest = max(q_dates)
                if latest <= pr.required_date:
                    del_score, del_detail = round(0.75 * W["delivery"], 1), "on time (quoted)"
                elif latest <= pr.required_date + timedelta(days=7):
                    del_score, del_detail = round(0.55 * W["delivery"], 1), "≤7d late (quoted)"
                else:
                    del_score, del_detail = round(0.35 * W["delivery"], 1), "late (quoted)"
                    flags.append("Quoted delivery later than PR required date")
            else:
                del_score, del_detail = 0, "no data"

        quoted_qty = sum(float(it.quoted_qty or 0) for it in items_by_quote.get(vq.quotation_id, []))
        if total_pr_qty and quoted_qty < total_pr_qty:
            flags.append(f"Partial quote — covers {quoted_qty:.0f}/{total_pr_qty:.0f} qty")

        vendors_out.append({
            "quotationId": vq.quotation_id,
            "vendorCode": vn["code"],
            "vendorName": vn["name"],
            "quoteNo": vq.quotation_number,
            "quoteDate": str(vq.quotation_date) if vq.quotation_date else None,
            "quoteStatus": vq.status,
            "grandTotal": total,
            "scores": {
                "compliance": {"score": comp_score, "detail": comp_detail},
                "cost": {"score": cost_score, "detail": cost_detail},
                "quality": {"score": qual_score, "detail": qual_detail},
                "terms": {"score": term_score, "detail": term_detail},
                "delivery": {"score": del_score, "detail": del_detail},
            },
            "weightedScore": round(comp_score + cost_score + qual_score + term_score + del_score, 1),
            "awardBlocked": award_blocked,
            "flags": flags,
        })

    vendors_out.sort(key=lambda v: -v["weightedScore"])
    for i, v in enumerate(vendors_out):
        v["rank"] = i + 1

    already_awarded = next((v for v in vendors_out if v["quoteStatus"] == "AWARDED"), None)
    recommendation = None
    eligible = [v for v in vendors_out if not v["awardBlocked"]]
    if eligible:
        top = eligible[0]
        parts = [f"Highest eligible weighted score ({top['weightedScore']}/100)"]
        if top["scores"]["delivery"]["detail"] != "no data":
            parts.append(f"delivery: {top['scores']['delivery']['detail']}")
        parts.append(f"landed cost {top['scores']['cost']['detail']}")
        text_rec = f"{top['vendorName']} — " + ", ".join(parts) + "."
        blocked = [v for v in vendors_out if v["awardBlocked"]]
        if blocked and blocked[0]["rank"] < top["rank"]:
            text_rec += f" {blocked[0]['vendorName']} ranks higher on raw score but is award-blocked on compliance."
        recommendation = {"vendorName": top["vendorName"], "vendorCode": top["vendorCode"],
                          "quotationId": top["quotationId"], "quoteNo": top["quoteNo"], "text": text_rec}

    criteria = [
        {"key": "compliance", "label": "Compliance", "weight": W["compliance"], "source": "Portal (vendor_compliance)"},
        {"key": "cost", "label": "Product / landed cost", "weight": W["cost"], "source": "Quote (grand total)"},
        {"key": "quality", "label": "Quality", "weight": W["quality"], "source": "GRN history (vendor_quality)"},
        {"key": "terms", "label": "Services / terms", "weight": W["terms"], "source": "Quote terms"},
        {"key": "delivery", "label": "Delivery adherence", "weight": W["delivery"], "source": "PO history (OTIF)"},
    ]

    return {
        "employeeInfo": employee_info,
        "prOptions": pr_options,
        "weights": W,
        "comparison": {
            "prId": pr.id,
            "prNumber": pr.pr_number,
            "prDate": str(pr.created_at.date()) if pr.created_at else None,
            "requiredDate": str(pr.required_date) if pr.required_date else None,
            "itemCodes": item_codes,
            "invited": invited,
            "received": len(vendors_out),
            "criteria": criteria,
            "vendors": vendors_out,
            "recommendation": recommendation,
            "alreadyAwarded": {"vendorName": already_awarded["vendorName"], "quoteNo": already_awarded["quoteNo"]} if already_awarded else None,
        },
    }

@router.post("/award-quote")
def award_quote(
    x_employee_id: str = Header(None, alias="X-Employee-Id"),
    payload: dict = Body(..., example={"employee_id": "1", "quotation_id": 1}),
    db: Session = Depends(get_db),
):
    uid, _ = resolve_employee(db, str(payload.get("employee_id") or x_employee_id or ""))
    qid = payload.get("quotation_id")
    if not qid:
        raise HTTPException(status_code=400, detail="quotation_id is required")

    vq = db.query(VendorQuotation).filter(VendorQuotation.quotation_id == qid).first()
    if not vq:
        raise HTTPException(status_code=404, detail="Quotation not found")

    pr = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == vq.pr_id).first()
    if not pr or pr.requested_by != uid:
        raise HTTPException(status_code=403, detail="You can only award quotes on your own PRs")
    if vq.status == "AWARDED":
        raise HTTPException(status_code=422, detail="This quotation is already awarded")

    missing = safe_rows(db, "SELECT cert_name FROM vendor_compliance WHERE vendor_id=:v AND status IN ('MISSING','EXPIRED')",
                        {"v": vq.vendor_id})
    if missing:
        raise HTTPException(status_code=422,
                            detail=f"Vendor is award-blocked — {missing[0]['cert_name']} not on file")

    db.execute(text("UPDATE vendor_quotations SET status='AWARDED', modified_date=NOW() WHERE quotation_id=:q"), {"q": qid})
    db.execute(text("""UPDATE vendor_quotations SET status='REJECTED', modified_date=NOW()
                       WHERE pr_id=:p AND quotation_id<>:q AND status='SUBMITTED'"""),
               {"p": vq.pr_id, "q": qid})
    
    # We must commit the transaction here so that the Java API sees the 'AWARDED' status
    db.commit()

    po_id = None
    try:
        import requests
        # The Java API expects a POST request to create the PO from the awarded quotation
        # It also updates the PR status to PO_CREATED
        java_api_url = f"http://localhost:8080/api/purchase-orders/from-awarded-quotation/{qid}"
        resp = requests.post(java_api_url, json={})
        resp.raise_for_status()
        po_id = resp.json().get("poId")
    except Exception as e:
        import logging
        logging.error(f"Failed to create PO via Java API: {e}")
        # We continue because the quotation was successfully awarded, even if PO creation failed/delayed.

    # Fetch updated data to return
    vq_awarded = db.query(VendorQuotation).filter(VendorQuotation.quotation_id == qid).first()
    
    return {
        "status": "success",
        "message": "Quote awarded successfully",
        "awardedQuote": {
            "quotationId": vq_awarded.quotation_id,
            "vendorId": vq_awarded.vendor_id,
            "poId": po_id,
            "status": vq_awarded.status
        }
    }
