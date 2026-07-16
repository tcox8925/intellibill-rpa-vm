def get_otp_email_body(otp: str) -> str:
    return f"""<html>
                <body style="font-family: Arial, sans-serif; line-height: 1.2;">
                    <h2>One time password</h2>
                    <p>Hello,</p>
                    <p>Here is your OTP: <strong>{otp}</strong></p>
                    <p>If you did not request the OTP, please ignore this email.</p>
                    <p>Thanks,<br/>The MyOps360 Team</p>
                </body>
            </html>
        """
