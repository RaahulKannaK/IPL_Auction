from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
import mysql.connector

bp = Blueprint('team_owner_auction', __name__, url_prefix='/team-owner/auction')

def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='raahul@185',
        database='cricket_auction'
    )

def get_user_team(user_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    team = cursor.fetchone()
    cursor.close()
    db.close()
    return team

@bp.route('/')
def auction_room():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/dashboard')
    
    user_team = get_user_team(session['user_id'])
    if not user_team:
        flash('No team assigned')
        return redirect('/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Get live auction
    cursor.execute("SELECT * FROM auctions WHERE status = 'live' ORDER BY id DESC LIMIT 1")
    auction = cursor.fetchone()
    
    # Get only available/unsold players for this auction
    players = []
    if auction:
        cursor.execute("""
            SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, ap.sold_price
            FROM players p
            JOIN auction_players ap ON p.id = ap.player_id
            WHERE ap.auction_id = %s AND ap.status IN ('available', 'unsold')
            ORDER BY RAND()
        """, (auction['id'],))
        players = cursor.fetchall()
    
    # Current player
    current_player = None
    if auction and auction.get('current_player_id'):
        cursor.execute("""
            SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            WHERE ap.id = %s
        """, (auction['current_player_id'],))
        current_player = cursor.fetchone()
    
    # Public bid history
    cursor.execute("""
        SELECT b.*, p.player_name, t.team_name
        FROM bids b
        JOIN auction_players ap ON b.auction_player_id = ap.id
        JOIN players p ON ap.player_id = p.id
        JOIN teams t ON b.team_id = t.id
        WHERE b.auction_id = %s
        ORDER BY b.created_at DESC
        LIMIT 20
    """, (auction['id'],) if auction else (0,))
    public_bids = cursor.fetchall()
    
    # Own hidden bids
    cursor.execute("""
        SELECT h.*, p.player_name
        FROM hidden_max_bids h
        JOIN auction_players ap ON h.auction_player_id = ap.id
        JOIN players p ON ap.player_id = p.id
        WHERE h.team_id = %s AND h.is_active = TRUE
    """, (user_team['id'],))
    hidden_bids = cursor.fetchall()
    
    cursor.close()
    db.close()
    
    return render_template('team_owner/auction.html',
        auction=auction,
        players=players,
        current_player=current_player,
        team=user_team,
        public_bids=public_bids,
        hidden_bids=hidden_bids
    )

@bp.route('/bid', methods=['POST'])
def place_bid():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    amount = float(data.get('amount', 0))
    
    user_team = get_user_team(session['user_id'])
    if not user_team:
        return jsonify({'error': 'No team'}), 400
    
    team_id = user_team['id']
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Check auction status
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    if not auction or auction['status'] != 'live':
        cursor.close()
        db.close()
        return jsonify({'error': 'Auction not live'}), 400
    
    # Check funds
    available = float(user_team['purse_limit']) - float(user_team['spent'] or 0) - float(user_team['reserved'] or 0)
    
    cursor.execute("""
        SELECT max_bid FROM hidden_max_bids 
        WHERE auction_player_id = %s AND team_id = %s AND is_active = TRUE
    """, (auction_player_id, team_id))
    hidden = cursor.fetchone()
    hidden_amount = float(hidden['max_bid']) if hidden else 0
    
    if amount > available + hidden_amount:
        cursor.close()
        db.close()
        return jsonify({'error': f'Insufficient funds. Available: ₹{available + hidden_amount:.2f}Cr'}), 400
    
    current_bid = float(auction.get('current_bid') or 0)
    if amount <= current_bid:
        cursor.close()
        db.close()
        return jsonify({'error': 'Bid must be higher'}), 400
    
    # Place bid
    cursor.execute("""
        INSERT INTO bids (auction_id, auction_player_id, team_id, bid_amount)
        VALUES (%s, %s, %s, %s)
    """, (auction_id, auction_player_id, team_id, amount))
    
    cursor.execute("""
        UPDATE auctions SET current_bid = %s, current_bidder_id = %s, current_player_id = %s
        WHERE id = %s
    """, (amount, team_id, auction_player_id, auction_id))
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True, 'current_bid': amount, 'bidder': user_team['team_name']})

