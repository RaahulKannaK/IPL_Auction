from flask import Blueprint, render_template, session, flash, redirect
from database.db import get_db

bp = Blueprint('viewer_teams', __name__, url_prefix='/viewer/teams')


@bp.route('/')
def view_teams():
    if session.get('role') != 'viewer':
        flash('Unauthorized')
        return redirect('/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT t.*, u.username as owner_name
        FROM teams t
        LEFT JOIN users u ON t.owner_id = u.id
    """)
    teams = cursor.fetchall()
    
    for team in teams:
        cursor.execute("""
            SELECT p.*, tp.purchase_price as sold_price
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE tp.team_id = %s
            ORDER BY tp.purchase_price DESC
        """, (team['id'],))
        team['squad'] = cursor.fetchall()
        team['spent'] = float(team['spent'] or 0)
    
    cursor.close()
    db.close()
    
    return render_template('viewer/teams.html', teams=teams)