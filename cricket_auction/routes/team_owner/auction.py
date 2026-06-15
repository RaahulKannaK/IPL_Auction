from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('team_owner_auction', __name__, url_prefix='/team-owner/auction')

def get_user_team(cursor, user_id, auction_id=None):
    """Get team owned by user"""
    if auction_id:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s AND auction_id = %s", (user_id, auction_id))
    else:
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
    """Main auction page - always shows session selector first, then auction room"""
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    # Get auction_id from session
    auction_id = session.get('active_auction_id')
    if not auction_id:
        flash('No active auction selected')
        return redirect('/team-owner/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify user's team in this auction
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team:
            flash('You do not own a team in this auction')
            return redirect('/team-owner/dashboard')
        
        # Get auction details
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            flash('Auction not found')
            return redirect('/team-owner/dashboard')
        
        total_teams = get_total_teams(cursor, auction_id)
        
        # ============================================
        # FETCH SESSIONS FOR THIS AUCTION - KEY FIX
        # ============================================
        cursor.execute("""
            SELECT s.* 
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY 
                CASE s.status 
                    WHEN 'active' THEN 1 
                    WHEN 'paused' THEN 2 
                    ELSE 3 
                END,
                s.created_at DESC
        """, (auction_id,))
        auction_sessions = cursor.fetchall()
        
        # Process sessions for template
        processed_sessions = []
        for sess in auction_sessions:
            team_ids = []
            if sess['team_ids']:
                try:
                    team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                    team_ids = [int(tid) for tid in team_ids]
                except:
                    team_ids = []
            
            processed_sessions.append({
                'id': sess['id'],
                'session_name': sess['session_name'],
                'status': sess['status'],
                'start_time': str(sess['start_time']) if sess['start_time'] else None,
                'end_time': str(sess['end_time']) if sess['end_time'] else None,
                'team_ids_list': team_ids,
                'total_teams': total_teams
            })
        
        # Get all teams for names
        cursor.execute("SELECT id, team_name FROM teams WHERE auction_id = %s", (auction_id,))
        all_teams = {row['id']: row['team_name'] for row in cursor.fetchall()}
        
        # Get currently selected session if any
        active_session_id = session.get('active_session_id')
        current_session = None
        
        if active_session_id:
            cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (active_session_id,))
            current_session = cursor.fetchone()
        
        # Get players for this auction
        cursor.execute("""
            SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, ap.sold_price
            FROM players p
            JOIN auction_players ap ON p.id = ap.player_id
            WHERE ap.auction_id = %s AND ap.status IN ('available', 'unsold')
            ORDER BY RAND()
        """, (auction_id,))
        players = cursor.fetchall()
        
        # Get current player if any
        current_player = None
        if auction.get('current_player_id'):
            cursor.execute("""
                SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status
                FROM auction_players ap
                JOIN players p ON ap.player_id = p.id
                WHERE ap.id = %s
            """, (auction['current_player_id'],))
            current_player = cursor.fetchone()
        
        # Get public bids
        cursor.execute("""
            SELECT b.*, p.player_name, t.team_name
            FROM bids b
            JOIN auction_players ap ON b.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            JOIN teams t ON b.team_id = t.id
            WHERE b.auction_id = %s
            ORDER BY b.created_at DESC
            LIMIT 20
        """, (auction_id,))
        public_bids = cursor.fetchall()
        
        # Own hidden bids
        cursor.execute("""
            SELECT h.*, p.player_name, ap.id as auction_player_id
            FROM hidden_max_bids h
            JOIN auction_players ap ON h.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE h.team_id = %s AND h.is_active = TRUE
        """, (user_team['id'],))
        hidden_bids = cursor.fetchall()
        
        # Skip votes
        skip_votes = []
        if current_player:
            cursor.execute("""
                SELECT ps.*, t.team_name, u.username as skipped_by_name
                FROM player_skips ps
                JOIN teams t ON ps.team_id = t.id
                JOIN users u ON ps.skipped_by = u.id
                WHERE ps.auction_id = %s AND ps.auction_player_id = %s
                ORDER BY ps.skipped_at DESC
            """, (auction_id, current_player['auction_player_id']))
            skip_votes = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/auction.html',
        auction=auction,
        auction_id=auction_id,
        auction_sessions=processed_sessions,
        current_session=current_session,
        all_teams=all_teams,
        players=players,
        current_player=current_player,
        team=user_team,
        public_bids=public_bids,
        hidden_bids=hidden_bids,
        skip_votes=skip_votes,
        total_teams=total_teams
    )

# ==================== SESSION MANAGEMENT ====================

