#!/usr/bin/env python3
"""
Rain Alert App - Development Server Entry Point
"""
import os
from app import create_app

# Create Flask application
app = create_app()

if __name__ == "__main__":
    # Get host and port from environment or use defaults
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", 5000))

    print(f"\n🌧️  Rain Alert Starting...")
    print(f"Access: http://{host}:{port}")
    print("Press CTRL+C to quit\n")

    # Run development server (use_reloader=False to avoid scheduler issues)
    app.run(host=host, port=port, debug=True, use_reloader=False)
