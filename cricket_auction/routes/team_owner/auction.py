from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('team_owner_auction', __name__, url_prefix='/team-owner/auction')

def get_user_team(cursor, user_id):
    """Get team owned by user — uses passed cursor, no new connection"""
    cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    return cursor.fetchone()

def get_total_teams(cursor, auction_id):
    cursor.execute("SELECT COUNT(*) as total FROM teams WHERE auction_id = %s", (auction_id,))
    result = cursor.fetchone()
    return result['total'] if result else 0

def get_skip_count(cursor, auction_id, auction_player_id):
    cursor.execute("""
        SELECT COUNT(DISTINCT team_id) as skip_count
        FROM player_skips
        WHERE auction_id = %s AND auction_player_id = %s
    """, (auction_id, auction_player_id))
    result = cursor.fetchone()
    return result['skip_count'] if result else 0

def get_min_bid_increment(current_bid):
    if current_bid < 1.0:
        return 0.05
    elif current_bid < 2.0:
        return 0.10
    elif current_bid < 7.0:
        return 0.25
    else:
        return 0.25

@bp.route('/')
def auction_room():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team:
            flash('No team assigned')
            return redirect('/dashboard')
        
        # Get auction from session or find latest live
        auction_id = session.get('active_auction_id')
        auction = None
        
        if auction_id:
            cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
            auction = cursor.fetchone()
        else:
            cursor.execute("SELECT * FROM auctions WHERE status = 'live' ORDER BY id DESC LIMIT 1")
            auction = cursor.fetchone()
        
        total_teams = 0
        if auction:
            total_teams = get_total_teams(cursor, auction['id'])
        
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
        
        current_player = None
        if auction and auction.get('current_player_id'):
            cursor.execute("""
                SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status
                FROM auction_players ap
                JOIN players p ON ap.player_id = p.id
                WHERE ap.id = %s
            """, (auction['current_player_id'],))
            current_player = cursor.fetchone()
        
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
        
        # Own hidden bids (willing prices)
        cursor.execute("""
            SELECT h.*, p.player_name, ap.id as auction_player_id
            FROM hidden_max_bids h
            JOIN auction_players ap ON h.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE h.team_id = %s AND h.is_active = TRUE
        """, (user_team['id'],))
        hidden_bids = cursor.fetchall()
        
        skip_votes = []
        if auction and current_player:
            cursor.execute("""
                SELECT ps.*, t.team_name, u.username as skipped_by_name
                FROM player_skips ps
                JOIN teams t ON ps.team_id = t.id
                JOIN users u ON ps.skipped_by = u.id
                WHERE ps.auction_id = %s AND ps.auction_player_id = %s
                ORDER BY ps.skipped_at DESC
            """, (auction['id'], current_player['auction_player_id']))
            skip_votes = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/auction.html',
        auction=auction,
        players=players,
        current_player=current_player,
        team=user_team,
        public_bids=public_bids,
        hidden_bids=hidden_bids,
        skip_votes=skip_votes,
        total_teams=total_teams
    )

