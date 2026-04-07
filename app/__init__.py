"""
Flask application factory for Rain Alert
"""
from flask import Flask
from app.models import db
import os


def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)

    # Configuration
    basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(basedir, 'data', 'rain_alert.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', f'sqlite:///{db_path}')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Initialize extensions
    db.init_app(app)

    # Create tables
    with app.app_context():
        db.create_all()

    # Register routes
    from app.routes import register_routes
    register_routes(app)

    # Start background scheduler
    from app.scheduler import start_scheduler
    start_scheduler(app)

    return app
