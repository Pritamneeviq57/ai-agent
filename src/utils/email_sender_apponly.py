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

def get_email_test_recipients():
    """
    Get list of test email recipients from EMAIL_TEST_RECIPIENT environment variable.
    Supports comma-separated values for multiple recipients.
    Returns a list of email addresses (stripped of whitespace).
    """
    if not EMAIL_TEST_RECIPIENT:
        return []
    # Split by comma and strip whitespace from each email
    recipients = [email.strip() for email in EMAIL_TEST_RECIPIENT.split(',') if email.strip()]
    return recipients


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
    recipient_email: str = None,
    meeting_subject: str = None,
    meeting_date: str = None,
    summary_text: str = None,
    model_name: str = "Claude",
    participants: list = None
) -> bool:
    """
    Send meeting summary via email using Microsoft Graph API (App-Only Auth)
    
    Args:
        graph_client: Authenticated GraphAPIClientAppOnly instance
        sender_user_id: User ID or email of the sender (must be in your org)
        recipient_email: Email address to send to (kept for backward compatibility)
        meeting_subject: Meeting subject/title
        meeting_date: Meeting date string
        summary_text: Generated summary text
        model_name: Name of AI model used
        participants: List of participant dicts or emails to send to (all meeting participants)
    
    Returns:
        bool: True if sent successfully
    """
    try:
        # Extract all participant emails
        # Filter to only include internal users (neeviq.com domain) and exclude sender
        unique_emails = []
        
        # Get sender email to exclude
        # sender_user_id could be an email or user ID, check if it's an email
        sender_email = None
        if sender_user_id:
            sender_lower = sender_user_id.lower()
            if "@" in sender_lower:
                # It's an email address
                sender_email = sender_lower
            else:
                # It's a user ID, use default sender email
                sender_email = "cs@neeviq.com"
        else:
            sender_email = "cs@neeviq.com"
        
        logger.debug(f"📧 Sender email to exclude: {sender_email}")
        
        if participants:
            # Extract emails from participants list
            # Filter: Only include emails with "neeviq.com" domain
            for participant in participants:
                if isinstance(participant, dict):
                    email = participant.get("email", "")
                elif isinstance(participant, str):
                    email = participant
                else:
                    continue
                
                if email:
                    email_lower = email.lower()
                    # Only include internal users (neeviq.com domain)
                    if "neeviq.com" in email_lower:
                        # Exclude sender email
                        if email_lower != sender_email.lower():
                            unique_emails.append(email)
                        else:
                            logger.debug(f"📧 Excluding sender email: {email}")
                    else:
                        logger.debug(f"📧 Excluding external email (not neeviq.com): {email}")
        
        # If no participants found, use recipient_email as fallback (if internal)
        if not unique_emails and recipient_email:
            recipient_lower = recipient_email.lower()
            if "neeviq.com" in recipient_lower and recipient_lower != sender_email.lower():
                unique_emails.append(recipient_email)
        
        # Always add EMAIL_TEST_RECIPIENT(s) if set (even in production mode)
        # But only if they're internal emails
        test_recipients = get_email_test_recipients()
        for test_recipient in test_recipients:
            test_recipient_lower = test_recipient.lower()
            if "neeviq.com" in test_recipient_lower and test_recipient_lower != sender_email.lower():
                unique_emails.append(test_recipient)
                logger.info(f"📧 Adding test recipient to email list: {test_recipient}")
            else:
                logger.warning(f"⚠️  Test recipient is not an internal email (neeviq.com), skipping: {test_recipient}")
        
        # Remove duplicates while preserving order
        seen = set()
        deduplicated_emails = []
        for email in unique_emails:
            email_lower = email.lower()
            if email_lower not in seen:
                seen.add(email_lower)
                deduplicated_emails.append(email)
        
        unique_emails = deduplicated_emails
        
        # Log filtering results
        if participants:
            total_participants = len(participants)
            internal_count = len(unique_emails)
            logger.info(f"📧 Filtered participants: {internal_count} internal (neeviq.com) out of {total_participants} total")
        
        # Override with test recipient(s) only if in test mode
        if EMAIL_TEST_MODE:
            test_recipients = get_email_test_recipients()
            if test_recipients:
                logger.info(f"🧪 TEST MODE: Overriding recipients to test email(s) only: {', '.join(test_recipients)}")
                logger.info(f"   (All {len(deduplicated_emails)} participant emails will be ignored in test mode)")
                unique_emails = test_recipients
            else:
                logger.warning("⚠️ TEST MODE enabled but no EMAIL_TEST_RECIPIENT set")
                return False
        
        if not unique_emails:
            logger.warning("📧 No recipient emails provided")
            return False
        
        if not sender_user_id:
            logger.warning("📧 No EMAIL_SENDER_USER_ID configured")
            return False
        
        # Format summary to HTML
        formatted_summary = format_summary_to_html(summary_text)
        
        # Format meeting date to show only date (no time)
        formatted_meeting_date = meeting_date
        if meeting_date:
            try:
                # Try to parse various date formats and extract just the date
                from datetime import datetime as dt
                # Handle ISO format: 2025-12-30T14:00:00.0000000
                if 'T' in meeting_date:
                    date_part = meeting_date.split('T')[0]
                    # Try to format it nicely: YYYY-MM-DD
                    try:
                        parsed_date = dt.fromisoformat(meeting_date.replace('Z', '+00:00') if meeting_date.endswith('Z') else meeting_date.split('.')[0])
                        formatted_meeting_date = parsed_date.strftime('%Y-%m-%d')
                    except:
                        formatted_meeting_date = date_part
                # Handle other formats
                elif len(meeting_date) >= 10:
                    formatted_meeting_date = meeting_date[:10]  # Take first 10 chars (YYYY-MM-DD)
            except Exception as e:
                logger.debug(f"Could not parse meeting date '{meeting_date}': {e}, using as-is")
                # If parsing fails, try to extract date part
                if 'T' in meeting_date:
                    formatted_meeting_date = meeting_date.split('T')[0]
        
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
                    <p style="margin: 8px 0;"><strong style="color: #0078d4;">Meeting:</strong> <span style="color: #1a1a1a; font-size: 16px; font-weight: 600;">{meeting_subject}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #0078d4;">Date:</strong> {formatted_meeting_date}</p>
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
        
        # Build recipients list
        recipients = []
        for email in unique_emails:
            recipients.append({
                "emailAddress": {
                    "address": email
                }
            })
        
        # Create email message
        message = {
            "message": {
                "subject": f"Meeting Summary ({model_name}): {meeting_subject}",
                "body": {
                    "contentType": "HTML",
                    "content": email_body
                },
                "toRecipients": recipients
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
        
        logger.info(f"📧 Sending email from {sender_user_id} to {len(unique_emails)} recipient(s): {', '.join(unique_emails)}")
        
        response = requests.post(url, headers=headers, json=message, timeout=30)
        
        if response.status_code in [202, 204]:
            logger.info(f"✅ Email sent successfully to {len(unique_emails)} recipient(s): {', '.join(unique_emails)}")
            return True
        else:
            logger.error(f"❌ Email failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Email error: {str(e)}")
        return False

