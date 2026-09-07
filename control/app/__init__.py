from flask import Flask, jsonify

from app.admin.routes import bp as admin_bp
from app.config import Config
from app.db import close_conn
from app.webhooks.routes import bp as webhooks_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    app.register_blueprint(admin_bp)
    app.register_blueprint(webhooks_bp)
    app.teardown_appcontext(close_conn)

    @app.get("/health")
    def health():
        return jsonify(status="ok", service="opssquad-control")

    return app
