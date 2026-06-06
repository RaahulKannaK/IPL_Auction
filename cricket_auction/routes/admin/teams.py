from flask import Blueprint, render_template, request, redirect, session, flash, jsonify


from database.db import get_db

bp = Blueprint('admin_teams', __name__, url_prefix='/admin/teams')



@bp.route('/')
def list_teams():
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.*, u.username as owner_name, a.league_name
        FROM teams t 
        LEFT JOIN users u ON t.owner_id = u.id
        LEFT JOIN auctions a ON t.auction_id = a.id
    """)
    teams = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('admin/teams.html', teams=teams)

@bp.route('/create', methods=['POST'])
def create_team():
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/admin/teams')
    
    team_name = request.form['team_name']
    auction_id = request.form.get('auction_id', 1)
    purse_limit = request.form.get('purse_limit', 100)
    
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
    return redirect('/admin/teams')

@bp.route('/edit/<int:id>', methods=['POST'])
def edit_team(id):
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    team_name = request.form['team_name']
    purse_limit = request.form.get('purse_limit')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE teams SET team_name=%s, purse_limit=%s WHERE id=%s",
        (team_name, purse_limit, id)
    )
    db.commit()
    cursor.close()
    db.close()
    
    flash('Team updated!')
    return redirect('/admin/teams')

@bp.route('/assign_owner/<int:team_id>', methods=['POST'])
def assign_owner(team_id):
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    user_id = request.form['user_id']
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE teams SET owner_id = %s WHERE id = %s", (user_id, team_id))
    db.commit()
    cursor.close()
    db.close()
    
    flash('Owner assigned!')
    return redirect('/admin/teams')

@bp.route('/remove_owner/<int:team_id>', methods=['POST'])
def remove_owner(team_id):
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE teams SET owner_id = NULL WHERE id = %s", (team_id,))
    db.commit()
    cursor.close()
    db.close()
    
    flash('Owner removed!')
    return redirect('/admin/teams')

@bp.route('/purse/<int:team_id>')
def view_purse(team_id):
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
    team = cursor.fetchone()
    cursor.close()
    db.close()
    
    return jsonify({
        'team_name': team['team_name'],
        'purse_limit': float(team['purse_limit']),
        'spent': float(team['spent'] or 0),
        'reserved': float(team['reserved'] or 0),
        'available': float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
    })

@bp.route('/squad/<int:team_id>')
def view_squad(team_id):
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
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