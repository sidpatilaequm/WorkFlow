import html

def escape(s: str) -> str:
    if s is None:
        return ""
    # We want to preserve newlines for intro/outro, but we replace them with <br /> only within the para function.
    return html.escape(str(s), quote=True)

TONES = {
    "info": {"bg": "#e3eef9", "ink": "#10508c", "edge": "#cadcee"},
    "ok": {"bg": "#dfefe8", "ink": "#1c6047", "edge": "#c6e0d4"},
    "warn": {"bg": "#f7efd8", "ink": "#7d6413", "edge": "#ecdfb8"},
    "bad": {"bg": "#fae9e6", "ink": "#b03225", "edge": "#f0cfc9"},
}

def build_email_html(
    subject: str,
    preheader: str,
    heading: str,
    intro: str,
    outro: str,
    status: str = None,
    tone: str = "info",
    details: list = None,
    cta: str = None,
    cta_url: str = None,
    company_name: str = "Ankt Aerospace Private Limited",
    foot_text: str = "Sent to you because you hold a role in the Supplier Portal.",
    foot_legal: str = "Ankt Aerospace Private Limited · Commercially confidential."
) -> str:
    """Builds a beautiful HTML email matching the Supplier Portal Admin theme."""
    F = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
    M = "'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"
    t = TONES.get(tone, TONES["info"])
    
    def para(s):
        if not s: 
            return ""
        # split by double newline to form distinct paragraphs
        paragraphs = [p.strip() for p in str(s).split("\n\n") if p.strip()]
        out = ""
        for p in paragraphs:
            # any single newlines within a paragraph become <br />
            safe_p = escape(p).replace("\n", "<br />")
            out += f'<p style="font-family:{F};font-size:15px;line-height:1.6;color:#15222e;margin:0 0 16px;">{safe_p}</p>'
        return out
        
    details_html = ""
    if details:
        rows = []
        for item in details:
            if not item or len(item) != 2:
                continue
            k, v = item
            if not k: 
                continue
            rows.append(
                f'<tr>'
                f'<td style="font-family:{M};font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#5c6e7e;padding:5px 14px 5px 0;vertical-align:top;white-space:nowrap;">{escape(k)}</td>'
                f'<td style="font-family:{F};font-size:14px;color:#15222e;padding:5px 0;font-weight:600;">{escape(v)}</td>'
                f'</tr>'
            )
        if rows:
            rows_str = "".join(rows)
            details_html = f'''
            <tr><td style="padding:0 32px 20px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:#f4f7fa;border:1px solid #d3dde5;border-radius:3px;">
                <tr><td style="padding:14px 18px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0">{rows_str}</table></td></tr>
              </table>
            </td></tr>'''
            
    cta_block = ""
    if cta and cta_url:
        cta_block = f'''
        <tr><td style="padding:0 32px 22px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td style="background:#10508c;border-radius:3px;">
              <a href="{escape(cta_url)}" style="display:inline-block;padding:13px 26px;font-family:{F};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;">{escape(cta)}</a>
            </td></tr></table>
          <p style="font-family:{F};font-size:12px;color:#5c6e7e;margin:12px 0 0;word-break:break-all;">
            If the button does not work, paste this into your browser:<br />
            <a href="{escape(cta_url)}" style="color:#10508c;">{escape(cta_url)}</a></p>
        </td></tr>'''
        
    status_row = ""
    if status:
        status_row = f'''
        <tr><td style="padding:13px 32px;background:{t["bg"]};border-bottom:1px solid {t["edge"]};">
          <span style="font-family:{M};font-size:11px;font-weight:600;letter-spacing:.13em;text-transform:uppercase;color:{t["ink"]};">{escape(status)}</span></td></tr>'''
          
    legal_br = f'<br /><br />{escape(foot_legal)}' if foot_legal else ''

    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<meta name="x-apple-disable-message-reformatting" />
<title>{escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#eef2f6;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef2f6;">
<tr><td align="center" style="padding:24px 12px;">

  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
         style="width:600px;max-width:100%;background:#ffffff;border:1px solid #d3dde5;border-radius:4px;">

    <tr><td style="padding:20px 32px;border-bottom:1px solid #e4ebf0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="font-family:{M};font-size:12px;font-weight:600;letter-spacing:.06em;color:#ffffff;background:#15222e;padding:5px 7px;border-radius:3px;width:1%;white-space:nowrap;">SP</td>
        <td style="padding-left:11px;font-family:{F};font-size:16px;font-weight:700;color:#15222e;">
          {escape(company_name)}
          <span style="font-family:{M};font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:#5c6e7e;">&nbsp; Supplier Portal</span>
        </td></tr></table>
    </td></tr>

    {status_row}

    <tr><td style="padding:28px 32px 6px;">
      <h1 style="font-family:{F};font-size:25px;line-height:1.2;font-weight:700;letter-spacing:-.4px;color:#15222e;margin:0 0 14px;">{escape(heading)}</h1>
      {para(intro)}
    </td></tr>

    {details_html}
    {cta_block}

    <tr><td style="padding:0 32px 8px;">{para(outro)}</td></tr>
  </table>

  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;">
    <tr><td style="padding:16px 32px 0;font-family:{F};font-size:12px;line-height:1.6;color:#5c6e7e;">
      {escape(foot_text)}{legal_br}
    </td></tr></table>

</td></tr></table></body></html>'''
