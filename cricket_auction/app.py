from flask import Flask, session
from flask_session import Session

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = 'cricket-auction-secret-2024'
app.config['SESSION_TYPE'] = 'filesystem'

Session(app)

# === AUTH ROUTES ===
import routes.auth as auth
app.register_blueprint(auth.bp)

# === ADMIN ROUTES ===
import routes.admin.dashboard as admin_dashboard
import routes.admin.auction as admin_auctions
import routes.admin.teams as admin_teams
import routes.admin.players as admin_players
import routes.admin.sessions as admin_sessions
import routes.admin.reports as admin_reports

app.url_map.strict_slashes = False

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
    app.run(debug=True, host='0.0.0.0', port=5005)