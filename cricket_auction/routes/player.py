from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db


bp = Blueprint('player', __name__, url_prefix='/players')



@bp.route('/')
def list_players():
    if not session.get('user_id'):
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Join with auction_players to get status and base_price, order by player_name
    cursor.execute("""
        SELECT p.*, ap.base_price, ap.status, ap.sold_price, t.team_name
        FROM players p
        LEFT JOIN auction_players ap ON p.id = ap.player_id
        LEFT JOIN teams t ON ap.sold_team_id = t.id
        ORDER BY p.player_name
    """)
    players = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('players.html', players=players)

@bp.route('/create', methods=['POST'])
def create_player():
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/players')
    
    player_name = request.form['player_name']
    category = request.form['category']
    overseas = request.form.get('overseas') == 'on'
    
    db = get_db()
    cursor = db.cursor()
    
    # Insert into players table only (master player database)
    cursor.execute(
        "INSERT INTO players (player_name, category, overseas) VALUES (%s, %s, %s)",
        (player_name, category, overseas)
    )
    db.commit()
    cursor.close()
    db.close()
    flash('Player added to master database!')
    return redirect('/players')

@bp.route('/edit/<int:id>', methods=['POST'])
def edit_player(id):
    if session.get('role') not in ['owner', 'admin']:
        flash('Unauthorized')
        return redirect('/players')
    
    player_name = request.form['player_name']
    category = request.form['category']
    overseas = request.form.get('overseas') == 'on'
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE players SET player_name=%s, category=%s, overseas=%s WHERE id=%s",
        (player_name, category, overseas, id)
    )
    db.commit()
    cursor.close()
    db.close()
    flash('Player updated!')
    return redirect('/players')

@bp.route('/delete/<int:id>', methods=['POST'])
def delete_player(id):
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Check if player is sold in any auction
    cursor.execute("SELECT status FROM auction_players WHERE player_id = %s AND status = 'sold'", (id,))
    sold = cursor.fetchone()
    
    if sold:
        return jsonify({'error': 'Cannot delete sold player'}), 400
    
    # Check if player exists in any auction
    cursor.execute("SELECT id FROM auction_players WHERE player_id = %s", (id,))
    in_auction = cursor.fetchone()
    
    if in_auction:
        # Remove from auction_players first (foreign key constraint)
        cursor.execute("DELETE FROM auction_players WHERE player_id = %s", (id,))
    
    # Delete from master players table
    cursor.execute("DELETE FROM players WHERE id = %s", (id,))
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True})

@bp.route('/')
def auction_room():
    if not session.get('user_id'):
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Get current auction
    cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
    auction = cursor.fetchone()
    
    # Get players - ALL players from auction_players for this auction, or all players if no auction
    players = []
    if auction:
        cursor.execute("""
            SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, ap.sold_price
            FROM players p
            JOIN auction_players ap ON p.id = ap.player_id
            WHERE ap.auction_id = %s
            ORDER BY RAND()
        """, (auction['id'],))
        players = cursor.fetchall()
    else:
        # No active auction - show all available players from auction_players
        cursor.execute("""
            SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, ap.sold_price
            FROM players p
            JOIN auction_players ap ON p.id = ap.player_id
            WHERE ap.status = 'available'
            ORDER BY RAND()
        """)
        players = cursor.fetchall()
    
    # Get teams for current auction or all teams
    teams = []
    if auction:
        cursor.execute("SELECT * FROM teams WHERE auction_id = %s", (auction['id'],))
        teams = cursor.fetchall()
    else:
        cursor.execute("SELECT * FROM teams")
        teams = cursor.fetchall()
    
    # Get user's team
    user_team = None
    if auction:
        user_team = get_user_team(session['user_id'], auction['id'])
    else:
        user_team = get_user_team(session['user_id'])
    
    # Get sessions count for display
    sessions_count = 0
    if auction:
        cursor.execute("SELECT COUNT(*) as count FROM auction_sessions WHERE auction_id = %s", (auction['id'],))
        sessions_count = cursor.fetchone()['count']
    
    cursor.close()
    db.close()
    
    return render_template('auction.html', 
        auction=auction, 
        players=players, 
        teams=teams,
        user_team=user_team,
        sessions_count=sessions_count
    )