"""
Notification service for sending alerts via Email, Slack, and Telegram
"""
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os


class NotificationService:
    """Service for sending notifications"""

    @staticmethod
    def send_slack(webhook_url: str, message: str, alert_id: int = None, latitude: float = None, longitude: float = None, address: str = None) -> bool:
        """Send notification to Slack via webhook with radar image"""
        try:
            # Build Slack Block Kit message if we have alert details
            if alert_id:
                # Radar image from weather2day.co.il
                radar_image_url = "https://www.weather2day.co.il/radar.php"

                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{message}*\n📍 Location: {address}"
                        }
                    },
                    {
                        "type": "image",
                        "image_url": radar_image_url,
                        "alt_text": "Israel Rain Radar"
                    }
                ]

                payload = {
                    "text": message,  # Fallback text
                    "blocks": blocks
                }
            else:
                # Simple text message fallback
                payload = {'text': message}

            response = requests.post(
                webhook_url,
                json=payload,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            print(f"[Notifications] Slack error: {e}")
            return False

    @staticmethod
    def send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
        """Send notification to Telegram"""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            response = requests.post(
                url,
                json={'chat_id': chat_id, 'text': message},
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            print(f"[Notifications] Telegram error: {e}")
            return False

    @staticmethod
    def send_email(
        smtp_server: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        to_address: str,
        subject: str,
        message: str
    ) -> bool:
        """Send notification via email"""
        try:
            msg = MIMEMultipart()
            msg['From'] = smtp_user
            msg['To'] = to_address
            msg['Subject'] = subject
            msg.attach(MIMEText(message, 'plain'))

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)

            return True
        except Exception as e:
            print(f"[Notifications] Email error: {e}")
            return False

    @staticmethod
    def send_alert(settings, alert_message: str, alert=None):
        """Send alert using configured notification methods

        Args:
            settings: NotificationSettings object
            alert_message: Message text to send
            alert: Optional Alert object for rich Slack messages
        """
        success_count = 0
        total_enabled = 0

        # Send to Slack
        if settings.slack_enabled and settings.slack_webhook_url:
            total_enabled += 1
            # If alert object provided, send rich message with location and dismiss button
            if alert and hasattr(alert, 'location'):
                if NotificationService.send_slack(
                    settings.slack_webhook_url,
                    alert_message,
                    alert_id=alert.id,
                    latitude=alert.location.latitude,
                    longitude=alert.location.longitude,
                    address=alert.location.address
                ):
                    success_count += 1
                    print(f"[Notifications] Sent rich message to Slack")
            else:
                # Fallback to simple message
                if NotificationService.send_slack(settings.slack_webhook_url, alert_message):
                    success_count += 1
                    print(f"[Notifications] Sent to Slack")

        # Send to Telegram
        if settings.telegram_enabled and settings.telegram_bot_token and settings.telegram_chat_id:
            total_enabled += 1
            if NotificationService.send_telegram(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                alert_message
            ):
                success_count += 1
                print(f"[Notifications] Sent to Telegram")

        # Send to Email
        if settings.email_enabled and all([
            settings.email_address,
            settings.email_smtp_server,
            settings.email_smtp_port,
            settings.email_smtp_user,
            settings.email_smtp_password
        ]):
            total_enabled += 1
            if NotificationService.send_email(
                settings.email_smtp_server,
                settings.email_smtp_port,
                settings.email_smtp_user,
                settings.email_smtp_password,
                settings.email_address,
                "Rain Alert",
                alert_message
            ):
                success_count += 1
                print(f"[Notifications] Sent to Email")

        if total_enabled == 0:
            print("[Notifications] No notification methods configured")
        else:
            print(f"[Notifications] Sent {success_count}/{total_enabled} notifications")

        return success_count > 0
