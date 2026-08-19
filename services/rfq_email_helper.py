import logging
import datetime
# pyrefly: ignore [missing-import]
from sqlalchemy import text
from .email_builder import build_email_html
from .notification import notification_service
import os

logger = logging.getLogger(__name__)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

from database import SessionLocal

async def send_rfq_invitation(pr_id: int, rfq_number: str, vendor_id: int):
    """
    Fetches vendor and PR details to construct and send the RFQ invitation email.
    """
    db = SessionLocal()
    try:
        # 1. Fetch vendor details
        vendor_query = text("""
            SELECT ud.first_name, ud.last_name, ud.email, cd.company_name
            FROM user_details ud
            JOIN company_details cd ON ud.company_id = cd.company_id
            WHERE ud.company_id = :vendor_id
            LIMIT 1
        """)
        vendor_row = db.execute(vendor_query, {"vendor_id": vendor_id}).fetchone()
        if not vendor_row:
            logger.warning(f"send_rfq_invitation: Vendor details not found for vendor_id {vendor_id}")
            return

        v_fname, v_lname, vendor_email, vendor_company_name = vendor_row
        vendor_contact_name = f"{v_fname or ''} {v_lname or ''}".strip() or "Vendor Contact"

        # 2. Fetch buyer details (the PR creator)
        pr_query = text("""
            SELECT pr.requested_by, pr.pr_number, pr.created_at
            FROM purchase_requisitions pr
            WHERE pr.id = :pr_id
        """)
        pr_row = db.execute(pr_query, {"pr_id": pr_id}).fetchone()
        if not pr_row:
            logger.warning(f"send_rfq_invitation: PR not found for pr_id {pr_id}")
            return

        requested_by, pr_number, pr_created_at = pr_row
        rfq_title = f"RFQ for {pr_number}"

        buyer_query = text("""
            SELECT first_name, last_name, email, designation, phone_number
            FROM user_details
            WHERE user_id = :buyer_id
        """)
        buyer_row = db.execute(buyer_query, {"buyer_id": requested_by}).fetchone()
        
        if buyer_row:
            b_fname, b_lname, buyer_email, buyer_title, buyer_phone = buyer_row
            buyer_name = f"{b_fname or ''} {b_lname or ''}".strip() or "Purchasing Dept"
        else:
            buyer_name = "Purchasing Dept"
            buyer_email = "procurement@anktaerospace.com"
            buyer_title = "Procurement Executive"
            buyer_phone = "N/A"

        # 3. Calculate Deadlines (defaults since not in DB yet)
        today = datetime.datetime.now()
        issue_date_str = today.strftime("%Y-%m-%d")
        ack_date_str = (today + datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        clarification_date_str = (today + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        submission_date_str = (today + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

        portal_link = f"{FRONTEND_URL}/vendor/rfq" # Adjust to exact route if needed
        portal_support_contact = "support@anktaerospace.com"
        company_name = "Ankt Aerospace Private Limited"

        # 4. Construct raw HTML intro
        raw_intro = f"""
        <p style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#15222e;margin:0 0 16px;">
            Dear {vendor_contact_name},
        </p>
        <p style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#15222e;margin:0 0 16px;">
            {company_name} has published a Request for Quotation and would like to invite {vendor_company_name} to participate.
        </p>

        <table style="width: 100%; font-size: 14px; margin-bottom: 20px;">
            <tr><td style="width: 200px; font-weight: bold; padding: 4px 0;">RFQ reference:</td><td>{rfq_number}</td></tr>
            <tr><td style="font-weight: bold; padding: 4px 0;">Title:</td><td>{rfq_title}</td></tr>
            <tr><td style="font-weight: bold; padding: 4px 0;">Published on:</td><td>{issue_date_str}</td></tr>
            <tr><td style="font-weight: bold; padding: 4px 0;">Clarifications close:</td><td>{clarification_date_str}</td></tr>
            <tr><td style="font-weight: bold; padding: 4px 0;">Quotations close:</td><td>{submission_date_str} (IST)</td></tr>
        </table>

        <h4 style="margin: 20px 0 10px; color: #333; text-transform: uppercase;">WHERE TO FIND THE DETAILS</h4>
        <p style="font-size: 14px; line-height: 1.6;">
            The complete requirement — scope, specifications, quantities, delivery and all terms and conditions — is available on the supplier portal:
        </p>
        <p style="margin: 15px 0;"><a href="{portal_link}" style="color: #10508c; font-weight: bold;">{portal_link}</a></p>
        <p style="font-size: 14px; line-height: 1.6;">
            Sign in with your registered credentials and open RFQ {rfq_number} to view the documents and submit your quotation. Please base your offer solely on the information published there.
        </p>

        <h4 style="margin: 20px 0 10px; color: #333; text-transform: uppercase;">PLEASE CONFIRM BY {ack_date_str}</h4>
        <p style="font-size: 14px; line-height: 1.6;">
            So that we can finalise the bidding list, please acknowledge this invitation by <strong>{ack_date_str}</strong> in one of two ways:
        </p>
        <ul style="font-size: 14px; line-height: 1.6;">
            <li><strong>Accept</strong> — you have received the RFQ and intend to submit a quotation</li>
            <li><strong>Decline</strong> — you do not intend to quote on this occasion</li>
        </ul>
        <p style="font-size: 14px; line-height: 1.6;">
            You can record either response directly on the portal, or simply reply to this mail with the word ACCEPT or DECLINE and the RFQ reference.
        </p>
        <p style="font-size: 14px; line-height: 1.6;">
            A decline costs you nothing and will not affect future invitations. What we ask is that you tell us either way, so we are not holding the RFQ open for a response that is not coming.
        </p>
        <p style="font-size: 14px; line-height: 1.6;">
            If we do not hear from you by <strong>{ack_date_str}</strong>, we will take it that you are not participating in this RFQ.
        </p>

        <h4 style="margin: 20px 0 10px; color: #333; text-transform: uppercase;">CLARIFICATIONS</h4>
        <p style="font-size: 14px; line-height: 1.6;">
            Questions on the requirement should be raised through the portal before {clarification_date_str} so that our response reaches all invited suppliers at the same time. For access or login issues, contact <a href="mailto:{portal_support_contact}">{portal_support_contact}</a>.
        </p>
        <p style="font-size: 14px; line-height: 1.6;">
            Thank you for your interest in working with {company_name}. We look forward to your response.
        </p>

        <p style="font-size: 14px; line-height: 1.6; margin-top: 25px;">
            Regards,<br/>
            <strong>{buyer_name}</strong><br/>
            {buyer_title} | Procurement<br/>
            {company_name}<br/>
            {buyer_email} | {buyer_phone or ''}
        </p>

        <div style="margin-top: 30px; padding-top: 15px; border-top: 1px solid #ddd; font-size: 11px; color: #777;">
            This invitation is confidential and issued solely to enable you to prepare a quotation. It does not constitute an order or an offer to contract. {company_name} is not obliged to accept any quotation received.
        </div>
        """

        subject = f"Action required: RFQ {rfq_number} published — confirm participation by {ack_date_str}"
        
        html_body = build_email_html(
            subject=subject,
            preheader=f"RFQ {rfq_number} published. Please confirm your participation.",
            heading=f"RFQ Invitation: {rfq_number}",
            intro="",
            raw_intro=raw_intro,
            outro="",
            status="Action Required",
            tone="info",
            details=[],
            cta="Go to Portal",
            cta_url=portal_link
        )
        
        text_body = f"Please review RFQ {rfq_number} at {portal_link} and confirm your participation by {ack_date_str}."

        # 5. Send the email
        await notification_service.send_email(
            to=[vendor_email],
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
        logger.info(f"Successfully sent RFQ invitation for {rfq_number} to {vendor_email}")
    except Exception as exc:
        logger.error(f"Failed to send RFQ invitation to {vendor_email}: {exc}")
    finally:
        db.close()
