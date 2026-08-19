import logging
from sqlalchemy import text
from .email_builder import build_email_html
import os

logger = logging.getLogger(__name__)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

def generate_pr_approval_email(db, pr_id: int, approve_url: str, reject_url: str, frontend_request_url: str) -> tuple:
    """
    Fetches PR details from the Java schema and generates a rich HTML email matching the user's template.
    Returns (subject, html_body, text_body).
    """
    # 1. Fetch PR details
    pr_query = text("""
        SELECT pr.pr_number, pr.created_at, pr.required_date, pr.total_amount, pr.remarks, 
               pr.location_id, u.first_name, u.last_name, u.email, u.dept_code,
               loc.location_name
        FROM purchase_requisitions pr
        LEFT JOIN user_details u ON pr.requested_by = u.user_id
        LEFT JOIN location loc ON pr.location_id = loc.location_id
        WHERE pr.id = :pr_id
    """)
    pr_row = db.execute(pr_query, {"pr_id": pr_id}).fetchone()
    
    if not pr_row:
        logger.warning(f"generate_pr_approval_email: PR {pr_id} not found in DB.")
        # Fallback to standard generic email handled by caller
        return None, None, None

    pr_number, created_at, required_date, total_amount, remarks, loc_id, f_name, l_name, req_email, dept_code, loc_name = pr_row
    requester_name = f"{f_name or ''} {l_name or ''}".strip() or "System"
    
    # 2. Fetch Line Items
    items_query = text("""
        SELECT i.sku, i.quantity, i.uom, i.estimated_price, i.total_price, m.material_description
        FROM purchase_requisition_items i
        LEFT JOIN material_master m ON i.material_id = m.id
        WHERE i.purchase_requisition_id = :pr_id
    """)
    items_rows = db.execute(items_query, {"pr_id": pr_id}).fetchall()
    
    # 3. Format the Handlebars-like loops in raw HTML
    line_items_html = ""
    for idx, item in enumerate(items_rows, start=1):
        sku, qty, uom, price, total_price, desc = item
        line_items_html += f"""
        <div style="margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid #e0e0e0;">
            <p style="margin: 0 0 4px; font-weight: bold;">{idx}. {desc or sku}</p>
            <p style="margin: 0; color: #555; font-size: 13px;">Specification: {sku}</p>
            <p style="margin: 0; color: #555; font-size: 13px;">Quantity: {qty} {uom}</p>
            <p style="margin: 0; color: #555; font-size: 13px;">Est. unit price: INR {price}</p>
            <p style="margin: 0; color: #555; font-size: 13px;">Est. line total: INR {total_price}</p>
        </div>
        """

    # 4. Construct the custom body payload
    intro_html = f"""
    <p style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#15222e;margin:0 0 16px;">
        Hi,
    </p>
    <p style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#15222e;margin:0 0 16px;">
        I'm raising a purchase requisition for approval. Details are below.
    </p>

    <h4 style="margin: 20px 0 10px; color: #333; text-transform: uppercase;">Requisition Details</h4>
    <table style="width: 100%; font-size: 14px; margin-bottom: 20px;">
        <tr><td style="width: 150px; font-weight: bold; padding: 4px 0;">Requisition ID:</td><td>{pr_number}</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Raised by:</td><td>{requester_name} ({req_email or 'N/A'})</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Department:</td><td>{dept_code or 'N/A'}</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Cost centre:</td><td>N/A</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Date raised:</td><td>{created_at.strftime('%Y-%m-%d') if hasattr(created_at, 'strftime') else 'N/A'}</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Required by:</td><td>{required_date or 'N/A'}</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Category:</td><td>N/A</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Deliver to:</td><td>{loc_name or 'N/A'}</td></tr>
    </table>

    <h4 style="margin: 20px 0 10px; color: #333; text-transform: uppercase;">Items Requested</h4>
    <div style="background: #f9f9f9; padding: 15px; border-radius: 6px; margin-bottom: 20px;">
        {line_items_html}
    </div>

    <h4 style="margin: 20px 0 10px; color: #333; text-transform: uppercase;">Estimated Cost</h4>
    <table style="width: 100%; font-size: 14px; margin-bottom: 20px;">
        <tr><td style="width: 150px; font-weight: bold; padding: 4px 0;">Subtotal:</td><td>INR {total_amount}</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Tax (0%):</td><td>INR 0.00</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Estimated total:</td><td>INR {total_amount}</td></tr>
    </table>
    <p style="font-size: 12px; color: #777;">Note: costs are indicative. Final pricing will be confirmed by Procurement at the sourcing stage.</p>

    <h4 style="margin: 20px 0 10px; color: #333; text-transform: uppercase;">Budget</h4>
    <table style="width: 100%; font-size: 14px; margin-bottom: 20px;">
        <tr><td style="width: 150px; font-weight: bold; padding: 4px 0;">Budget line:</td><td>N/A</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">GL account:</td><td>N/A</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Budget available:</td><td>N/A</td></tr>
        <tr><td style="font-weight: bold; padding: 4px 0;">Balance after spend:</td><td>N/A</td></tr>
    </table>

    <h4 style="margin: 20px 0 10px; color: #333; text-transform: uppercase;">Business Justification</h4>
    <p style="font-size: 14px; background: #f9f9f9; padding: 15px; border-radius: 6px;">
        {remarks or 'N/A'}
    </p>

    <p style="margin-top: 20px;">Please approve or return with comments.</p>
    """

    subject = f"Purchase Requisition {pr_number} — {len(items_rows)} items — INR {total_amount}"
    
    html_body = build_email_html(
        subject=subject,
        preheader=f"Purchase Requisition approval required for {pr_number}",
        heading=f"Approval Required: {pr_number}",
        intro="",
        raw_intro=intro_html,
        outro=f"Approve: <a href='{approve_url}'>Approve</a><br/>Reject: <a href='{reject_url}'>Reject</a>",
        status="Awaiting Approval",
        tone="info",
        details=[], # Empty since we embedded them above
        cta="Review Request",
        cta_url=frontend_request_url
    )
    
    text_body = f"Please review PR {pr_number}.\nApprove: {approve_url}\nReject: {reject_url}"
    return subject, html_body, text_body
