"""
Web routes and API endpoints
"""
from flask import render_template, request, jsonify, redirect, url_for, send_from_directory
from app.models import db, Location, Alert, NotificationSettings
from app.weather import WeatherService
from app.notifications import NotificationService
from app.radar import RadarService
from datetime import datetime
import os


def register_routes(app):
    """Register all routes with the Flask app"""

    @app.route('/')
    def index():
        """Home page - show locations and active alerts"""
        locations = Location.query.filter_by(active=True).order_by(Location.created_at.desc()).all()
        alerts = Alert.query.filter_by(dismissed=False).order_by(Alert.created_at.desc()).all()
        return render_template('index.html', locations=locations, alerts=alerts)

    @app.route('/review')
    def review():
        """Alert review page - review all alerts from now forward with radar images"""
        # Get all alerts from now onwards (not dismissed old ones)
        # Order: current alerts first, then historical (newest first)
        alerts = Alert.query.order_by(Alert.created_at.desc()).all()
        return render_template('review.html', alerts=alerts)

    # API Endpoints

    @app.route('/api/locations', methods=['POST'])
    def api_add_location():
        """Add a new location"""
        data = request.get_json()
        address = data.get('address', '').strip()

        if not address:
            return jsonify({'error': 'Address is required'}), 400

        # Geocode address
        result = WeatherService.geocode_address(address)
        if not result:
            return jsonify({'error': 'Could not find this address'}), 404

        lat, lon, display_name = result

        # Check if location already exists
        existing = Location.query.filter_by(
            latitude=lat,
            longitude=lon
        ).first()

        if existing:
            if not existing.active:
                existing.active = True
                db.session.commit()
            return jsonify(existing.to_dict()), 200

        # Check if this will be the only active location
        active_count = Location.query.filter_by(active=True).count()
        is_first = (active_count == 0)

        # Create new location
        location = Location(
            address=display_name,
            latitude=lat,
            longitude=lon,
            active=True,
            is_main=is_first  # Auto-set as main if first location
        )
        db.session.add(location)
        db.session.commit()

        return jsonify(location.to_dict()), 201

    @app.route('/api/locations/<int:location_id>', methods=['DELETE'])
    def api_delete_location(location_id):
        """Remove a location"""
        location = Location.query.get_or_404(location_id)
        was_main = location.is_main
        location.active = False
        location.is_main = False
        db.session.commit()

        # If this was the main location, auto-set another as main
        if was_main:
            remaining = Location.query.filter_by(active=True).first()
            if remaining:
                remaining.is_main = True
                db.session.commit()

        return jsonify({'success': True}), 200

    @app.route('/api/locations/<int:location_id>/set-main', methods=['POST'])
    def api_set_main_location(location_id):
        """Set a location as the main location"""
        location = Location.query.get_or_404(location_id)

        if not location.active:
            return jsonify({'error': 'Cannot set inactive location as main'}), 400

        # Unset all other main locations
        Location.query.filter_by(is_main=True).update({'is_main': False})

        # Set this location as main
        location.is_main = True
        db.session.commit()

        return jsonify({'success': True, 'location': location.to_dict()}), 200

    @app.route('/api/alerts', methods=['GET'])
    def api_get_alerts():
        """Get active alerts"""
        alerts = Alert.query.filter_by(dismissed=False).order_by(Alert.created_at.desc()).all()
        return jsonify({
            'alerts': [alert.to_dict() for alert in alerts]
        }), 200

    @app.route('/api/alerts/<int:alert_id>/dismiss', methods=['POST'])
    def api_dismiss_alert(alert_id):
        """Dismiss an alert"""
        alert = Alert.query.get_or_404(alert_id)
        alert.dismissed = True
        db.session.commit()
        return jsonify({'success': True}), 200

    @app.route('/api/alerts/<int:alert_id>/feedback', methods=['POST'])
    def api_alert_feedback(alert_id):
        """Mark alert as accurate or false alarm (images already saved at creation) and auto-dismiss"""
        alert = Alert.query.get_or_404(alert_id)
        data = request.get_json()
        is_accurate = data.get('accurate', False)

        alert.user_feedback = is_accurate
        alert.feedback_timestamp = datetime.utcnow()
        alert.dismissed = True  # Auto-dismiss when marked
        db.session.commit()

        return jsonify({
            'success': True,
            'feedback': is_accurate
        }), 200

    @app.route('/api/alerts/<int:alert_id>/slack-dismiss', methods=['GET', 'POST'])
    def api_slack_dismiss_alert(alert_id):
        """Dismiss an alert for 30 minutes (Slack button callback)"""
        alert = Alert.query.get_or_404(alert_id)

        # Set slack_dismissed_until to 30 minutes from now
        from datetime import timedelta
        alert.slack_dismissed_until = datetime.utcnow() + timedelta(minutes=30)
        db.session.commit()

        # Return HTML response for browser or JSON for API
        if request.headers.get('Accept', '').startswith('text/html'):
            return '''
                <html>
                <head>
                    <title>Alert Dismissed</title>
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                            background: #f5f5f5;
                        }
                        .message {
                            background: white;
                            padding: 40px;
                            border-radius: 8px;
                            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                            text-align: center;
                        }
                        h1 { color: #0066cc; margin: 0 0 10px 0; }
                        p { color: #666; margin: 10px 0; }
                    </style>
                </head>
                <body>
                    <div class="message">
                        <h1>✓ Alert Dismissed</h1>
                        <p>This alert has been snoozed for 30 minutes.</p>
                        <p>You will be notified again if rain continues.</p>
                    </div>
                </body>
                </html>
            ''', 200
        else:
            return jsonify({'success': True, 'dismissed_until': alert.slack_dismissed_until.isoformat()}), 200

    @app.route('/api/alerts/<int:alert_id>/slack-feedback', methods=['GET', 'POST'])
    def api_slack_feedback_alert(alert_id):
        """Mark alert as accurate or false alarm from Slack and auto-dismiss"""
        alert = Alert.query.get_or_404(alert_id)

        # Get accurate parameter from query string or form data
        is_accurate = request.args.get('accurate', 'false').lower() == 'true'

        alert.user_feedback = is_accurate
        alert.feedback_timestamp = datetime.utcnow()
        alert.dismissed = True  # Auto-dismiss when marked
        db.session.commit()

        feedback_text = "accurate" if is_accurate else "a false alarm"

        # Return HTML response for browser
        if request.headers.get('Accept', '').startswith('text/html') or request.method == 'GET':
            return f'''
                <html>
                <head>
                    <title>Feedback Received</title>
                    <style>
                        body {{
                            font-family: Arial, sans-serif;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                            background: #f5f5f5;
                        }}
                        .message {{
                            background: white;
                            padding: 40px;
                            border-radius: 8px;
                            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                            text-align: center;
                        }}
                        h1 {{ color: {"#0066cc" if is_accurate else "#cc0000"}; margin: 0 0 10px 0; }}
                        p {{ color: #666; margin: 10px 0; }}
                    </style>
                </head>
                <body>
                    <div class="message">
                        <h1>{"✓" if is_accurate else "✗"} Feedback Received</h1>
                        <p>Thank you! You marked this alert as {feedback_text}.</p>
                        <p>The alert has been dismissed.</p>
                    </div>
                </body>
                </html>
            ''', 200
        else:
            return jsonify({'success': True, 'feedback': is_accurate}), 200

    @app.route('/settings', methods=['GET'])
    def settings_page():
        """Settings page for notification configuration"""
        settings = NotificationSettings.query.first()
        if not settings:
            # Create default settings
            settings = NotificationSettings()
            db.session.add(settings)
            db.session.commit()
        return render_template('settings.html', settings=settings)

    @app.route('/api/settings', methods=['GET', 'POST'])
    def api_settings():
        """Get or update notification settings"""
        if request.method == 'GET':
            settings = NotificationSettings.query.first()
            if not settings:
                settings = NotificationSettings()
                db.session.add(settings)
                db.session.commit()
            return jsonify(settings.to_dict()), 200

        # POST - update settings
        data = request.get_json()
        settings = NotificationSettings.query.first()
        if not settings:
            settings = NotificationSettings()
            db.session.add(settings)

        # Update email settings
        settings.email_enabled = data.get('email_enabled', False)
        settings.email_address = data.get('email_address', '').strip() or None
        settings.email_smtp_server = data.get('email_smtp_server', '').strip() or None
        settings.email_smtp_port = data.get('email_smtp_port') or None
        settings.email_smtp_user = data.get('email_smtp_user', '').strip() or None
        settings.email_smtp_password = data.get('email_smtp_password', '').strip() or None

        # Update Slack settings
        settings.slack_enabled = data.get('slack_enabled', False)
        settings.slack_webhook_url = data.get('slack_webhook_url', '').strip() or None

        # Update Telegram settings
        settings.telegram_enabled = data.get('telegram_enabled', False)
        settings.telegram_bot_token = data.get('telegram_bot_token', '').strip() or None
        settings.telegram_chat_id = data.get('telegram_chat_id', '').strip() or None

        db.session.commit()

        return jsonify({'success': True, 'settings': settings.to_dict()}), 200

    @app.route('/api/test-notifications', methods=['POST'])
    def api_test_notifications():
        """Send test notification to all configured channels"""
        settings = NotificationSettings.query.first()
        if not settings:
            return jsonify({'error': 'No settings configured'}), 400

        test_message = "🌧️ Test notification from Rain Alert - Your notifications are working!"

        success = NotificationService.send_alert(settings, test_message)

        if success:
            return jsonify({'success': True, 'message': 'Test notification sent!'}), 200
        else:
            return jsonify({'error': 'No notification methods configured or all failed'}), 400

    @app.route('/api/test-alert', methods=['POST'])
    def api_test_alert():
        """Create a test rain alert for the first active location"""
        location = Location.query.filter_by(active=True).first()
        if not location:
            return jsonify({'error': 'No active locations to test'}), 400

        # Create test alert
        test_alert = Alert(
            location_id=location.id,
            alert_time=datetime.utcnow(),
            rain_expected_at=datetime.utcnow() + timedelta(minutes=20),
            minutes_ahead=20,
            message=f"⚠️ TEST ALERT: Simulated moderate rain expected in {location.address} in 20 minutes (radar-detected, high confidence)",
            dismissed=False
        )
        db.session.add(test_alert)
        db.session.commit()

        # Send notification
        settings = NotificationSettings.query.first()
        if settings:
            NotificationService.send_alert(settings, test_alert.message, test_alert)

        return jsonify({
            'success': True,
            'message': 'Test alert created and notification sent!',
            'alert': test_alert.to_dict()
        }), 201

    @app.route('/api/radar/images', methods=['GET'])
    def api_radar_images():
        """Get list of available radar images"""
        images = RadarService.get_available_images()
        return jsonify({'images': images}), 200

    @app.route('/radar/<filename>')
    def serve_radar_image(filename):
        """Serve a radar image file"""
        radar_dir = RadarService.get_radar_directory()
        return send_from_directory(radar_dir, filename)

    @app.route('/radar-feedback/<filename>')
    def serve_feedback_image(filename):
        """Serve a saved alert radar image"""
        radar_dir = RadarService.get_radar_directory()
        feedback_dir = os.path.join(radar_dir, 'alerts')
        return send_from_directory(feedback_dir, filename)

    @app.route('/health')
    def health():
        """Health check endpoint"""
        return jsonify({'status': 'ok'}), 200
