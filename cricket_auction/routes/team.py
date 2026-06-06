from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db

bp = Blueprint('team', __name__, url_prefix='/teams')



@bp.route('/')
def list_teams():
    if not session.get('user_id'):
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.*, u.username as owner_name 
        FROM teams t 
        LEFT JOIN users u ON t.owner_id = u.id
    """)
    teams = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('teams.html', teams=teams)

@bp.route('/create', methods=['POST'])
def create_team():
    if session.get('role') not in ['owner', 'admin']:
        flash('Unauthorized')
        return redirect('/teams')
    
    team_name = request.form['team_name']
    purse_limit = request.form.get('purse_limit', 100)
    auction_id = request.form.get('auction_id', 1)
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO teams (auction_id, team_name, purse_limit) VALUES (%s, %s, %s)",
        (auction_id, team_name, purse_limit)
    )
    db.commit()
    cursor.close()
    db.close()
    flash('Team created!')
    return redirect('/teams')

@bp.route('/assign_owner', methods=['POST'])
def assign_owner():
    if session.get('role') not in ['owner', 'admin']:
        flash('Unauthorized')
        return redirect('/teams')
    
    team_id = request.form['team_id']
    user_id = request.form['user_id']
    
    db = get_db()
    cursor = db.cursor()
    
    # Update team owner - schema uses owner_id in teams table
    cursor.execute("UPDATE teams SET owner_id = %s WHERE id = %s", (user_id, team_id))
    
    # Schema has no team_id in users table - skip that update
    
    db.commit()
    cursor.close()
    db.close()
    
    flash('Owner assigned successfully!')
    return redirect('/teams')

@bp.route('/available_owners')
def available_owners():
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    # Schema has no team_id in users table - find users not assigned as owner in any team
    cursor.execute("""
        SELECT u.id, u.username, u.role 
        FROM users u 
        WHERE u.role IN ('team_owner', 'viewer')
        AND u.id NOT IN (SELECT owner_id FROM teams WHERE owner_id IS NOT NULL)
    """)
    users = cursor.fetchall()
    cursor.close()
    db.close()
    
    return jsonify({'users': users})

@bp.route('/squad/<int:team_id>')
def view_squad(team_id):
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    # Join through team_players -> auction_players -> players
    cursor.execute("""
        SELECT p.*, tp.purchase_price as sold_price
        FROM team_players tp
        JOIN auction_players ap ON tp.auction_player_id = ap.id
        JOIN players p ON ap.player_id = p.id
        WHERE tp.team_id = %s
        ORDER BY tp.purchase_price DESC
    """, (team_id,))
    players = cursor.fetchall()
    cursor.close()
    db.close()
    
    return jsonify({'players': players})