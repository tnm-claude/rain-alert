"""
Database models for Rain Alert
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Location(db.Model):
    """Location to monitor for rain"""
    __tablename__ = 'locations'

    id = db.Column(db.Integer, primary_key=True)
    address = db.Column(db.String(500), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    is_main = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationship
    alerts = db.relationship('Alert', backref='location', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'address': self.address,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'active': self.active,
            'is_main': self.is_main,
            'created_at': self.created_at.isoformat()
        }


class Alert(db.Model):
    """Rain alert for a specific location"""
    __tablename__ = 'alerts'

    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    alert_time = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    rain_expected_at = db.Column(db.DateTime, nullable=False)
    minutes_ahead = db.Column(db.Integer, nullable=False)  # 30, 20, or 10
    message = db.Column(db.String(500), nullable=False)
    dismissed = db.Column(db.Boolean, default=False, nullable=False)
    slack_dismissed_until = db.Column(db.DateTime, nullable=True)  # Slack-specific snooze
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # User feedback for improving alert accuracy
    user_feedback = db.Column(db.Boolean, nullable=True)  # True=accurate, False=false alarm, None=no feedback
    feedback_timestamp = db.Column(db.DateTime, nullable=True)
    radar_images_saved = db.Column(db.String(1000), nullable=True)  # Comma-separated list of saved image filenames

    def is_slack_dismissed(self):
        """Check if alert is currently dismissed via Slack"""
        if not self.slack_dismissed_until:
            return False
        return datetime.utcnow() < self.slack_dismissed_until

    def to_dict(self):
        return {
            'id': self.id,
            'location_id': self.location_id,
            'location_address': self.location.address if self.location else None,
            'alert_time': self.alert_time.isoformat(),
            'rain_expected_at': self.rain_expected_at.isoformat(),
            'minutes_ahead': self.minutes_ahead,
            'message': self.message,
            'dismissed': self.dismissed,
            'created_at': self.created_at.isoformat()
        }


class NotificationSettings(db.Model):
    """Notification destination settings (single row)"""
    __tablename__ = 'notification_settings'

    id = db.Column(db.Integer, primary_key=True)

    # Email settings
    email_enabled = db.Column(db.Boolean, default=False, nullable=False)
    email_address = db.Column(db.String(200))
    email_smtp_server = db.Column(db.String(200))
    email_smtp_port = db.Column(db.Integer)
    email_smtp_user = db.Column(db.String(200))
    email_smtp_password = db.Column(db.String(200))

    # Slack settings
    slack_enabled = db.Column(db.Boolean, default=False, nullable=False)
    slack_webhook_url = db.Column(db.String(500))

    # Telegram settings
    telegram_enabled = db.Column(db.Boolean, default=False, nullable=False)
    telegram_bot_token = db.Column(db.String(200))
    telegram_chat_id = db.Column(db.String(100))

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'email_enabled': self.email_enabled,
            'email_address': self.email_address,
            'email_smtp_server': self.email_smtp_server,
            'email_smtp_port': self.email_smtp_port,
            'email_smtp_user': self.email_smtp_user,
            'slack_enabled': self.slack_enabled,
            'slack_webhook_url': self.slack_webhook_url,
            'telegram_enabled': self.telegram_enabled,
            'telegram_bot_token': self.telegram_bot_token,
            'telegram_chat_id': self.telegram_chat_id,
            'updated_at': self.updated_at.isoformat()
        }
