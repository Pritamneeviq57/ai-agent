"""
Email Sender using Microsoft Graph API (App-Only Authentication)
Sends meeting summaries via email using application permissions
Works with Railway deployment
"""
from src.api.graph_client_apponly import GraphAPIClientAppOnly
from src.utils.logger import setup_logger
from datetime import datetime
import os
import re
import requests

logger = setup_logger(__name__)

# Email settings from environment variables
EMAIL_TEST_MODE = os.getenv("EMAIL_TEST_MODE", "true").lower() == "true"
EMAIL_TEST_RECIPIENT = os.getenv("EMAIL_TEST_RECIPIENT", "")
EMAIL_SENDER_USER_ID = os.getenv("EMAIL_SENDER_USER_ID", "")  # User ID or email to send from


def format_summary_to_html(summary_text: str) -> str:
    """
    Convert plain text summary to well-formatted HTML
    """
    if not summary_text:
        return "<p>No summary available.</p>"
    
    html_parts = []
    lines = summary_text.split('\n')
    current_list_items = []
    in_numbered_list = False
    
    for line in lines:
        line = line.strip()
        
        if not line:
            if current_list_items:
                tag = 'ol' if in_numbered_list else 'ul'
                html_parts.append(f'<{tag} style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append(f'</{tag}>')
                current_list_items = []
                in_numbered_list = False
            continue
        
        # Markdown headers
        header_match = re.match(r'^(#{1,3})\s+(.+)$', line)
        if header_match:
            if current_list_items:
                tag = 'ol' if in_numbered_list else 'ul'
                html_parts.append(f'<{tag} style="margin: 15px 0; padding-left: 30px;">')
                html_parts.extend(current_list_items)
                html_parts.append(f'</{tag}>')
                current_list_items = []
                in_numbered_list = False
            
            level = len(header_match.group(1))
            text = header_match.group(2).strip()
            
            if level == 1:
                html_parts.append(f'<h1 style="margin-top: 30px; padding: 15px; background: #0078d4; color: white; border-radius: 6px;">{text}</h1>')
            elif level == 2:
                html_parts.append(f'<h2 style="margin-top: 25px; padding: 12px; background: #f0f8ff; color: #0078d4; border-left: 4px solid #0078d4;">{text}</h2>')
            else:
                html_parts.append(f'<h3 style="margin-top: 20px; padding: 10px; background: #f5f5f5; color: #333; border-left: 3px solid #0078d4;">{text}</h3>')
            continue
        
        # Bold text headers
        bold_match = re.match(r'^\*\*([^\*]+)\*\*\s*(.*)$', line)
        if bold_match:
            if current_list_items:
                tag = 'ol' if in_numbered_list else 'ul'
                html_parts.append(f'<{tag} style="margin: 15px 0; padding-left: 30px;">')
                html_parts.extend(current_list_items)
                html_parts.append(f'</{tag}>')
                current_list_items = []
                in_numbered_list = False
            
            title = bold_match.group(1).strip()
            content = bold_match.group(2).strip()
            html_parts.append(f'<h4 style="color: #0078d4; margin-top: 15px;">{title}</h4>')
            if content:
                html_parts.append(f'<p style="margin: 10px 0 10px 15px;">{content}</p>')
            continue
        
        # Bullet points
        if line.startswith('•') or line.startswith('*') or line.startswith('-'):
            if current_list_items and in_numbered_list:
                html_parts.append('<ol style="margin: 15px 0; padding-left: 30px;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ol>')
                current_list_items = []
            in_numbered_list = False
            content = line.lstrip('•*-').strip()
            # Convert **text** to bold
            content = re.sub(r'\*\*([^\*]+)\*\*', r'<strong>\1</strong>', content)
            current_list_items.append(f'<li style="margin: 8px 0;">{content}</li>')
            continue
        
        # Numbered items
        num_match = re.match(r'^(\d+)\.\s+(.+)$', line)
        if num_match:
            if current_list_items and not in_numbered_list:
                html_parts.append('<ul style="margin: 15px 0; padding-left: 30px;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ul>')
                current_list_items = []
            in_numbered_list = True
            content = num_match.group(2).strip()
            content = re.sub(r'\*\*([^\*]+)\*\*', r'<strong>\1</strong>', content)
            current_list_items.append(f'<li style="margin: 8px 0;">{content}</li>')
            continue
        
        # Regular paragraph
        if current_list_items:
            tag = 'ol' if in_numbered_list else 'ul'
            html_parts.append(f'<{tag} style="margin: 15px 0; padding-left: 30px;">')
            html_parts.extend(current_list_items)
            html_parts.append(f'</{tag}>')
            current_list_items = []
            in_numbered_list = False
        
        formatted_line = re.sub(r'\*\*([^\*]+)\*\*', r'<strong>\1</strong>', line)
        html_parts.append(f'<p style="margin: 10px 0; line-height: 1.8;">{formatted_line}</p>')
    
    # Close any remaining list
    if current_list_items:
        tag = 'ol' if in_numbered_list else 'ul'
        html_parts.append(f'<{tag} style="margin: 15px 0; padding-left: 30px;">')
        html_parts.extend(current_list_items)
        html_parts.append(f'</{tag}>')
    
    return '\n'.join(html_parts)


def send_summary_email_apponly(
    graph_client: GraphAPIClientAppOnly,
    sender_user_id: str,
    recipient_email: str,
    meeting_subject: str,
    meeting_date: str,
    summary_text: str,
    model_name: str = "Claude"
) -> bool:
    """
    Send meeting summary via email using Microsoft Graph API (App-Only Auth)
    
    Args:
        graph_client: Authenticated GraphAPIClientAppOnly instance
        sender_user_id: User ID or email of the sender (must be in your org)
        recipient_email: Email address to send to
        meeting_subject: Meeting subject/title
        meeting_date: Meeting date string
        summary_text: Generated summary text
        model_name: Name of AI model used
    
    Returns:
        bool: True if sent successfully
    """
    try:
        # Override with test recipient if in test mode
        if EMAIL_TEST_MODE:
            if EMAIL_TEST_RECIPIENT:
                logger.info(f"🧪 TEST MODE: Overriding recipient to {EMAIL_TEST_RECIPIENT}")
                recipient_email = EMAIL_TEST_RECIPIENT
            else:
                logger.warning("⚠️ TEST MODE enabled but no EMAIL_TEST_RECIPIENT set")
                return False
        
        if not recipient_email:
            logger.warning("📧 No recipient email provided")
            return False
        
        if not sender_user_id:
            logger.warning("📧 No EMAIL_SENDER_USER_ID configured")
            return False
        
        # Format summary to HTML
        formatted_summary = format_summary_to_html(summary_text)
        
        # Create email body
        email_body = f"""
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
            <div style="max-width: 750px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                <!-- Header -->
                <div style="background: linear-gradient(to right, #0078d4, #005a9e); color: white; padding: 25px; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0; font-size: 24px;">📋 Meeting Summary</h1>
                    <p style="margin: 10px 0 0 0; opacity: 0.9;">Generated by {model_name}</p>
                </div>
                
                <!-- Meeting Info -->
                <div style="background-color: #f9f9f9; padding: 20px 25px; margin: 20px; border-radius: 6px; border-left: 4px solid #0078d4;">
                    <p style="margin: 8px 0;"><strong style="color: #0078d4;">Meeting:</strong> {meeting_subject}</p>
                    <p style="margin: 8px 0;"><strong style="color: #0078d4;">Date:</strong> {meeting_date}</p>
                    <p style="margin: 8px 0;"><strong style="color: #0078d4;">AI Model:</strong> 
                        <span style="background-color: #e3f2fd; padding: 3px 8px; border-radius: 3px; color: #0078d4; font-weight: 600;">{model_name}</span>
                    </p>
                </div>
                
                <!-- Summary Content -->
                <div style="padding: 25px 35px; background-color: #ffffff;">
                    {formatted_summary}
                </div>
                
                <!-- Footer -->
                <div style="padding: 20px; border-top: 2px solid #e1dfdd; background-color: #fafafa; border-radius: 0 0 8px 8px; font-size: 12px; color: #666; text-align: center;">
                    <p style="margin: 5px 0;">✨ This is an automated summary generated from Teams meeting transcript</p>
                    <p style="margin: 5px 0;">📅 Generated on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
                    <p style="margin: 8px 0 0 0; font-size: 11px; color: #999;">Powered by Neeviq Teams Meeting Summarizer</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Create email message
        message = {
            "message": {
                "subject": f"Meeting Summary ({model_name}): {meeting_subject}",
                "body": {
                    "contentType": "HTML",
                    "content": email_body
                },
                "toRecipients": [
                    {"emailAddress": {"address": recipient_email}}
                ]
            },
            "saveToSentItems": True
        }
        
        # Send via Graph API using app-only auth
        # Use /users/{id}/sendMail endpoint (not /me/sendMail)
        url = f"{graph_client.base_url}/users/{sender_user_id}/sendMail"
        headers = {
            "Authorization": f"Bearer {graph_client.access_token}",
            "Content-Type": "application/json"
        }
        
        logger.info(f"📧 Sending email from {sender_user_id} to {recipient_email}")
        
        response = requests.post(url, headers=headers, json=message, timeout=30)
        
        if response.status_code in [202, 204]:
            logger.info(f"✅ Email sent successfully to {recipient_email}")
            return True
        else:
            logger.error(f"❌ Email failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Email error: {str(e)}")
        return False

