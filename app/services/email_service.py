import httpx
from app.core.config import settings
from app.core.logger import logger

class EmailService:
    def __init__(self):
        self.api_url = "https://api.brevo.com/v3/smtp/email"
        self.headers = {
            "accept": "application/json",
            "api-key": settings.BREVO_API_KEY,
            "content-type": "application/json"
        }

    async def send_otp_email(self, recipient_email: str, otp: str, subject: str = "Gallery Vault Security OTP"):
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f4f4f7; padding: 20px;">
                <div style="max-width: 600px; margin: 0 auto; background: #ffffff; padding: 30px; border-radius: 8px;">
                    <h2 style="color: #333;">Gallery Vault Verification</h2>
                    <p>Use the following 6-digit One Time Password (OTP) to proceed:</p>
                    <div style="font-size: 32px; font-weight: bold; color: #4A90E2; letter-spacing: 5px; margin: 20px 0;">
                        {otp}
                    </div>
                    <p>This code expires in {settings.OTP_EXPIRE_MINUTES} minutes.</p>
                </div>
            </body>
        </html>
        """
        payload = {
            "sender": {"name": settings.BREVO_SENDER_NAME, "email": settings.BREVO_SENDER_EMAIL},
            "to": [{"email": recipient_email}],
            "subject": subject,
            "htmlContent": html_content
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(self.api_url, json=payload, headers=self.headers)
                if response.status_code not in [200, 201, 202]:
                    logger.error(f"Failed to send Brevo email: {response.text}")
                    return False
                return True
            except Exception as e:
                logger.error(f"Exception raised during email sending: {str(e)}")
                return False

email_service = EmailService()
