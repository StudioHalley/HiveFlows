"""
HiveFlows — Landing page backend
Stocke les inscriptions bêta dans subscribers.yaml
"""

import os
import re
from datetime import datetime, timezone

import yaml
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")

SUBSCRIBERS_FILE = os.path.join(os.path.dirname(__file__), "subscribers.yaml")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_subscribers() -> list[dict]:
    """Charge la liste des inscrits depuis le fichier YAML."""
    if not os.path.exists(SUBSCRIBERS_FILE):
        return []
    with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    return data if isinstance(data, list) else []


def save_subscribers(subscribers: list[dict]) -> None:
    """Sauvegarde la liste dans le fichier YAML."""
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(subscribers, f, allow_unicode=True, sort_keys=False)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Sert la landing page."""
    return send_from_directory(".", "index.html")


@app.route("/subscribe", methods=["POST"])
def subscribe():
    """
    Enregistre un nouvel inscrit.
    Body JSON attendu : { "email": "...", "lang": "fr"|"en" }
    Codes de retour :
        200  — inscrit avec succès
        400  — email invalide ou manquant
        409  — email déjà enregistré
        500  — erreur serveur
    """
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    lang  = body.get("lang", "fr")

    # Validation
    if not email or not EMAIL_RE.match(email):
        return jsonify({"error": "invalid_email"}), 400

    try:
        subscribers = load_subscribers()

        # Vérifier si déjà inscrit
        existing_emails = {s["email"].lower() for s in subscribers}
        if email in existing_emails:
            return jsonify({"error": "already_registered"}), 409

        # Ajouter le nouvel inscrit
        subscribers.append({
            "email":      email,
            "lang":       lang,
            "subscribed_at": datetime.now(timezone.utc).isoformat(),
        })
        save_subscribers(subscribers)

        print(f"[HiveFlows] Nouvel inscrit : {email} ({lang})")
        return jsonify({"status": "ok", "count": len(subscribers)}), 200

    except Exception as exc:
        print(f"[HiveFlows] Erreur : {exc}")
        return jsonify({"error": "server_error"}), 500


@app.route("/admin/subscribers", methods=["GET"])
def list_subscribers():
    """
    Liste tous les inscrits (usage interne — à protéger en prod).
    Exemple : GET /admin/subscribers
    """
    subscribers = load_subscribers()
    return jsonify({
        "count":       len(subscribers),
        "subscribers": subscribers,
    })


# ── Démarrage ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"HiveFlows backend démarré sur http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