@bp.route('/sessions')
def get_sessions():
    """Get available sessions for this auction"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = request.args.get('auction_id', type=int)
    
    if not auction_id:
        return jsonify({'error': 'Missing auction_id'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify user's team in this auction
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team or user_team['auction_id'] != auction_id:
            return jsonify({'error': 'Not your auction'}), 403
        
        # Get all sessions for this auction
        cursor.execute("""
            SELECT s.* 
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY s.created_at DESC
        """, (auction_id,))
        all_sessions = cursor.fetchall()
        
        # Get all teams for names
        cursor.execute("SELECT id, team_name FROM teams WHERE auction_id = %s", (auction_id,))
        all_teams = {row['id']: row['team_name'] for row in cursor.fetchall()}
        
        # Get total teams count
        total_teams = len(all_teams)
        
        # Process sessions
        processed = []
        for sess in all_sessions:
            team_ids = []
            if sess['team_ids']:
                try:
                    team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                except:
                    team_ids = []
            
            processed.append({
                'id': sess['id'],
                'session_name': sess['session_name'],
                'status': sess['status'],
                'start_time': str(sess['start_time']) if sess['start_time'] else None,
                'end_time': str(sess['end_time']) if sess['end_time'] else None,
                'team_ids_list': team_ids,
                'total_teams': total_teams
            })
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'sessions': processed,
        'my_team_id': user_team['id'],
        'all_teams': all_teams
    })

@bp.route('/join-session/<int:session_id>', methods=['POST'])
def join_session(session_id):
    """Join a session and set session context"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get session
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        sess = cursor.fetchone()
        
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        # Verify user's team
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team or user_team['auction_id'] != sess['auction_id']:
            return jsonify({'error': 'Not your auction'}), 403
        
        # Parse team_ids
        team_ids = []
        if sess['team_ids']:
            try:
                team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                team_ids = []
        
        # Add team if not already in
        if user_team['id'] not in team_ids:
            team_ids.append(user_team['id'])
            cursor.execute("""
                UPDATE auction_sessions SET team_ids = %s WHERE id = %s
            """, (json.dumps(team_ids), session_id))
            db.commit()
        
        # Set session in flask session
        session['active_session_id'] = session_id
        session['active_auction_id'] = sess['auction_id']
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True})

@bp.route('/players')
def get_players():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = request.args.get('auction_id', type=int)
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        if not auction_id:
            cursor.execute("SELECT * FROM auctions WHERE status = 'live' ORDER BY id DESC LIMIT 1")
            auction = cursor.fetchone()
            auction_id = auction['id'] if auction else None
        
        players = []
        if auction_id:
            cursor.execute("""
                SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, ap.sold_price
                FROM players p
                JOIN auction_players ap ON p.id = ap.player_id
                WHERE ap.auction_id = %s AND ap.status IN ('available', 'unsold')
                ORDER BY p.player_name
            """, (auction_id,))
            players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'players': players})

@bp.route('/bid', methods=['POST'])
def place_bid():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    amount = float(data.get('amount', 0))
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team:
            return jsonify({'error': 'No team'}), 400
        
        team_id = user_team['id']
        
        # Check if this team already skipped this player
        cursor.execute("""
            SELECT * FROM player_skips 
            WHERE auction_id = %s AND auction_player_id = %s AND team_id = %s
        """, (auction_id, auction_player_id, team_id))
        existing_skip = cursor.fetchone()
        if existing_skip:
            return jsonify({'error': 'You already skipped this player. Cannot bid.'}), 400
        
        # Check auction status
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if not auction or auction['status'] != 'live':
            return jsonify({'error': 'Auction not live'}), 400
        
        # Get base price from auction_players table
        cursor.execute("SELECT base_price FROM auction_players WHERE id = %s", (auction_player_id,))
        ap_row = cursor.fetchone()
        base_price = float(ap_row['base_price']) if ap_row else float(auction.get('base_price') or 2.0)
        
        # CHECK IF ANY BIDS EXIST FOR THIS PLAYER
        cursor.execute("""
            SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
            FROM bids 
            WHERE auction_id = %s AND auction_player_id = %s
        """, (auction_id, auction_player_id))
        bid_info = cursor.fetchone()
        has_bids = bid_info['bid_count'] > 0
        highest_bid = float(bid_info['highest_bid']) if bid_info['highest_bid'] else 0
        
        # INITIAL BID: No bids placed yet
        if not has_bids:
            if amount < base_price:
                return jsonify({'error': f'Initial bid must be at least base price ₹{base_price:.2f}Cr'}), 400
        
        # SUBSEQUENT BID: Bids already exist
        else:
            current_bid = highest_bid
            
            if amount <= current_bid:
                return jsonify({'error': f'Bid must be higher than current bid ₹{current_bid:.2f}Cr'}), 400
            
            min_increment = get_min_bid_increment(current_bid)
            if amount < current_bid + min_increment:
                return jsonify({'error': f'Bid must be at least ₹{min_increment:.2f}Cr higher than current bid'}), 400
        
        # Check if this team is the current highest bidder
        if has_bids:
            cursor.execute("""
                SELECT team_id FROM bids 
                WHERE auction_id = %s AND auction_player_id = %s 
                ORDER BY bid_amount DESC, created_at DESC LIMIT 1
            """, (auction_id, auction_player_id))
            last_bidder = cursor.fetchone()
            if last_bidder and last_bidder['team_id'] == team_id:
                return jsonify({'error': 'You are already the highest bidder.'}), 400
        
        # Check funds
        available = float(user_team['purse_limit']) - float(user_team['spent'] or 0) - float(user_team['reserved'] or 0)
        
        cursor.execute("""
            SELECT max_bid FROM hidden_max_bids 
            WHERE auction_player_id = %s AND team_id = %s AND is_active = TRUE
        """, (auction_player_id, team_id))
        hidden = cursor.fetchone()
        hidden_amount = float(hidden['max_bid']) if hidden else 0
        
        if amount > available + hidden_amount:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available + hidden_amount:.2f}Cr'}), 400
        
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
        
    finally:
        cursor.close()
        db.close()
    
    # Clear cache after bid
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({'success': True, 'current_bid': amount, 'bidder': user_team['team_name'], 'check_auto': True})

