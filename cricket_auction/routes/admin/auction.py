from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
import mysql.connector

bp = Blueprint('admin_auction', __name__, url_prefix='/admin')

def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='raahul@185',
        database='cricket_auction'
    )

def get_user_team(user_id, auction_id=None):
    """Get team owned by user"""
    db = get_db()
    cursor = db.cursor(dictionary=True)
    if auction_id:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s AND auction_id = %s", (user_id, auction_id))
    else:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    team = cursor.fetchone()
    cursor.close()
    db.close()
    return team

@bp.route('/auction')
def auction_room():
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        flash('Unauthorized')
        return redirect('/')
    
    # Check if there's an active auction context
    auction_id = session.get('active_auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Get auction - either from session or most recent
    if auction_id:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
    else:
        cursor.execute("SELECT * FROM auctions ORDER BY id DESC LIMIT 1")
        auction = cursor.fetchone()
        if auction:
            session['active_auction_id'] = auction['id']
            session['active_league_name'] = auction['league_name']
    
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
    
    # Get teams for this auction
    teams = []
    if auction:
        cursor.execute("SELECT * FROM teams WHERE auction_id = %s", (auction['id'],))
        teams = cursor.fetchall()
    
    # Get user's team (for team_owner role)
    user_team = None
    if auction and session.get('role') == 'team_owner':
        user_team = get_user_team(session['user_id'], auction['id'])
    
    # Get sessions count
    sessions_count = 0
    if auction:
        cursor.execute("SELECT COUNT(*) as count FROM auction_sessions WHERE auction_id = %s", (auction['id'],))
        result = cursor.fetchone()
        sessions_count = result['count'] if result else 0
    
    # Current player
    current_player = None
    current_bid = 0
    if auction and auction.get('current_player_id'):
        cursor.execute("""
            SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            WHERE ap.id = %s
        """, (auction['current_player_id'],))
        current_player = cursor.fetchone()
        current_bid = float(auction.get('current_bid') or 0)
    
    cursor.close()
    db.close()
    
    return render_template('admin/auction.html', 
        auction=auction, 
        players=players, 
        teams=teams,
        user_team=user_team,
        sessions_count=sessions_count,
        current_player=current_player,
        current_bid=current_bid
    )

@bp.route('/auction/start', methods=['POST'])
def start_auction():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        flash('Unauthorized')
        return redirect('/admin/auction')
    
    league_name = request.form.get('league_name', 'IPL Auction 2024')
    
    db = get_db()
    cursor = db.cursor()
    
    # Create auction
    cursor.execute(
        "INSERT INTO auctions (league_name, status, created_by) VALUES (%s, 'live', %s)",
        (league_name, session['user_id'])
    )
    db.commit()
    auction_id = cursor.lastrowid
    
    # IMPORTANT: Create auction_players records for all players
    cursor.execute("SELECT id, base_price FROM players")
    all_players = cursor.fetchall()
    
    for player in all_players:
        cursor.execute("""
            INSERT INTO auction_players (auction_id, player_id, base_price, status)
            VALUES (%s, %s, %s, 'available')
            ON DUPLICATE KEY UPDATE auction_id = auction_id
        """, (auction_id, player['id'], player.get('base_price', 2.00)))
    
    db.commit()
    cursor.close()
    db.close()
    
    flash('Auction started!')
    return jsonify({'success': True, 'auction_id': auction_id})

@bp.route('/auction/pause', methods=['POST'])
def pause_auction():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE auctions SET status = 'paused' WHERE status = 'live'")
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'status': 'paused', 'success': True})

@bp.route('/auction/resume', methods=['POST'])
def resume_auction():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE auctions SET status = 'live' WHERE status = 'paused'")
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'status': 'live', 'success': True})

