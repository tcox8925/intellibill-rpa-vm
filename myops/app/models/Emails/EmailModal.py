from azure.communication.email import EmailClient
from fastapi import HTTPException
from app.core.config import settings
from pydantic import BaseModel, EmailStr
from typing import List, Optional
from app.models.Emails.templates.email_templates import myadmin_onboarding, myops_onboarding

class EmailAttachment(BaseModel):
    file_name: str
    content_type: str
    content: str  # base64 string

class EmailContent(BaseModel):
    sender: EmailStr
    to: Optional[List[EmailStr]] | str
    subject: str
    body: str
    html: bool = False
    cc: Optional[List[EmailStr]] = None
    bcc: Optional[List[EmailStr]] = None
    attachments: Optional[List[EmailAttachment]] = None

class EmailService:
    """Service class to manage Azure Communication Service (ACS) Email sending."""
    myops_sender_email = 'dataops@834labs.com'
    myadmin_sender_email = 'DoNotReply@enrollinsurance.com'
    

    def __init__(self):
        connection_string = settings.ACS_EMAIL_CONNECTION_STRING
        if not connection_string:
            raise ValueError("ACS_CONNECTION_STRING environment variable not set")
        
        self.client = EmailClient.from_connection_string(connection_string)
    
    def getValidatedEmail(self, email: str) -> str:
        if not email or "@" not in email:
            return self.myops_sender_email
        allowed_domains = {
            "834labs.com",
            "enrollinsurance.com",
            "patientcarehealth.com",
        }

        domain = email.split("@")[-1].lower()

        if domain in allowed_domains:
            return email

        return self.myops_sender_email


    async def send_email(self, email: EmailContent):
        """
        Send an email using Azure Communication Services Email.

        Args:
            to (str): Recipient email address
            subject (str): Subject line
            body (str): Email body (plain or HTML)
            html (bool): Whether the body is HTML (default False)
        """
        try:
            sender_email = self.getValidatedEmail(email.sender)
            
            message = {
                "senderAddress": sender_email,     
                "content": {
                    "subject": email.subject,
                    "html": email.body if email.html else None,
                    "plainText": email.body if not email.html else None,
                },
                "recipients": {
                    "to": [{"address": r} for r in (email.to or [])]
                },
            }

            if email.cc:
                message["recipients"]["cc"] = [{"address": c} for c in email.cc]

            if email.bcc:
                message["recipients"]["bcc"] = [{"address": b} for b in email.bcc]

            # Attachments support
            if email.attachments:
                message["attachments"] = [
                    {
                        "name": a.file_name,
                        "contentType": a.content_type,
                        "contentInBase64": a.content
                    }
                    for a in email.attachments
                ]

            poller = self.client.begin_send(message)
            return poller.result()

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Email send failed: {str(e)}")
            
    
    async def send_password_reset_email(self, token: str, recipient_email: str):
        """Send a password reset email with HTML formatting."""
        # TODO: get app url from settings based on environment
        reset_link = f"https://myops360.834labs.com/reset-password?token={token}"
        # reset_link = f"http://localhost:3000/reset-password?token={token}"
        

        html_content = f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.2;">
                    <h2>Password Reset Request</h2>
                    <p>Hello,</p>
                    <p>We received a request to reset your password. Click the button below to reset it:</p>
                    <p style="text-align:;">
                        <a href="{reset_link}" 
                        style="background-color:#0078D4; color:white; padding:5px 5px; 
                                text-decoration:none; border-radius:5px; display:inline-block;">
                        Reset Password
                        </a>
                    </p>
                    <p>If you did not request a password reset, you can safely ignore this email.</p>
                    <p>Thanks,<br/>The MyOps360 Team</p>
                </body>
            </html>
        """

        email_content = EmailContent(
            sender=self.myops_sender_email,
            to=recipient_email,
            subject="Reset Your Password",
            body=html_content,
            html=True
        )

        try:
            message = {
                "content": {
                    "subject": email_content.subject,
                    "html": email_content.body
                },
                "recipients": {
                    "to": [
                        {"address": email_content.to}
                    ]
                },
                "senderAddress": email_content.sender
            }

            poller = self.client.begin_send(message)
            return poller.result()

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Password reset email failed: {str(e)}")
        
    def _build_interruption_content(self, mail_type: str, mail_info) -> tuple[str, str]:
        """Build shared subject and HTML content for both email and Teams."""

        color = '#ff3b3b'
        header = 'SERVICE INTERRUPTION RESOLUTION'

        if mail_type == "service_interruption":
            description = mail_info.issue_description or "No description provided."
            resolution_date = mail_info.resolution_date or "No resolution date provided."
            interruption_time = mail_info.report_date or "No interruption time provided."
            header = 'SERVICE LEVEL INTERRUPTION'
            color = '#ff3b3b'

        elif mail_type == "interruption_activity":
            issue_description = mail_info.get("issue_description", "No description provided.")
            resolution_description = mail_info.get("resolution_description", "")
            activity_date = mail_info.get("Activity_date")
            activity_type = mail_info.get("Activity_type")

            if activity_type and activity_type.lower() == 'update':
                header = 'SERVICE INTERRUPTION UPDATE'
                color = '#ff3b3b'
            elif activity_type and activity_type.lower() == 'resolution':
                header = 'SERVICE INTERRUPTION RESOLUTION'
                color = '#39FF14'

        base_styles = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>Service Level Interruption</title>
            <style>
                body {{ margin: 0; padding: 0; background-color: #1d1d1d; font-family: Arial, sans-serif; color: #fff; }}
                .container {{ background-color: #2a2a2a; padding: 25px 35px; margin: 40px auto; border-top: 4px solid {color}; }}
                h2 {{ color: {color}; font-size: 18px; font-weight: bold; text-transform: uppercase; margin-bottom: 25px; letter-spacing: 0.5px; }}
                .row {{ display: flex; align-items: center; margin-bottom: 8px; }}
                .label {{ width: 230px; font-weight: 600; color: #ffffff; flex-shrink: 0; }}
                .label::after {{ content: ":"; margin-left: 3px; }}
                .value {{ color: #e5e5e5; margin-left: 10px; flex-grow: 1; }}
                hr {{ border: none; border-bottom: 1px solid #555; margin-top: 20px; }}
            </style>
            </head>
            <body>
        """

        if mail_type == "interruption_activity":
            body_content = f"""
            <div class="container">
                <h2>{header}</h2>
                <div class="row"><div class="label">Impacted Services</div><div class="value">myops360.834labs.com</div></div>
                <div class="row"><div class="label">Interruption Scope</div><div class="value">All Modules</div></div>
                <div class="row"><div class="label">Interruption Issue</div><div class="value">{issue_description}</div></div>
                <div class="row"><div class="label">Resolution Update</div><div class="value">{resolution_description}</div></div>
                <div class="row"><div class="label">Activity Date</div><div class="value">{activity_date}</div></div>
                <div class="row"><div class="label">Activity Type</div><div class="value">{activity_type}</div></div>
                <hr />
            </div>
            </body>
            </html>
            """

        elif mail_type == "service_interruption":
            body_content = f"""
            <div class="container">
                <h2>{header}</h2>
                <div class="row"><div class="label">Impacted Services</div><div class="value">myops360.834labs.com</div></div>
                <div class="row"><div class="label">Interruption Scope</div><div class="value">All Modules</div></div>
                <div class="row"><div class="label">Interruption Issue</div><div class="value">{description}</div></div>
                <div class="row"><div class="label">Interruption Time</div><div class="value">{interruption_time}</div></div>
                <div class="row"><div class="label">Resolution Time</div><div class="value">{resolution_date}</div></div>
                <hr />
            </div>
            </body>
            </html>
            """

        html_content = base_styles + body_content
        subject = "Service Interruption Notification"

        return subject, html_content

        
    async def send_teams_notification(
        self,
        mail_type: str,
        mail_info,
        recipient_list: Optional[List[str]] = None,
    ):
        """Send service interruption notification to Microsoft Teams channel."""

        print(f"Preparing to send Teams notification to: {recipient_list}")

        # ── Use shared content builder ──
        subject, html_content = self._build_interruption_content(mail_type, mail_info)

        try:
            message = {
                "content": {
                    "subject": subject,
                    "html": html_content
                },
                "recipients": {
                    "to": [{"address": addr} for addr in recipient_list]
                },
                "senderAddress": self.myops_sender_email
            }
            poller = self.client.begin_send(message)
            return poller.result()

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Teams notification failed: {str(e)}")

        
    async def send_service_interruption_email(
        self,
        mail_type: str,
        mail_info,
        recipient_list: Optional[List[str]] = None,
    ):
        """Send service interruption as email."""

        print(f"Preparing to send service interruption email to: {recipient_list}")

        # ── Use shared content builder ──
        subject, html_content = self._build_interruption_content(mail_type, mail_info)

        try:
            message = {
                "content": {
                    "subject": subject,
                    "html": html_content
                },
                "recipients": {
                    "to": [{"address": addr} for addr in recipient_list]
                },
                "senderAddress": self.myops_sender_email
            }
            poller = self.client.begin_send(message)
            return poller.result()

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Service interruption email failed: {str(e)}")


    # async def send_service_interruption_email(
    #     self,
    #     mail_type: str,
    #     mail_info: dict,
    #     process_id: str,
    # ):
    #     """Send a service interruption email with HTML formatting."""

    #     if process_id:
    #         from app.routes.ServicePerformance.service_interruption import get_process_type_users
    #         users = await get_process_type_users(process_id)
    #         recipient_list = [u.get("user_email") for u in users if u.get("email")]
    #         print(f"Recipient List: {recipient_list}")

    #     else:

    #         recipient_list = [
    #             "d49fcecc.834labs.com@amer.teams.ms",
    #             "steve@agilityholdingsgroup.com",
    #             "mhuertero@agilityholdingsgroup.com",
    #             "cjones@agilityholdingsgroup.com",
    #             "mark@agilityholdingsgroup.com",
    #             "mareda@agilepeopleandpayroll.com",
    #             "cshirley@agilityholdingsgroup.com",
    #             "ahill@healthedly.com",
    #             "DFine@834labs.com",
    #             "jfoster@834labs.com",
    #             "lucas@834labs.com",
    #             "JPoorna@834labs.com",
    #             "DCrockett@834Labs.com",
    #             "gbrasher@834labs.com",
    #             "jwillis@agilityholdingsgroup.com",
    #             "vivek@834labs.com"
    #         ]

        
    #     color = '#ff3b3b'
    #     header = 'SERVICE INTERRUPTION RESOLUTION'

    #     if mail_type == "service_interruption":
    #         description = mail_info.issue_description or "No description provided."
    #         resolution_date = mail_info.resolution_date or "No resolution date provided."
    #         interruption_time = mail_info.report_date or "No interruption time provided."

    #         header = 'SERVICE LEVEL INTERRUPTION'
    #         color = '#ff3b3b'


    #     if mail_type == "interruption_activity":
    #         issue_description = mail_info.get("issue_description", "No description provided.")
    #         resolution_description = mail_info.get("resolution_description", "")
    #         activity_date = mail_info.get("Activity_date")
    #         activity_type = mail_info.get("Activity_type")

    #         if activity_type == 'Update':
    #             header = 'SERVICE INTERRUPTION UPDATE'
    #             color = '#ff3b3b'
    #         elif activity_type == 'Resolution':
    #             header = 'SERVICE INTERRUPTION RESOLUTION'
    #             color = '#39FF14'
            

    #     html_content = f"""
    #         <!DOCTYPE html>
    #         <html lang="en">
    #         <head>
    #         <meta charset="UTF-8" />
    #         <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    #         <title>Service Level Interruption</title>
    #         <style>
    #             body {{
    #             margin: 0;
    #             padding: 0;
    #             background-color: #1d1d1d;
    #             font-family: Arial, sans-serif;
    #             color: #fff;
    #             }}

    #             .container {{
    #             background-color: #2a2a2a;
    #             padding: 25px 35px;
    #             margin: 40px auto;
    #             border-top: 4px solid #ff3b3b;
    #             }}

    #             h2 {{
    #             color: {color};
    #             font-size: 18px;
    #             font-weight: bold;
    #             text-transform: uppercase;
    #             margin-bottom: 25px;
    #             letter-spacing: 0.5px;
    #             }}

    #             .row {{
    #             display: flex;
    #             align-items: center;
    #             margin-bottom: 8px;
    #             white-space: nowrap;
    #             overflow: hidden;
    #             }}

    #             .label {{
    #             width: 230px;
    #             font-weight: 600;
    #             color: #ffffff;
    #             flex-shrink: 0;
    #             position: relative;
    #             }}

    #             .label::after {{
    #             content: ":";
    #             margin-left: 3px;
    #             }}

    #             .value {{
    #             color: #e5e5e5;
    #             margin-left: 10px;
    #             flex-grow: 1;
    #             overflow: hidden;
    #             text-overflow: ellipsis;
    #             white-space: nowrap;
    #             }}

    #             hr {{
    #             border: none;
    #             border-bottom: 1px solid #555;
    #             margin-top: 20px;
    #             }}

    #             @media (max-width: 600px) {{
    #             .row {{
    #                 flex-direction: column;
    #                 white-space: normal;
    #             }}
    #             .label {{
    #                 width: auto;
    #             }}
    #             .value {{
    #                 margin-left: 0;
    #                 text-overflow: unset;
    #                 white-space: normal;
    #             }}
    #             }}
    #         </style>
    #         </head>
    #         <body> """

    #     if mail_type == "interruption_activity":
    #         html_content += f"""
    #         <div class="container">
    #             <h2>{header}</h2>

    #             <div class="row">
    #             <div class="label">Impacted Services:</div>
    #             <div class="value">myops360.834labs.com</div>
    #             </div>

    #             <div class="row">
    #             <div class="label">Interruption Scope:</div>
    #             <div class="value">All Modules</div>
    #             </div>

    #             <div class="row">
    #             <div class="label">Interruption Issue:</div>
    #             <div class="value">{issue_description}</div>
    #             </div>

    #             <div class="row">
    #             <div class="label">Resolution Update:</div>
    #             <div class="value">{resolution_description}</div>
    #             </div>

    #             <div class="row">
    #             <div class="label">Activity Date:</div>
    #             <div class="value">{activity_date}</div>
    #             </div>

    #             <div class="row">
    #             <div class="label">Activity Type:</div>
    #             <div class="value">{activity_type}</div>
    #             </div>

    #             <hr />
    #         </div>
    #         </body>
    #         </html>
    #         """

    #     if mail_type == "service_interruption":
    #         html_content += f"""
    #         <div class="container">
    #             <h2>SERVICE LEVEL INTERRUPTION</h2>

    #             <div class="row">
    #             <div class="label">Impacted Services:</div>
    #             <div class="value">myops360.834labs.com</div>
    #             </div>

    #             <div class="row">
    #             <div class="label">Interruption Scope:</div>
    #             <div class="value">All Modules</div>
    #             </div>

    #             <div class="row">
    #             <div class="label">Interruption Issue:</div>
    #             <div class="value">{description}</div>
    #             </div>

    #             <div class="row">
    #             <div class="label">Interruption Time:</div>
    #             <div class="value">{interruption_time}</div>
    #             </div>

    #             <div class="row">
    #             <div class="label">Resolution Time</div>
    #             <div class="value">{resolution_date}</div>
    #             </div>

    #             <hr />
    #         </div>
    #         </body>
    #         </html>
    #         """


    #     recipient_email = recipient_list

    #     email_content = EmailContent(
    #         sender=self.myops_sender_email,
    #         to=recipient_email,
    #         subject="Service Interruption Notification",
    #         body=html_content,
    #         html=True
    #     )
    #     to_recipients = [{"address": addr} for addr in email_content.to or []]
    #     try:
    #         message = {
    #             "content": {
    #                 "subject": email_content.subject,
    #                 "html": email_content.body
    #             },
    #             "recipients": {
    #                 "to": to_recipients
    #             },
    #             "senderAddress": email_content.sender
    #         }

    #         poller = self.client.begin_send(message)
    #         return poller.result()

    #     except Exception as e:
    #         raise HTTPException(status_code=500, detail=f"Service interruption email failed: {str(e)}")

    async def send_onboarding_email(
        self,
        token: str,
        recipient_email: str,
        sender_email: str,
        agent_name: str,
        role: str,
        temporary_password: str,
        myadmin_logo: str,
        mail_logo: str,
        message_logo: str,
        phone_logo: str
    ):
        """Send an onboarding email with HTML formatting."""
        admin_roles = ["agent", "agent_support", "agent_exec", "agent_lead"]

        if role == "agent" or role == "agent_support":
            onboarding_url = settings.MYADMIN_STAGING_ONBOARDING_URL + f"?token={token}"
        elif role == "agent_exec" or role == "agent_lead":
            onboarding_url = settings.MYADMIN_STAGING_ONBOARDING_URL + f"?token={token}&?role=direct"
        else:
            onboarding_url = settings.MYOPS_ONBOARDING_URL

        if role in admin_roles:
            onboarding_template = myadmin_onboarding(
                agent_name=agent_name,
                myadmin_logo=myadmin_logo,
                onboarding_url=onboarding_url,
                phone_logo=phone_logo,
                message_logo=message_logo,
                mail_logo=mail_logo
            )

            email_content = EmailContent(
            sender=self.myadmin_sender_email,
            to=recipient_email,
            subject="Welcome to MyAdmin360!",
            body=onboarding_template,
            html=True
        )
            
        else:
            onboarding_template = myops_onboarding(
                recipient_email=recipient_email,
                temporary_password=temporary_password,
                onboarding_url=onboarding_url
            )

            email_content = EmailContent(
                sender=self.myops_sender_email,
                to=recipient_email,
                subject="Welcome to MyOps360!",
                body=onboarding_template,
                html=True
            )

        try:
            message = {
                "content": {
                    "subject": email_content.subject,
                    "html": email_content.body
                },
                "recipients": {
                    "to": [
                        {"address": email_content.to}
                    ]
                },
                "senderAddress": email_content.sender
            }

            poller = self.client.begin_send(message)
            return poller.result()

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Onboarding email failed: {str(e)}")