@bp.route('/sessions')
def get_sessions():
    """Get all sessions for this auction"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Get auction_id from session (set when entering from dashboard)
    auction_id = session.get('active_auction_id')
    
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify user's team in this auction
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team:
            return jsonify({'error': 'Not your auction'}), 403
        
        # Get ALL sessions for this auction (active, paused, completed)
        cursor.execute("""
            SELECT s.* 
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY 
                CASE s.status 
                    WHEN 'active' THEN 1 
                    WHEN 'paused' THEN 2 
                    ELSE 3 
                END,
                s.created_at DESC
        """, (auction_id,))
        all_sessions = cursor.fetchall()
        
        # Get all teams for names
        cursor.execute("SELECT id, team_name FROM teams WHERE auction_id = %s", (auction_id,))
        all_teams = {str(row['id']): row['team_name'] for row in cursor.fetchall()}
        
        # Get total teams count
        total_teams = len(all_teams)
        
        # Process sessions
        processed = []
        for sess in all_sessions:
            team_ids = []
            if sess['team_ids']:
                try:
                    team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                    # Ensure all IDs are integers for comparison
                    team_ids = [int(tid) for tid in team_ids]
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
        'all_teams': all_teams,
        'auction_id': auction_id
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
        user_team = get_user_team(cursor, session['user_id'], sess['auction_id'])
        if not user_team:
            return jsonify({'error': 'Not your auction'}), 403
        
        # Parse team_ids
        team_ids = []
        if sess['team_ids']:
            try:
                team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                team_ids = [int(tid) for tid in team_ids]
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
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True})

# ==================== PLAYERS & BIDDING ====================

@bp.route('/players')
def get_players():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Use auction_id from session
    auction_id = session.get('active_auction_id')
    
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
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
    auction_id = data.get('auction_id') or session.get('active_auction_id')
    auction_player_id = data.get('auction_player_id')
    amount = float(data.get('amount', 0))
    
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'], auction_id)
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
        if not auction or auction['status'] not in ['live', 'paused']:
            return jsonify({'error': 'Auction not live'}), 400
        
        # Get base price
        cursor.execute("SELECT base_price FROM auction_players WHERE id = %s", (auction_player_id,))
        ap_row = cursor.fetchone()
        base_price = float(ap_row['base_price']) if ap_row else 2.0
        
        # Check existing bids
        cursor.execute("""
            SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
            FROM bids 
            WHERE auction_id = %s AND auction_player_id = %s
        """, (auction_id, auction_player_id))
        bid_info = cursor.fetchone()
        has_bids = bid_info['bid_count'] > 0
        highest_bid = float(bid_info['highest_bid']) if bid_info['highest_bid'] else 0
        
        # Validate bid amount
        if not has_bids:
            if amount < base_price:
                return jsonify({'error': f'Initial bid must be at least base price ₹{base_price:.2f}Cr'}), 400
        else:
            current_bid = highest_bid
            if amount <= current_bid:
                return jsonify({'error': f'Bid must be higher than current bid ₹{current_bid:.2f}Cr'}), 400
            min_increment = get_min_bid_increment(current_bid)
            if amount < current_bid + min_increment:
                return jsonify({'error': f'Bid must be at least ₹{min_increment:.2f}Cr higher'}), 400
        
        # Check if already highest bidder
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
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({'success': True, 'current_bid': amount, 'bidder': user_team['team_name'], 'check_auto': True})

@bp.route('/hidden_bid', methods=['POST'])
def place_hidden_bid():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id') or session.get('active_auction_id')
    auction_player_id = data.get('auction_player_id')
    max_amount = float(data.get('max_amount', 0))
    
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team:
            return jsonify({'error': 'No team'}), 400
        
        team_id = user_team['id']
        available = float(user_team['purse_limit']) - float(user_team['spent'] or 0) - float(user_team['reserved'] or 0)
        
        if max_amount > available:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        cursor.execute("DELETE FROM hidden_max_bids WHERE auction_player_id = %s AND team_id = %s", (auction_player_id, team_id))
        cursor.execute("""
            INSERT INTO hidden_max_bids (auction_id, auction_player_id, team_id, max_bid, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (auction_id, auction_player_id, team_id, max_amount))
        
        cursor.execute("SELECT id FROM purse_reservations WHERE team_id = %s AND auction_player_id = %s", (team_id, auction_player_id))
        existing_res = cursor.fetchone()
        
        if existing_res:
            cursor.execute("UPDATE purse_reservations SET reserved_amount = %s WHERE id = %s", (max_amount, existing_res['id']))
        else:
            cursor.execute("INSERT INTO purse_reservations (team_id, auction_player_id, reserved_amount) VALUES (%s, %s, %s)", (team_id, auction_player_id, max_amount))
        
        cursor.execute("UPDATE teams SET reserved = reserved + %s WHERE id = %s", (max_amount, team_id))
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})

@bp.route('/skip', methods=['POST'])
def skip_player():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id') or session.get('active_auction_id')
    auction_player_id = data.get('auction_player_id')
    reason = data.get('reason', 'no_bids')
    notes = data.get('notes', '')
    
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team:
            return jsonify({'error': 'No team assigned'}), 400
        
        team_id = user_team['id']
        user_id = session['user_id']
        
        cursor.execute("""
            SELECT * FROM player_skips 
            WHERE auction_id = %s AND auction_player_id = %s AND team_id = %s
        """, (auction_id, auction_player_id, team_id))
        if cursor.fetchone():
            return jsonify({'error': 'You already skipped this player'}), 400
        
        cursor.execute("""
            SELECT team_id FROM bids 
            WHERE auction_id = %s AND auction_player_id = %s 
            ORDER BY bid_amount DESC, created_at DESC LIMIT 1
        """, (auction_id, auction_player_id))
        last_bid = cursor.fetchone()
        
        if last_bid and last_bid['team_id'] == team_id:
            return jsonify({'error': 'You are the current highest bidder. Cannot skip.'}), 400
        
        cursor.execute("""
            INSERT INTO player_skips (auction_id, auction_player_id, player_id, reason, notes, skipped_by, team_id)
            SELECT %s, %s, ap.player_id, %s, %s, %s, %s
            FROM auction_players ap
            WHERE ap.id = %s
        """, (auction_id, auction_player_id, reason, notes, user_id, team_id, auction_player_id))
        
        cursor.execute("UPDATE auction_players SET skip_reason = %s, skip_notes = %s WHERE id = %s", (reason, notes, auction_player_id))
        db.commit()
        
        skip_count = get_skip_count(cursor, auction_id, auction_player_id)
        total_teams = get_total_teams(cursor, auction_id)
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'success': True,
        'skip_count': skip_count,
        'total_teams': total_teams,
        'all_skipped': skip_count >= total_teams,
        'message': f'Skipped ({skip_count}/{total_teams} teams)'
    })