@bp.route('/auction/sell', methods=['POST'])
def sell_player():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Get auction details
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    
    # Check if there are any bids before selling
    if not auction or not auction.get('current_bidder_id') or float(auction.get('current_bid') or 0) <= 0:
        cursor.close()
        db.close()
        return jsonify({'error': 'No active bid - cannot sell. Player goes unsold.'}), 400
    
    team_id = auction['current_bidder_id']
    sold_price = auction['current_bid']
    
    # Get auction_player record
    cursor.execute("SELECT * FROM auction_players WHERE id = %s AND auction_id = %s", (auction_player_id, auction_id))
    ap = cursor.fetchone()
    
    if not ap:
        cursor.close()
        db.close()
        return jsonify({'error': 'Player not in auction'}), 404
    
    # Update auction_player status
    cursor.execute(
        "UPDATE auction_players SET status = 'sold', sold_team_id = %s, sold_price = %s WHERE id = %s",
        (team_id, sold_price, auction_player_id)
    )
    
    # Update team spent
    cursor.execute(
        "UPDATE teams SET spent = spent + %s WHERE id = %s",
        (sold_price, team_id)
    )
    
    # Add to team_players
    cursor.execute(
        "INSERT INTO team_players (team_id, auction_player_id, purchase_price) VALUES (%s, %s, %s)",
        (team_id, auction_player_id, sold_price)
    )
    
    # Update auction - clear current player
    cursor.execute(
        "UPDATE auctions SET current_player_id = NULL, current_bid = 0, current_bidder_id = NULL WHERE id = %s",
        (auction_id,)
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True, 'sold_to': team_id, 'price': sold_price})

@bp.route('/auction/unsold', methods=['POST'])
def mark_unsold():
    """Mark player as unsold when no bids received"""
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Get auction_player record
    cursor.execute("SELECT * FROM auction_players WHERE id = %s AND auction_id = %s", (auction_player_id, auction_id))
    ap = cursor.fetchone()
    
    if not ap:
        cursor.close()
        db.close()
        return jsonify({'error': 'Player not in auction'}), 404
    
    # Mark as unsold
    cursor.execute(
        "UPDATE auction_players SET status = 'unsold' WHERE id = %s",
        (auction_player_id,)
    )
    
    # Clear auction current player
    cursor.execute(
        "UPDATE auctions SET current_player_id = NULL, current_bid = 0, current_bidder_id = NULL WHERE id = %s",
        (auction_id,)
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True, 'status': 'unsold'})

@bp.route('/auction/undo', methods=['POST'])
def undo_sale():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_player_id = data.get('auction_player_id')
    auction_id = data.get('auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Get auction_player details
    cursor.execute("SELECT * FROM auction_players WHERE id = %s AND auction_id = %s", (auction_player_id, auction_id))
    ap = cursor.fetchone()
    
    if not ap or ap['status'] != 'sold':
        cursor.close()
        db.close()
        return jsonify({'error': 'Player not sold'}), 400
    
    # Refund team
    cursor.execute(
        "UPDATE teams SET spent = spent - %s WHERE id = %s",
        (ap['sold_price'], ap['sold_team_id'])
    )
    
    # Reset auction_player
    cursor.execute(
        "UPDATE auction_players SET status = 'available', sold_team_id = NULL, sold_price = NULL WHERE id = %s",
        (ap['id'],)
    )
    
    # Remove from team_players
    cursor.execute(
        "DELETE FROM team_players WHERE team_id = %s AND auction_player_id = %s",
        (ap['sold_team_id'], ap['id'])
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True})

