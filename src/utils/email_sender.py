"""
Email Sender using Microsoft Graph API
Sends meeting summaries via email to organizers/participants
"""
from src.api.graph_client_delegated import GraphAPIClientDelegated
from src.utils.logger import setup_logger
from config.settings import Settings
from datetime import datetime
import re
import requests

logger = setup_logger(__name__)


def format_summary_to_html(summary_text: str) -> str:
    """
    Convert plain text summary to well-formatted HTML with proper structure
    Enhanced to handle GPT-OSS-Safeguard output format
    
    Args:
        summary_text: Plain text summary from the LLM
    
    Returns:
        str: HTML-formatted summary with proper headings and structure
    """
    if not summary_text:
        return "<p>No summary available.</p>"
    
    html_parts = []
    
    # Split by lines to process each line
    lines = summary_text.split('\n')
    current_list_items = []
    in_numbered_list = False
    in_table = False
    
    for i, line in enumerate(lines):
        original_line = line
        line = line.strip()
        
        # Skip empty lines but close lists if needed
        if not line:
            if current_list_items:
                if in_numbered_list:
                    html_parts.append('<ol style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                else:
                    html_parts.append('<ul style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ol>' if in_numbered_list else '</ul>')
                current_list_items = []
                in_numbered_list = False
            continue
        
        # Skip separator lines (dashed lines, pipes)
        if re.match(r'^[\|\-\s]+$', line) or line.startswith('|---'):
            continue
        
        # Check for markdown headers (###, ##, #)
        markdown_header_match = re.match(r'^(#{1,3})\s+(.+)$', line)
        if markdown_header_match:
            # Close any open list
            if current_list_items:
                if in_numbered_list:
                    html_parts.append('<ol style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                else:
                    html_parts.append('<ul style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ol>' if in_numbered_list else '</ul>')
                current_list_items = []
                in_numbered_list = False
            
            # Close any open table
            if in_table:
                html_parts.append('</table>')
                in_table = False
            
            header_level = len(markdown_header_match.group(1))  # Number of # symbols
            header_text = markdown_header_match.group(2).strip()
            
            # Style based on header level
            if header_level == 1:  # # Header
                html_parts.append(f'<h1 style="margin-top: 35px; margin-bottom: 20px; padding: 18px 20px; background: linear-gradient(to right, #0078d4, #005a9e); color: #ffffff; font-size: 24px; font-weight: 700; border-radius: 6px; border-left: 5px solid #004578;">{header_text}</h1>')
            elif header_level == 2:  # ## Header
                html_parts.append(f'<h2 style="margin-top: 30px; margin-bottom: 15px; padding: 15px 18px; background: linear-gradient(to right, #f0f8ff, #ffffff); color: #0078d4; font-size: 20px; font-weight: 600; border-left: 5px solid #0078d4; border-radius: 5px;">{header_text}</h2>')
            else:  # ### Header (most common)
                html_parts.append(f'<h3 style="margin-top: 25px; margin-bottom: 12px; padding: 12px 15px; background-color: #f0f8ff; color: #005a9e; font-size: 18px; font-weight: 600; border-left: 4px solid #0078d4; border-radius: 4px;">{header_text}</h3>')
            continue
        
        # Check for major section headers (e.g., "1. MEETING OVERVIEW**")
        major_header_match = re.match(r'^(\d+)\.\s+([A-Z\s&]+)\*\*\s*$', line)
        if major_header_match:
            # Close any open list
            if current_list_items:
                if in_numbered_list:
                    html_parts.append('<ol style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                else:
                    html_parts.append('<ul style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ol>' if in_numbered_list else '</ul>')
                current_list_items = []
                in_numbered_list = False
            
            section_num = major_header_match.group(1)
            section_title = major_header_match.group(2).strip()
            html_parts.append(f'<div style="margin-top: 30px; margin-bottom: 15px; padding: 15px; background: linear-gradient(to right, #f0f8ff, #ffffff); border-left: 5px solid #0078d4; border-radius: 5px;"><h2 style="margin: 0; color: #0078d4; font-size: 20px; font-weight: 600;">{section_num}. {section_title}</h2></div>')
            continue
        
        # Check for subsection headers with ** (e.g., "**Purpose / Context**")
        subsection_match = re.match(r'^\*\*([^\*]+)\*\*\s*(.*)$', line)
        if subsection_match:
            # Close any open list
            if current_list_items:
                if in_numbered_list:
                    html_parts.append('<ol style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                else:
                    html_parts.append('<ul style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ol>' if in_numbered_list else '</ul>')
                current_list_items = []
                in_numbered_list = False
            
            subsection_title = subsection_match.group(1).strip()
            content = subsection_match.group(2).strip()
            html_parts.append(f'<h4 style="color: #2c5aa0; margin-top: 20px; margin-bottom: 10px; font-size: 16px; font-weight: 600;">{subsection_title}</h4>')
            if content:
                # Remove leading dash or hyphen
                content = content.lstrip('–-').strip()
                html_parts.append(f'<p style="margin: 10px 0 10px 15px; line-height: 1.8; color: #000000;">{content}</p>')
            continue
        
        # Check for table rows (e.g., "| Topic | Details | Issues / Questions Raised |")
        if line.startswith('|') and '|' in line[1:]:
            # Skip separator lines (e.g., "|---|---|")
            if re.match(r'^\|\s*[\-\:]+\s*\|', line):
                continue
            
            # Parse table row
            cells = [cell.strip() for cell in line.split('|')[1:-1]]  # Remove first and last empty strings
            if cells:
                # Convert **text** to <strong>text</strong> in cells
                formatted_cells = []
                for cell in cells:
                    formatted_cell = re.sub(r'\*\*([^\*]+)\*\*', r'<strong>\1</strong>', cell)
                    formatted_cell = re.sub(r'\*([^\*]+)\*', r'<em>\1</em>', formatted_cell)
                    formatted_cells.append(f'<td style="padding: 8px 12px; border-bottom: 1px solid #e0e0e0; vertical-align: top;">{formatted_cell}</td>')
                
                if not in_table:
                    html_parts.append('<table style="width: 100%; border-collapse: collapse; margin: 20px 0; background-color: #fafafa;">')
                    in_table = True
                
                html_parts.append(f'<tr>{"".join(formatted_cells)}</tr>')
                continue
        
        # Check for list items with pipe separator (e.g., "| **Domino DWG file** | ...")
        pipe_item_match = re.match(r'^\|\s*\*\*([^\*]+)\*\*\s*\|\s*(.+?)\s*\|', line)
        if pipe_item_match:
            if current_list_items and in_numbered_list:
                html_parts.append('<ol style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ol>')
                current_list_items = []
            
            in_numbered_list = False
            topic = pipe_item_match.group(1).strip()
            details = pipe_item_match.group(2).strip()
            current_list_items.append(f'<li style="margin: 12px 0;"><strong style="color: #0078d4;">{topic}:</strong> {details}</li>')
            continue
        
        # Check for regular numbered items (e.g., "1. **Domino DWG** – Akshay cuts...")
        numbered_item_match = re.match(r'^(\d+)\.\s+\*\*([^\*]+)\*\*\s*[–-]\s*(.+)$', line)
        if numbered_item_match:
            if current_list_items and not in_numbered_list:
                html_parts.append('<ul style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ul>')
                current_list_items = []
            
            in_numbered_list = True
            item_title = numbered_item_match.group(2).strip()
            item_content = numbered_item_match.group(3).strip()
            current_list_items.append(f'<li style="margin: 12px 0;"><strong style="color: #0078d4;">{item_title}</strong> – {item_content}</li>')
            continue
        
        # Check for bullet items with ** (e.g., "• **Domino DWG** – cut-out inlay...")
        bullet_item_match = re.match(r'^[•\*\-]\s+\*\*([^\*]+)\*\*\s*[–-]\s*(.+)$', line)
        if bullet_item_match:
            if current_list_items and in_numbered_list:
                html_parts.append('<ol style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ol>')
                current_list_items = []
            
            in_numbered_list = False
            item_title = bullet_item_match.group(1).strip()
            item_content = bullet_item_match.group(2).strip()
            current_list_items.append(f'<li style="margin: 12px 0;"><strong style="color: #0078d4;">{item_title}</strong> – {item_content}</li>')
            continue
        
        # Check for simple bullet points
        if line.startswith('•') or line.startswith('*') or (line.startswith('-') and not re.match(r'^\-{3,}', line)):
            if current_list_items and in_numbered_list:
                html_parts.append('<ol style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ol>')
                current_list_items = []
            
            in_numbered_list = False
            content = line.lstrip('•*-').strip()
            current_list_items.append(f'<li style="margin: 10px 0; line-height: 1.8; color: #000000;">{content}</li>')
            continue
        
        # Check for simple numbered items
        simple_numbered_match = re.match(r'^(\d+)\.\s+(.+)$', line)
        if simple_numbered_match:
            if current_list_items and not in_numbered_list:
                html_parts.append('<ul style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ul>')
                current_list_items = []
            
            in_numbered_list = True
            content = simple_numbered_match.group(2).strip()
            current_list_items.append(f'<li style="margin: 10px 0; line-height: 1.8; color: #000000;">{content}</li>')
            continue
        
        # Regular paragraph
        else:
            # Close any open table
            if in_table:
                html_parts.append('</table>')
                in_table = False
            
            # Close any open list
            if current_list_items:
                if in_numbered_list:
                    html_parts.append('<ol style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                else:
                    html_parts.append('<ul style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
                html_parts.extend(current_list_items)
                html_parts.append('</ol>' if in_numbered_list else '</ul>')
                current_list_items = []
                in_numbered_list = False
            
            # Format the content - convert **text** to bold
            formatted_line = re.sub(r'\*\*([^\*]+)\*\*', r'<strong>\1</strong>', line)
            html_parts.append(f'<p style="margin: 12px 0 12px 15px; line-height: 1.8; color: #000000;">{formatted_line}</p>')
    
    # Close any remaining open table
    if in_table:
        html_parts.append('</table>')
    
    # Close any remaining open list
    if current_list_items:
        if in_numbered_list:
            html_parts.append('<ol style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
        else:
            html_parts.append('<ul style="margin: 15px 0; padding-left: 30px; line-height: 1.9;">')
        html_parts.extend(current_list_items)
        html_parts.append('</ol>' if in_numbered_list else '</ul>')
    
    return '\n'.join(html_parts)


def send_summary_email(
    graph_client: GraphAPIClientDelegated,
    recipient_email: str,
    meeting_subject: str,
    meeting_date: str,
    summary_text: str,
    meeting_id: str = None,
    model_name: str = None,
    organizer_participants: list = None
) -> bool:
    """
    Send meeting summary via email using Microsoft Graph API
    
    Args:
        graph_client: Authenticated GraphAPIClientDelegated instance
        recipient_email: Email address of the recipient (organizer) - kept for backward compatibility
        meeting_subject: Subject/title of the meeting
        meeting_date: Date and time of the meeting
        summary_text: The generated summary text
        meeting_id: Optional meeting ID for reference
        model_name: Optional name of the AI model used for summarization
        organizer_participants: List of organizer participant emails (neeviq.com emails) to send to
    
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        # TEST MODE: If enabled, only send to test email(s)
        if Settings.EMAIL_TEST_MODE:
            test_recipients = Settings.get_email_test_recipients()
            if test_recipients:
                unique_emails = test_recipients
                logger.info(f"🧪 TEST MODE: Sending to test email(s) only: {', '.join(test_recipients)}")
                logger.info(f"   (All participant emails will be ignored in test mode)")
            else:
                logger.warning("⚠️  TEST MODE enabled but no EMAIL_TEST_RECIPIENT set")
                unique_emails = []
        else:
            # PRODUCTION MODE: Extract participant emails from organizer_participants parameter
            # Filter to only include internal users (neeviq.com domain) and exclude sender
            unique_emails = []
            
            # Get sender email to exclude (from graph_client if available, or from settings)
            sender_email = None
            try:
                # Try to get sender email from graph client (for delegated auth)
                if hasattr(graph_client, 'user_email'):
                    sender_email = graph_client.user_email.lower()
                elif hasattr(graph_client, 'user_id'):
                    sender_email = graph_client.user_id.lower()
            except:
                pass
            
            # Also check for common sender emails
            if not sender_email:
                sender_email = "cs@neeviq.com"
            
            if organizer_participants:
                # Extract emails from organizer_participants list
                # Filter: Only include emails with "neeviq.com" domain
                for participant in organizer_participants:
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
            
            # If no organizer participants found, use recipient_email as fallback (if internal)
            if not unique_emails and recipient_email:
                recipient_lower = recipient_email.lower()
                if "neeviq.com" in recipient_lower and recipient_lower != sender_email.lower():
                    unique_emails.append(recipient_email)
            
            # Always add EMAIL_TEST_RECIPIENT(s) if set (even in production mode)
            # But only if they're internal emails
            test_recipients = Settings.get_email_test_recipients()
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
            if organizer_participants:
                total_participants = len(organizer_participants)
                internal_count = len(unique_emails)
                logger.info(f"📧 Filtered participants: {internal_count} internal (neeviq.com) out of {total_participants} total")
            
            if not unique_emails:
                test_recipients = Settings.get_email_test_recipients()
                if test_recipients:
                    logger.warning("⚠️  No recipient emails found, using test email(s)")
                    # Fallback to test email(s) if no recipients found
                    unique_emails = test_recipients
                else:
                    logger.error("❌ No recipient emails found and no EMAIL_TEST_RECIPIENT configured")
        
        # Format the email subject (include model name if provided)
        if model_name:
            email_subject = f"Meeting Summary ({model_name}): {meeting_subject}"
        else:
            email_subject = f"Meeting Summary: {meeting_subject}"
        
        # Log model name for debugging
        logger.info(f"📊 Sending email with model: {model_name if model_name else 'No model specified'}")
        logger.info(f"📧 Sending to {len(unique_emails)} recipient(s)")
        
        # Format meeting date to show only date (no time)
        formatted_meeting_date = meeting_date
        try:
            # Try to parse various date formats and extract just the date
            from datetime import datetime as dt
            # Handle ISO format: 2025-12-30T14:00:00.0000000
            if 'T' in meeting_date:
                date_part = meeting_date.split('T')[0]
                # Try to format it nicely: YYYY-MM-DD or convert to readable format
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
        
        # Validate summary before sending
        if not summary_text or not isinstance(summary_text, str) or len(summary_text.strip()) < 50:
            logger.error(f"❌ Cannot send email: summary is empty or invalid (length: {len(summary_text) if summary_text else 0})")
            return False
        
        # Convert plain text summary to HTML
        formatted_summary = format_summary_to_html(summary_text)
        
        # Format model name for display (simplified HTML for better email client compatibility)
        model_display = ""
        if model_name:
            model_display = f'<p style="margin: 10px 0 0 0; font-size: 14px; color: rgba(255,255,255,0.95); background-color: rgba(255,255,255,0.2); padding: 10px 15px; border-radius: 5px;">🤖 <strong>Generated by {model_name} Model</strong></p>'
        
        # Format meeting ID for display
        meeting_id_display = ""
        if meeting_id:
            # Truncate long meeting IDs
            display_id = meeting_id[:50] + "..." if len(meeting_id) > 50 else meeting_id
            meeting_id_display = f'<p style="margin: 5px 0;"><strong style="color: #0078d4;">Meeting ID:</strong> <span style="font-family: monospace; font-size: 11px; color: #666;">{display_id}</span></p>'
        
        # Format participants display
        participants_display = ""
        if len(unique_emails) > 1:
            participants_display = f'<p style="margin: 8px 0;"><strong style="color: #0078d4;">Recipients:</strong> <span style="font-size: 12px; color: #666;">{len(unique_emails)} organizer participants</span></p>'
        
        # Format the email body (HTML)
        email_body = f"""
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #000000; background-color: #f5f5f5; margin: 0; padding: 20px;">
            <div style="max-width: 750px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                <!-- Header -->
               
                
                <!-- Meeting Info -->
                <div style="background-color: #f9f9f9; padding: 20px 25px; margin: 20px; border-radius: 6px; border-left: 4px solid #0078d4;">
                    <p style="margin: 8px 0;"><strong style="color: #0078d4;">Meeting:</strong> <span style="color: #1a1a1a; font-size: 16px; font-weight: 600;">{meeting_subject}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #0078d4;">Date:</strong> {formatted_meeting_date}</p>
                    {f'<p style="margin: 8px 0;"><strong style="color: #0078d4;">AI Model:</strong> <span style="background-color: #e3f2fd; padding: 3px 8px; border-radius: 3px; color: #0078d4; font-weight: 600;">{model_name}</span></p>' if model_name else ''}
                   
                </div>
                
                <!-- Summary Content -->
                <div style="padding: 25px 35px; background-color: #ffffff; color: #000000;">
                    {formatted_summary}
                </div>
                
                <!-- Footer -->
                <div style="margin: 0; padding: 25px; border-top: 2px solid #e1dfdd; background-color: #fafafa; border-radius: 0 0 8px 8px; font-size: 12px; color: #666; text-align: center;">
                    <p style="margin: 5px 0;">✨ This is an automated summary generated from Teams meeting transcript using AI</p>
                    <p style="margin: 5px 0;">📅 Generated on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
                    <p style="margin: 8px 0 0 0; font-size: 11px; color: #999;">Powered by Neeviq Teams Meeting Summarizer</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Create the email message payload
        # Graph API expects the message object directly, not wrapped
        # Use the unique_emails we extracted above
        all_recipients = unique_emails
        
        # Build recipients list
        recipients = []
        for email in all_recipients:
            recipients.append({
                "emailAddress": {
                    "address": email
                }
            })
        
        message = {
            "message": {
                "subject": email_subject,
                "body": {
                    "contentType": "HTML",
                    "content": email_body
                },
                "toRecipients": recipients
            },
            "saveToSentItems": True  # Boolean, not string
        }
        
        # Send email via Graph API
        # Endpoint: POST /me/sendMail
        # Note: sendMail endpoint returns 202 Accepted or 204 No Content on success (empty body)
        endpoint = "/me/sendMail"
        logger.info(f"📧 Sending summary email to {len(all_recipients)} recipient(s): {', '.join(all_recipients)}")
        
        # Use direct requests call to better handle the response
        # The sendMail endpoint returns 202/204 with empty body on success
        url = f"{graph_client.base_url}{endpoint}"
        headers = graph_client.get_headers()
        
        try:
            response = requests.post(
                url=url,
                headers=headers,
                json=message,
                timeout=30
            )
            
            # Check response status
            if response.status_code in [202, 204]:
                # Success - 202 Accepted or 204 No Content
                logger.info(f"✅ Email sent successfully to {len(all_recipients)} recipient(s): {', '.join(all_recipients)}")
                logger.info(f"   Response status: {response.status_code} (Accepted/No Content)")
                return True
            elif response.status_code >= 400:
                # Error response
                error_text = response.text if response.text else "No error details"
                logger.error(f"❌ Email sending failed with status {response.status_code}")
                logger.error(f"   Error: {error_text}")
                return False
            else:
                # Unexpected status code
                logger.warning(f"⚠️  Unexpected response status: {response.status_code}")
                logger.info(f"✅ Assuming success - Email sent to {len(all_recipients)} recipient(s)")
                return True
                
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Network error sending email: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error sending email: {str(e)}")
            return False
        
    except Exception as e:
        logger.error(f"❌ Failed to send email: {str(e)}")
        return False


def send_summary_to_organizer(
    graph_client: GraphAPIClientDelegated,
    organizer_email: str,
    meeting_subject: str,
    meeting_date: str,
    summary_text: str,
    meeting_id: str = None,
    model_name: str = None,
    participants: list = None
) -> bool:
    """
    Convenience function to send summary email to meeting organizer and all organizer participants
    
    Args:
        graph_client: Authenticated GraphAPIClientDelegated instance
        organizer_email: Email address of the meeting organizer
        meeting_subject: Subject/title of the meeting
        meeting_date: Date and time of the meeting
        summary_text: The generated summary text
        meeting_id: Optional meeting ID for reference
        model_name: Optional name of the AI model used for summarization
        participants: List of all meeting participants (will filter for neeviq.com emails)
    
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    # Extract organizer participants (neeviq.com emails) from participants list
    organizer_participants = []
    if participants:
        for participant in participants:
            if isinstance(participant, dict):
                email = participant.get("email", "")
            elif isinstance(participant, str):
                email = participant
            else:
                continue
            
            if email and "neeviq.com" in email.lower():
                organizer_participants.append(participant)
    
    # If no organizer participants found but organizer_email exists, use it
    if not organizer_participants and organizer_email:
        organizer_participants = [{"email": organizer_email}]
    
    if not organizer_participants:
        logger.warning("⚠️  No organizer participants found, skipping email send")
        return False
    
    return send_summary_email(
        graph_client=graph_client,
        recipient_email=organizer_email,
        meeting_subject=meeting_subject,
        meeting_date=meeting_date,
        summary_text=summary_text,
        meeting_id=meeting_id,
        model_name=model_name,
        organizer_participants=organizer_participants
    )

