def myadmin_onboarding(agent_name: str, onboarding_url: str, phone_logo: str = "", mail_logo: str = "", message_logo: str = "", myadmin_logo: str = ""):
    myadmin_onboarding_template = f"""           
        <!DOCTYPE html>
                <html lang="en">
                    <head>
                        <meta charset="UTF-8">
                        <meta name="viewport" content="width=device-width, initial-scale=1.0">
                        <title>Welcome to MyAdmin</title>
                        <style>
                            /* Reset styles for email clients */
                            body, table, td, p, a, li, blockquote {{
                                -webkit-text-size-adjust: 100%;
                                -ms-text-size-adjust: 100%;
                            }}
                            table, td {{
                                mso-table-lspace: 0pt;
                                mso-table-rspace: 0pt;
                            }}
                            img {{
                                -ms-interpolation-mode: bicubic;
                                border: 0;
                                height: auto;
                                line-height: 100%;
                                outline: none;
                                text-decoration: none;
                            }}
                            
                            /* Main styles */
                            body {{
                                margin: 0;
                                padding: 0;
                                width: 100% !important;
                                min-width: 100%;
                                height: 100%;
                                background: #f9fdff;
                                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                            }}
                            
                            /* Mobile responsive styles */
                            @media only screen and (max-width: 600px) {{ 
                                .mobile-padding {{
                                    padding: 20px !important;
                                }}
                                .mobile-text-center {{
                                    text-align: center !important;
                                }}
                                .mobile-full-width {{
                                    width: 100% !important;
                                }}
                                .mobile-inner-padding {{
                                    padding: 30px 20px !important;
                                }}
                            }}
                        </style>
                    </head>
                    <body>
                        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background: #f9fdff; width: 100%; min-width: 100%;">
                            <tr>
                                <td align="center" style="padding: 50px 20px;">
                                    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 1120px; background: #f9fdff; border-radius: 20px; box-shadow: -613px 0px 172px 0px rgba(0,49,105,0), -392px 0px 157px 0px rgba(0,49,105,0.01), -221px 0px 132px 0px rgba(0,49,105,0.03), -98px 0px 98px 0px rgba(0,49,105,0.04), -25px 0px 54px 0px rgba(0,49,105,0.05);">
                                        <tr>
                                            <td style="padding: 60px 40px;" class="mobile-inner-padding">
                                                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                                    <!-- Logo Section -->
                                                    <tr>
                                                        <td align="center" style="padding-bottom: 20px;">
                                                            <img src="data:image/png;base64,{myadmin_logo}" alt="MyAdmin Logo" width="200" height="200" style="display: block; margin: 0 auto 20px; border: 0; outline: none; text-decoration: none;">
                                                        </td>
                                                    </tr>
                                                    
                                                    <!-- Main Content -->
                                                    <tr>
                                                        <td align="center" style="padding: 47px 0;">
                                                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                                                <tr>
                                                                    <td align="center" style="padding-bottom: 20px;">
                                                                        <p style="font-size: 20px; line-height: 30px; color: #111111; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
                                                                            Hello <span style="font-weight: bold; color: #12bde1;">{agent_name}</span>!
                                                                        </p>
                                                                    </td>
                                                                </tr>
                                                                <tr>
                                                                    <td align="center" style="padding-bottom: 20px;">
                                                                        <p style="font-size: 16px; line-height: 21px; color: #111111; margin: 0 auto; padding: 0; max-width: 600px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
                                                                            Click the button below to start your resgistration process for the new MyAdmin 360. If you need assistance, don't hesitate to contact our support team.
                                                                        </p>
                                                                    </td>
                                                                </tr>
                                                                <tr>
                                                                    <td align="center" style="padding-bottom: 20px;">
                                                                        <a href="{onboarding_url}" style="display: inline-block; background: #12bde1; color: #ffffff; text-decoration: none; padding: 10px 20px; border-radius: 5px; font-weight: bold; font-size: 16px; line-height: 21px; text-align: center; max-width: 600px; width: 100%; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; box-shadow: -6px 34px 10px 0px rgba(18,189,225,0), -4px 22px 9px 0px rgba(18,189,225,0.02), -2px 12px 8px 0px rgba(18,189,225,0.08), -1px 5px 6px 0px rgba(18,189,225,0.13), 0px 1px 3px 0px rgba(18,189,225,0.15);">
                                                                            START ONBOARDING
                                                                        </a>
                                                                    </td>
                                                                </tr>
                                                                <tr>
                                                                    <td align="center">
                                                                        <p style="font-size: 16px; line-height: 21px; color: #111111; margin: 0 auto; padding: 0; max-width: 722px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
                                                                            Best regards,<br>
                                                                            The MyAdmin Team
                                                                        </p>
                                                                    </td>
                                                                </tr>
                                                            </table>
                                                        </td>
                                                    </tr>
                                                    
                                                    <!-- Confidentiality Notice -->
                                                    <tr>
                                                        <td align="center" style="padding-bottom: 20px;">
                                                            <p style="font-size: 12px; line-height: 15px; color: #111111; text-transform: uppercase; margin: 0 auto; padding: 0; max-width: 722px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
                                                                <span style="font-weight: bold;">CONFIDENTIALITY NOTICE</span><br>
                                                                The information contained in this email is personal and confidential, intended solely for the use of the individual named above. If you are not the intended recipient, please be advised that any review, disclosure, copying, distribution, or use of this communication is strictly prohibited. If you have received this email in error, please notify the sender immediately and delete it from your system.
                                                            </p>
                                                        </td>
                                                    </tr>
                                                    
                                                    <!-- Assistance Section -->
                                                    <tr>
                                                        <td align="center">
                                                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                                                <tr>
                                                                    <td align="center" style="padding-bottom: 20px;">
                                                                        <p style="font-size: 14px; line-height: 20px; color: #111111; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
                                                                            For login assistance, contact <span style="font-weight: bold; color: #12bde1;">Agility Producer Support</span> at
                                                                        </p>
                                                                    </td>
                                                                </tr>
                                                                <tr>
                                                                    <td align="center">
                                                                        <table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 0 auto;">
                                                                            <tr>
                                                                                <td align="center" style="padding: 0 20px; vertical-align: top;">
                                                                                    <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                                                                                        <tr>
                                                                                            <td align="center" style="padding-bottom: 10px;">
                                                                                                <div style="width: 20px; height: 20px; background: #12bde1; border-radius: 2px; display: flex; align-items: center; justify-content: center; margin: 0 auto; box-shadow: 0px 9px 3px 0px rgba(18,189,225,0), 0px 6px 2px 0px rgba(18,189,225,0.02), 0px 3px 2px 0px rgba(18,189,225,0.08), 0px 1px 1px 0px rgba(18,189,225,0.13), 0px 0px 1px 0px rgba(18,189,225,0.15);">
                                                                                                    <img src="data:image/png;base64,{phone_logo}" alt="Phone" width="15" height="15" style="display: block; border: 0; outline: none; text-decoration: none;">
                                                                                                </div>
                                                                                            </td>
                                                                                        </tr>
                                                                                        <tr>
                                                                                            <td align="center">
                                                                                                <p style="font-size: 14px; line-height: 24px; color: #111111; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">(866) 590 - 9771</p>
                                                                                            </td>
                                                                                        </tr>
                                                                                    </table>
                                                                                </td>
                                                                                <td align="center" style="padding: 0 20px; vertical-align: top;">
                                                                                    <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                                                                                        <tr>
                                                                                            <td align="center" style="padding-bottom: 10px;">
                                                                                                <div style="width: 20px; height: 20px; background: #12bde1; border-radius: 2px; display: flex; align-items: center; justify-content: center; margin: 0 auto; box-shadow: 0px 9px 3px 0px rgba(18,189,225,0), 0px 6px 2px 0px rgba(18,189,225,0.02), 0px 3px 2px 0px rgba(18,189,225,0.08), 0px 1px 1px 0px rgba(18,189,225,0.13), 0px 0px 1px 0px rgba(18,189,225,0.15);">
                                                                                                    <img src="data:image/png;base64,{mail_logo}" alt="Email" width="15" height="15" style="display: block; border: 0; outline: none; text-decoration: none;">
                                                                                                </div>
                                                                                            </td>
                                                                                        </tr>
                                                                                        <tr>
                                                                                            <td align="center">
                                                                                                <p style="font-size: 14px; line-height: 24px; color: #111111; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">support@enrollinsurance.com</p>
                                                                                            </td>
                                                                                        </tr>
                                                                                    </table>
                                                                                </td>
                                                                                <td align="center" style="padding: 0 20px; vertical-align: top;">
                                                                                    <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                                                                                        <tr>
                                                                                            <td align="center" style="padding-bottom: 10px;">
                                                                                                <div style="width: 20px; height: 20px; background: #12bde1; border-radius: 2px; display: flex; align-items: center; justify-content: center; margin: 0 auto; box-shadow: 0px 9px 3px 0px rgba(18,189,225,0), 0px 6px 2px 0px rgba(18,189,225,0.02), 0px 3px 2px 0px rgba(18,189,225,0.08), 0px 1px 1px 0px rgba(18,189,225,0.13), 0px 0px 1px 0px rgba(18,189,225,0.15);">
                                                                                                    <img src="data:image/png;base64,{message_logo}" alt="SMS" width="15" height="15" style="display: block; border: 0; outline: none; text-decoration: none;">
                                                                                                </div>
                                                                                            </td>
                                                                                        </tr>
                                                                                        <tr>
                                                                                            <td align="center">
                                                                                                <p style="font-size: 14px; line-height: 24px; color: #111111; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">Agility Support</p>
                                                                                            </td>
                                                                                        </tr>
                                                                                    </table>
                                                                                </td>
                                                                            </tr>
                                                                        </table>
                                                                    </td>
                                                                </tr>
                                                            </table>
                                                        </td>
                                                    </tr>
                                                </table>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                        </table>
                    </body>
                </html>
            """
    return myadmin_onboarding_template

def myops_onboarding(onboarding_url: str, recipient_email: str = "", temporary_password: str = ""):
    myops_onboarding_template = f"""
                <html>
                    <body style="font-family: Arial, sans-serif; line-height: 1.2;">
                        <h2>Welcome to MyOps360!</h2>
                        <p>Hello,</p>
                        <p>Your account has been created successfully. Below are your login details:</p>
                        <ul>
                            <li><strong>Email:</strong> {recipient_email}</li>
                            <li><strong>Temporary Password:</strong> {temporary_password}</li>
                        </ul>
                        <p>Please log in using the temporary password and change it immediately for security reasons.</p>
                        <p>Login here: <a href={onboarding_url}>MyOps360 Login</a></p>
                        <p>Thanks,<br/>The MyOps360 Team</p>
                    </body>
                </html>
            """
    return myops_onboarding_template