@bp.route('/auction/select_player', methods=['POST'])
def select_player():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    
    print(f"DEBUG select_player: auction_id={auction_id}, auction_player_id={auction_player_id}")
    
    if not auction_id:
        return jsonify({'error': 'Missing auction_id'}), 400
    
    try:
        auction_player_id = int(auction_player_id) if auction_player_id else None
        auction_id = int(auction_id)
    except (ValueError, TypeError):
        return jsonify({'error': f'Invalid IDs: auction_id={auction_id}, auction_player_id={auction_player_id}'}), 400
    
    if not auction_player_id:
        return jsonify({'error': 'Missing auction_player_id'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Check if auction exists
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    if not auction:
        cursor.close()
        db.close()
        return jsonify({'error': f'Auction {auction_id} not found'}), 404
    
    # Check the auction_players record
    cursor.execute("""
        SELECT ap.*, p.player_name, p.category, p.overseas
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        WHERE ap.id = %s AND ap.auction_id = %s
    """, (auction_player_id, auction_id))
    player = cursor.fetchone()
    
    if not player:
        # Debug: show what auction_players exist
        cursor.execute("""
            SELECT ap.id, ap.auction_id, ap.player_id, p.player_name, ap.status 
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            WHERE ap.auction_id = %s
        """, (auction_id,))
        all_ap = cursor.fetchall()
        
        # Also check if this auction_player_id exists at all
        cursor.execute("""
            SELECT ap.id, ap.auction_id, p.player_name 
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            WHERE ap.id = %s
        """, (auction_player_id,))
        wrong_auction = cursor.fetchone()
        
        cursor.close()
        db.close()
        
        debug_info = {
            'requested_auction_player_id': auction_player_id,
            'requested_auction_id': auction_id,
            'available_in_this_auction': [{'id': a['id'], 'name': a['player_name']} for a in all_ap],
            'wrong_auction_match': dict(wrong_auction) if wrong_auction else None
        }
        print(f"DEBUG select_player FAIL: {debug_info}")
        return jsonify({
            'error': 'Player not found in this auction',
            'debug': debug_info
        }), 404
    
    # Update auction with selected player
    cursor.execute("""
        UPDATE auctions 
        SET current_player_id = %s, current_bid = %s, current_bidder_id = NULL
        WHERE id = %s
    """, (auction_player_id, player['base_price'], auction_id))
    
    db.commit()
    cursor.close()
    db.close()
    
    print(f"DEBUG select_player SUCCESS: {player['player_name']} sent to auction {auction_id}")
    
    return jsonify({
        'success': True,
        'player_name': player['player_name'],
        'category': player['category'],
        'base_price': float(player['base_price']),
        'auction_player_id': auction_player_id,
        'overseas': player.get('overseas', False)
    })

@bp.route('/auction/status')
def get_status():
    auction_id = request.args.get('auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    if auction_id:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    else:
        cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
    
    auction = cursor.fetchone()
    
    if not auction:
        cursor.close()
        db.close()
        return jsonify({'status': 'none'})
    
    result = {
        'status': auction['status'],
        'league_name': auction.get('league_name'),
        'auction_id': auction['id'],
        'current_bid': float(auction.get('current_bid') or 0),
        'current_bidder': None,
        'current_player': None,
        'player_category': None,
        'base_price': 0,
        'auction_player_id': None,
        'overseas': False
    }
    
    # Get current bidder name
    if auction.get('current_bidder_id'):
        cursor.execute("SELECT team_name FROM teams WHERE id = %s", (auction['current_bidder_id'],))
        bidder = cursor.fetchone()
        if bidder:
            result['current_bidder'] = bidder['team_name']
    
    # Get current player details using auction_players.id
    if auction.get('current_player_id'):
        cursor.execute("""
            SELECT p.player_name, p.category, p.overseas, ap.base_price, ap.id as auction_player_id
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            WHERE ap.id = %s
        """, (auction['current_player_id'],))
        player = cursor.fetchone()
        if player:
            result['current_player'] = player['player_name']
            result['player_category'] = player['category']
            result['base_price'] = float(player['base_price'])
            result['auction_player_id'] = player['auction_player_id']
            result['overseas'] = player.get('overseas', False)
    
    cursor.close()
    db.close()
    
    return jsonify(result)

@bp.route('/auction/bid', methods=['POST'])
def place_bid():
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    team_id = data.get('team_id')
    amount = float(data.get('amount', 0))
    
    user_role = session.get('role')
    
    # Only team owners are restricted to their own team
    if user_role == 'team_owner':
        user_team = get_user_team(session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only bid for your own team'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Check team exists
    cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
    team = cursor.fetchone()
    
    if not team:
        cursor.close()
        db.close()
        return jsonify({'error': 'Team not found'}), 404
    
    # Check hidden max bids for this team on this player
    cursor.execute(
        "SELECT max_bid FROM hidden_max_bids WHERE auction_player_id = %s AND team_id = %s AND is_active = TRUE",
        (auction_player_id, team_id)
    )
    hidden = cursor.fetchone()
    hidden_amount = float(hidden['max_bid']) if hidden else 0
    
    available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
    
    if amount > available + hidden_amount:
        cursor.close()
        db.close()
        return jsonify({'error': f'Insufficient funds. Available: ₹{available + hidden_amount:.2f}Cr'}), 400
    
    # Check current bid
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    
    if auction['status'] != 'live':
        cursor.close()
        db.close()
        return jsonify({'error': 'Auction not live'}), 400
    
    current_bid = float(auction.get('current_bid') or 0)
    if amount <= current_bid:
        cursor.close()
        db.close()
        return jsonify({'error': 'Bid must be higher than current bid'}), 400
    
    # Place bid
    cursor.execute(
        "INSERT INTO bids (auction_id, auction_player_id, team_id, bid_amount) VALUES (%s, %s, %s, %s)",
        (auction_id, auction_player_id, team_id, amount)
    )
    
    # Update auction
    cursor.execute(
        "UPDATE auctions SET current_bid = %s, current_bidder_id = %s, current_player_id = %s WHERE id = %s",
        (amount, team_id, auction_player_id, auction_id)
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({
        'success': True, 
        'current_bid': amount, 
        'bidder': team['team_name'],
        'check_auto': True
    })

@bp.route('/auction/hidden_bid', methods=['POST'])
def place_hidden_bid():
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    team_id = data.get('team_id')
    max_amount = float(data.get('max_amount', 0))
    
    # Admin/auctioneer can set for any team, team_owner only for own team
    user_team = get_user_team(session['user_id'], auction_id)
    if session.get('role') == 'team_owner' and (not user_team or user_team['id'] != team_id):
        return jsonify({'error': 'You can only set hidden bids for your own team'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Check team purse
    cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
    team = cursor.fetchone()
    
    if not team:
        cursor.close()
        db.close()
        return jsonify({'error': 'Team not found'}), 404
    
    available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
    
    if max_amount > available:
        cursor.close()
        db.close()
        return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
    
    # Remove existing hidden bid for this player+team
    cursor.execute(
        "DELETE FROM hidden_max_bids WHERE auction_player_id = %s AND team_id = %s",
        (auction_player_id, team_id)
    )
    
    # Insert new hidden max bid
    cursor.execute(
        "INSERT INTO hidden_max_bids (auction_id, auction_player_id, team_id, max_bid) VALUES (%s, %s, %s, %s)",
        (auction_id, auction_player_id, team_id, max_amount)
    )
    
    # Add purse reservation
    cursor.execute(
        "INSERT INTO purse_reservations (team_id, auction_player_id, reserved_amount) VALUES (%s, %s, %s)",
        (team_id, auction_player_id, max_amount)
    )
    
    # Update team reserved
    cursor.execute(
        "UPDATE teams SET reserved = reserved + %s WHERE id = %s",
        (max_amount, team_id)
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})

@bp.route('/auction/auto_bid', methods=['POST'])
def auto_counter_bid():
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    current_bid = float(data.get('current_bid', 0))
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Find active hidden max bids higher than current bid
    cursor.execute("""
        SELECT h.*, t.team_name, t.purse_limit, t.spent, t.reserved 
        FROM hidden_max_bids h 
        JOIN teams t ON h.team_id = t.id 
        WHERE h.auction_player_id = %s AND h.is_active = TRUE AND h.max_bid > %s
        ORDER BY h.max_bid DESC
    """, (auction_player_id, current_bid))
    
    hidden_bids = cursor.fetchall()
    
    if not hidden_bids:
        cursor.close()
        db.close()
        return jsonify({'auto_bid': False})
    
    winner = hidden_bids[0]
    next_bid = current_bid + 0.5
    if next_bid > winner['max_bid']:
        next_bid = winner['max_bid']
    
    # Check available funds
    cursor.execute(
        "SELECT reserved_amount FROM purse_reservations WHERE auction_player_id = %s AND team_id = %s AND status = 'active'",
        (auction_player_id, winner['team_id'])
    )
    res = cursor.fetchone()
    reserved_amount = float(res['reserved_amount']) if res else 0
    
    available = float(winner['purse_limit']) - float(winner['spent'] or 0) - (float(winner['reserved'] or 0) - reserved_amount)
    
    if next_bid > available + reserved_amount:
        cursor.close()
        db.close()
        return jsonify({'auto_bid': False, 'reason': 'Insufficient funds'})
    
    # Place auto bid
    cursor.execute(
        "INSERT INTO bids (auction_id, auction_player_id, team_id, bid_amount) VALUES (%s, %s, %s, %s)",
        (auction_id, auction_player_id, winner['team_id'], next_bid)
    )
    
    # Update auction
    cursor.execute(
        "UPDATE auctions SET current_bid = %s, current_bidder_id = %s, current_player_id = %s WHERE id = %s",
        (next_bid, winner['team_id'], auction_player_id, auction_id)
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({
        'auto_bid': True,
        'team': winner['team_name'],
        'amount': next_bid
    })