@bp.route('/hidden_bid', methods=['POST'])
def place_hidden_bid():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    max_amount = float(data.get('max_amount', 0))
    
    user_team = get_user_team(session['user_id'])
    if not user_team:
        return jsonify({'error': 'No team'}), 400
    
    team_id = user_team['id']
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    available = float(user_team['purse_limit']) - float(user_team['spent'] or 0) - float(user_team['reserved'] or 0)
    if max_amount > available:
        cursor.close()
        db.close()
        return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
    
    # Remove existing
    cursor.execute("""
        DELETE FROM hidden_max_bids WHERE auction_player_id = %s AND team_id = %s
    """, (auction_player_id, team_id))
    
    # Insert new
    cursor.execute("""
        INSERT INTO hidden_max_bids (auction_id, auction_player_id, team_id, max_bid)
        VALUES (%s, %s, %s, %s)
    """, (auction_id, auction_player_id, team_id, max_amount))
    
    # Reserve purse
    cursor.execute("""
        INSERT INTO purse_reservations (team_id, auction_player_id, reserved_amount)
        VALUES (%s, %s, %s)
    """, (team_id, auction_player_id, max_amount))
    
    cursor.execute("""
        UPDATE teams SET reserved = reserved + %s WHERE id = %s
    """, (max_amount, team_id))
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})

@bp.route('/hidden_bid/<int:hidden_id>', methods=['PUT'])
def edit_hidden_bid(hidden_id):
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    new_max = float(data.get('max_amount', 0))
    
    user_team = get_user_team(session['user_id'])
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM hidden_max_bids WHERE id = %s AND team_id = %s", (hidden_id, user_team['id']))
    existing = cursor.fetchone()
    if not existing:
        cursor.close()
        db.close()
        return jsonify({'error': 'Not found'}), 404
    
    # Update
    cursor.execute("UPDATE hidden_max_bids SET max_bid = %s WHERE id = %s", (new_max, hidden_id))
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True})

@bp.route('/hidden_bid/<int:hidden_id>', methods=['DELETE'])
def cancel_hidden_bid(hidden_id):
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    user_team = get_user_team(session['user_id'])
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM hidden_max_bids WHERE id = %s AND team_id = %s", (hidden_id, user_team['id']))
    hidden = cursor.fetchone()
    if not hidden:
        cursor.close()
        db.close()
        return jsonify({'error': 'Not found'}), 404
    
    # Release reservation
    cursor.execute("""
        UPDATE teams SET reserved = reserved - %s WHERE id = %s
    """, (hidden['max_bid'], user_team['id']))
    
    cursor.execute("DELETE FROM hidden_max_bids WHERE id = %s", (hidden_id,))
    cursor.execute("DELETE FROM purse_reservations WHERE team_id = %s AND auction_player_id = %s", 
                   (user_team['id'], hidden['auction_player_id']))
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True})

@bp.route('/notifications')
def get_notifications():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    user_team = get_user_team(session['user_id'])
    if not user_team:
        return jsonify({'notifications': []})
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Outbid notifications
    cursor.execute("""
        SELECT b.*, p.player_name
        FROM bids b
        JOIN auction_players ap ON b.auction_player_id = ap.id
        JOIN players p ON ap.player_id = p.id
        WHERE b.team_id = %s
        ORDER BY b.created_at DESC
        LIMIT 10
    """, (user_team['id'],))
    bids = cursor.fetchall()
    
    notifications = []
    for bid in bids:
        notifications.append({
            'type': 'bid',
            'message': f"You bid ₹{float(bid['bid_amount']):.2f}Cr on {bid['player_name']}",
            'time': str(bid['created_at'])
        })
    
    cursor.close()
    db.close()
    
    return jsonify({'notifications': notifications})