@bp.route('/hidden_bid', methods=['POST'])
def place_hidden_bid():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    max_amount = float(data.get('max_amount', 0))
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team:
            return jsonify({'error': 'No team'}), 400
        
        team_id = user_team['id']
        
        available = float(user_team['purse_limit']) - float(user_team['spent'] or 0) - float(user_team['reserved'] or 0)
        if max_amount > available:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        # Remove existing hidden bid for this player
        cursor.execute("""
            DELETE FROM hidden_max_bids WHERE auction_player_id = %s AND team_id = %s
        """, (auction_player_id, team_id))
        
        # Insert new hidden bid (willing price)
        cursor.execute("""
            INSERT INTO hidden_max_bids (auction_id, auction_player_id, team_id, max_bid, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (auction_id, auction_player_id, team_id, max_amount))
        
        # Update or insert purse reservation
        cursor.execute("""
            SELECT id FROM purse_reservations WHERE team_id = %s AND auction_player_id = %s
        """, (team_id, auction_player_id))
        existing_res = cursor.fetchone()
        
        if existing_res:
            cursor.execute("""
                UPDATE purse_reservations SET reserved_amount = %s WHERE id = %s
            """, (max_amount, existing_res['id']))
        else:
            cursor.execute("""
                INSERT INTO purse_reservations (team_id, auction_player_id, reserved_amount)
                VALUES (%s, %s, %s)
            """, (team_id, auction_player_id, max_amount))
        
        # Update team reserved amount
        cursor.execute("""
            UPDATE teams SET reserved = reserved + %s WHERE id = %s
        """, (max_amount, team_id))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})

@bp.route('/hidden_bid/<int:hidden_id>', methods=['PUT'])
def edit_hidden_bid(hidden_id):
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    new_max = float(data.get('max_amount', 0))
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'])
        
        cursor.execute("SELECT * FROM hidden_max_bids WHERE id = %s AND team_id = %s", (hidden_id, user_team['id']))
        existing = cursor.fetchone()
        if not existing:
            return jsonify({'error': 'Not found'}), 404
        
        cursor.execute("UPDATE hidden_max_bids SET max_bid = %s WHERE id = %s", (new_max, hidden_id))
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True})

@bp.route('/hidden_bid/<int:hidden_id>', methods=['DELETE'])
def cancel_hidden_bid(hidden_id):
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'])
        
        cursor.execute("SELECT * FROM hidden_max_bids WHERE id = %s AND team_id = %s", (hidden_id, user_team['id']))
        hidden = cursor.fetchone()
        if not hidden:
            return jsonify({'error': 'Not found'}), 404
        
        cursor.execute("""
            UPDATE teams SET reserved = reserved - %s WHERE id = %s
        """, (hidden['max_bid'], user_team['id']))
        
        cursor.execute("DELETE FROM hidden_max_bids WHERE id = %s", (hidden_id,))
        cursor.execute("DELETE FROM purse_reservations WHERE team_id = %s AND auction_player_id = %s", 
                       (user_team['id'], hidden['auction_player_id']))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True})

@bp.route('/skip', methods=['POST'])
def skip_player():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    reason = data.get('reason', 'no_bids')
    notes = data.get('notes', '')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team:
            return jsonify({'error': 'No team assigned'}), 400
        
        team_id = user_team['id']
        user_id = session['user_id']
        
        # Check if already skipped
        cursor.execute("""
            SELECT * FROM player_skips 
            WHERE auction_id = %s AND auction_player_id = %s AND team_id = %s
        """, (auction_id, auction_player_id, team_id))
        existing = cursor.fetchone()
        if existing:
            return jsonify({'error': 'You already skipped this player'}), 400
        
        # Check if this team is the current highest bidder
        cursor.execute("""
            SELECT team_id FROM bids 
            WHERE auction_id = %s AND auction_player_id = %s 
            ORDER BY bid_amount DESC, created_at DESC LIMIT 1
        """, (auction_id, auction_player_id))
        last_bid = cursor.fetchone()
        
        if last_bid and last_bid['team_id'] == team_id:
            return jsonify({'error': 'You are the current highest bidder. Cannot skip this player.'}), 400
        
        # Insert skip record
        cursor.execute("""
            INSERT INTO player_skips (auction_id, auction_player_id, player_id, reason, notes, skipped_by, team_id)
            SELECT %s, %s, ap.player_id, %s, %s, %s, %s
            FROM auction_players ap
            WHERE ap.id = %s
        """, (auction_id, auction_player_id, reason, notes, user_id, team_id, auction_player_id))
        
        cursor.execute("""
            UPDATE auction_players 
            SET skip_reason = %s, skip_notes = %s 
            WHERE id = %s
        """, (reason, notes, auction_player_id))
        
        db.commit()
        
        skip_count = get_skip_count(cursor, auction_id, auction_player_id)
        total_teams = get_total_teams(cursor, auction_id)
        all_skipped = skip_count >= total_teams and total_teams > 0
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'success': True,
        'skip_count': skip_count,
        'total_teams': total_teams,
        'all_skipped': all_skipped,
        'message': f'Skipped ({skip_count}/{total_teams} teams)'
    })

@bp.route('/skip_status/<int:auction_player_id>')
def get_skip_status(auction_player_id):
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT ps.*, t.team_name, u.username as skipped_by_name
            FROM player_skips ps
            JOIN teams t ON ps.team_id = t.id
            JOIN users u ON ps.skipped_by = u.id
            WHERE ps.auction_player_id = %s
            ORDER BY ps.skipped_at DESC
        """, (auction_player_id,))
        skips = cursor.fetchall()
        
        auction_id = None
        if skips:
            auction_id = skips[0]['auction_id']
        
        total_teams = get_total_teams(cursor, auction_id) if auction_id else 0
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'skips': skips,
        'skip_count': len(skips),
        'total_teams': total_teams,
        'all_skipped': len(skips) >= total_teams
    })

@bp.route('/notifications')
def get_notifications():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team:
            return jsonify({'notifications': []})
        
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
        
    finally:
        cursor.close()
        db.close()
    
    notifications = []
    for bid in bids:
        notifications.append({
            'type': 'bid',
            'message': f"You bid ₹{float(bid['bid_amount']):.2f}Cr on {bid['player_name']}",
            'time': str(bid['created_at'])
        })
    
    return jsonify({'notifications': notifications})