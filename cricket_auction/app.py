import os
from datetime import timedelta
from flask import Flask

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cricket-auction-secret-2024')

# Client-side sessions — fast, no server storage needed, works on Render
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

# Remove Flask-Session — not needed for simple session data
app.url_map.strict_slashes = False

# === REGISTER BLUEPRINTS ===

# Auth routes (login/logout)
import routes.auth as auth
app.register_blueprint(auth.bp)

# === ADMIN ROUTES (Auctioneer) ===
import routes.admin.dashboard as admin_dashboard
import routes.admin.auction as admin_auctions
import routes.admin.teams as admin_teams
import routes.admin.players as admin_players
import routes.admin.sessions as admin_sessions
import routes.admin.reports as admin_reports

app.register_blueprint(admin_dashboard.bp)
app.register_blueprint(admin_auctions.bp)
app.register_blueprint(admin_teams.bp)
app.register_blueprint(admin_players.bp)
app.register_blueprint(admin_sessions.bp)
app.register_blueprint(admin_reports.bp)

# === TEAM OWNER ROUTES ===
import routes.team_owner.dashboard as team_owner_dashboard
import routes.team_owner.auction as team_owner_auction
import routes.team_owner.squad as team_owner_squad
import routes.team_owner.playing11 as team_owner_playing11

app.register_blueprint(team_owner_dashboard.bp)
app.register_blueprint(team_owner_auction.bp)
app.register_blueprint(team_owner_squad.bp)
app.register_blueprint(team_owner_playing11.bp)

# === VIEWER ROUTES ===
import routes.viewer.dashboard as viewer_dashboard
import routes.viewer.auction as viewer_auction
import routes.viewer.teams as viewer_teams

app.register_blueprint(viewer_dashboard.bp)
app.register_blueprint(viewer_auction.bp)
app.register_blueprint(viewer_teams.bp)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5005)))