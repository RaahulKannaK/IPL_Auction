from flask import Flask, session
from flask_session import Session

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SESSION_TYPE'] = 'filesystem'

Session(app)

# Import routes
from routes import auth, auction, team, player

app.register_blueprint(auth.bp)
app.register_blueprint(auction.bp)
app.register_blueprint(team.bp)
app.register_blueprint(player.bp)

if __name__ == '__main__':
    app.run(debug=True)