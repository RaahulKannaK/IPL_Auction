from flask import Blueprint, render_template, request, redirect, session, flash, jsonify, url_for
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

def get_skip_count(cursor, auction_id, session_player_id):
    cursor.execute("""
        SELECT COUNT(DISTINCT team_id) as skip_count
        FROM player_skips
        WHERE auction_id = %s AND session_player_id = %s
    """, (auction_id, session_player_id))
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

def get_session_icon(session_name):
    name_lower = (session_name or '').lower()
    if 'morning' in name_lower:
        return '🌅'
    elif 'evening' in name_lower or 'night' in name_lower:
        return '🌙'
    elif 'weekend' in name_lower:
        return '🏖️'
    elif 'afternoon' in name_lower:
        return '☀️'
    elif 'final' in name_lower or 'mega' in name_lower:
        return '👑'
    else:
        return '📅'

def get_session_time_label(session_name, start_time, end_time):
    name_lower = (session_name or '').lower()
    if 'morning' in name_lower:
        return '🌅 Morning Session'
    elif 'evening' in name_lower or 'night' in name_lower:
        return '🌙 Evening Session'
    elif 'weekend' in name_lower:
        return '🏖️ Weekend Session'
    elif 'afternoon' in name_lower:
        return '☀️ Afternoon Session'
    else:
        time_str = ''
        if start_time and end_time:
            time_str = f' ({start_time} - {end_time})'
        return f'📅 Session{time_str}'

def get_join_button_text(session_name, is_member):
    if is_member:
        return '🚀 Enter Auction Room'
    name_lower = (session_name or '').lower()
    if 'morning' in name_lower:
        return '➕ Join Morning Session'
    elif 'evening' in name_lower or 'night' in name_lower:
        return '➕ Join Evening Session'
    elif 'weekend' in name_lower:
        return '➕ Join Weekend Session'
    elif 'afternoon' in name_lower:
        return '➕ Join Afternoon Session'
    else:
        return '➕ Join Session'

def parse_team_ids(team_ids_raw):
    """Safely parse team_ids from JSON string or list, return list of INTEGERS"""
    if not team_ids_raw:
        return []
    try:
        if isinstance(team_ids_raw, str):
            parsed = json.loads(team_ids_raw)
        else:
            parsed = team_ids_raw
        return [int(tid) for tid in parsed] if parsed else []
    except (json.JSONDecodeError, ValueError, TypeError):
        return []

# ============================================================
# FIXED: Route that dashboard calls - sets active_auction_id
# ============================================================
@bp.route('/auction-room/<int:auction_id>')
def enter_auction_room(auction_id):
    """Entry point from dashboard - sets session and redirects to main auction page"""
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    # FIXED: Set the active auction in session
    session['active_auction_id'] = auction_id
    
    # FIXED: Clear any stale session state
    session.pop('active_session_id', None)
    
    return redirect(url_for('team_owner_auction.auction_room'))

# ============================================================
# FIXED: Main auction page - handles both direct access and redirected access
# ============================================================
@bp.route('/')
def auction_room():
    """Main auction page - shows session selector first, then auction room after joining"""
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    # FIXED: Try to get auction_id from multiple sources
    auction_id = session.get('active_auction_id')
    
    # If not in session, check if user has a team and get their auction
    if not auction_id:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        try:
            user_team = get_user_team(cursor, session['user_id'])
            if user_team:
                auction_id = user_team['auction_id']
                session['active_auction_id'] = auction_id
        finally:
            cursor.close()
            db.close()
    
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
            session.pop('active_auction_id', None)
            return redirect('/team-owner/dashboard')
        
        # Get auction details
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            flash('Auction not found')
            return redirect('/team-owner/dashboard')
        
        total_teams = get_total_teams(cursor, auction_id)
        user_team_id = int(user_team['id'])
        
        # ============================================
        # FETCH ALL SESSIONS FOR THIS AUCTION
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
        
        # Process sessions with enriched data
        processed_sessions = []
        for sess in auction_sessions:
            team_ids = parse_team_ids(sess['team_ids'])
            is_member = user_team_id in team_ids
            slots_left = total_teams - len(team_ids)
            
            processed_sessions.append({
                'id': sess['id'],
                'session_name': sess['session_name'] or f'Session {sess["id"]}',
                'status': sess['status'],
                'start_time': str(sess['start_time'])[:16] if sess['start_time'] else None,
                'end_time': str(sess['end_time'])[:16] if sess['end_time'] else None,
                'team_ids_list': team_ids,
                'total_teams': total_teams,
                'slots_left': max(0, slots_left),
                'slots_filled': len(team_ids),
                'is_member': is_member,
                'session_icon': get_session_icon(sess['session_name']),
                'time_label': get_session_time_label(sess['session_name'], sess['start_time'], sess['end_time']),
                'join_button_text': get_join_button_text(sess['session_name'], is_member),
                'created_at': str(sess['created_at']) if sess['created_at'] else None
            })
        
        # Get all teams for names lookup
        cursor.execute("SELECT id, team_name FROM teams WHERE auction_id = %s", (auction_id,))
        all_teams = {int(row['id']): row['team_name'] for row in cursor.fetchall()}
        
        # Check if user already has an active session selected
        active_session_id = session.get('active_session_id')
        has_active_session = False
        is_member_session = False
        current_session = None
        
        if active_session_id:
            cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (active_session_id,))
            current_session = cursor.fetchone()
            if current_session:
                sess_team_ids = parse_team_ids(current_session['team_ids'])
                is_member_session = user_team_id in sess_team_ids
                # Only active if session is live/paused AND user is member
                has_active_session = is_member_session and current_session['status'] in ['active', 'paused']
                # If session is closed/completed, clear it
                if not has_active_session:
                    session.pop('active_session_id', None)
                    active_session_id = None
        
        # ============================================
        # FIXED: Get players for THIS SESSION ONLY (like admin)
        # ============================================
        players = []
        if has_active_session and active_session_id:
            cursor.execute("""
                SELECT p.*, sp.id as session_player_id, sp.base_price, sp.status, sp.sold_price
                FROM players p
                JOIN session_players sp ON p.id = sp.player_id
                WHERE sp.session_id = %s AND sp.status IN ('available', 'unsold')
                ORDER BY p.player_name
            """, (active_session_id,))
            players = cursor.fetchall()
        
        # Get current player if any
        current_player = None
        if auction.get('current_player_id'):
            cursor.execute("""
                SELECT p.*, sp.id as session_player_id, sp.base_price, sp.status
                FROM session_players sp
                JOIN players p ON sp.player_id = p.id
                WHERE sp.id = %s
            """, (auction['current_player_id'],))
            current_player = cursor.fetchone()
        
        # Get public bids for this auction
        cursor.execute("""
            SELECT b.*, p.player_name, t.team_name
            FROM bids b
            JOIN session_players sp ON b.session_player_id = sp.id
            JOIN players p ON sp.player_id = p.id
            JOIN teams t ON b.team_id = t.id
            WHERE b.auction_id = %s
            ORDER BY b.created_at DESC
            LIMIT 20
        """, (auction_id,))
        public_bids = cursor.fetchall()
        
        # Own hidden bids
        cursor.execute("""
            SELECT h.*, p.player_name, sp.id as session_player_id
            FROM hidden_max_bids h
            JOIN session_players sp ON h.session_player_id = sp.id
            JOIN players p ON sp.player_id = p.id
            WHERE h.team_id = %s AND h.is_active = TRUE
        """, (user_team_id,))
        hidden_bids = cursor.fetchall()
        
        # Skip votes for current player
        skip_votes = []
        if current_player:
            cursor.execute("""
                SELECT ps.*, t.team_name, u.username as skipped_by_name
                FROM player_skips ps
                JOIN teams t ON ps.team_id = t.id
                JOIN users u ON ps.skipped_by = u.id
                WHERE ps.auction_id = %s AND ps.session_player_id = %s
                ORDER BY ps.skipped_at DESC
            """, (auction_id, current_player['session_player_id']))
            skip_votes = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/auction.html',
        auction=auction,
        auction_id=auction_id,
        auction_sessions=processed_sessions,
        has_active_session=has_active_session,
        is_member_session=is_member_session,
        active_session_id=active_session_id,
        current_session=current_session,
        all_teams=all_teams,
        players=players,
        current_player=current_player,
        team=user_team,
        public_bids=public_bids,
        hidden_bids=hidden_bids,
        skip_votes=skip_votes,
        total_teams=total_teams,
        user_team_id=user_team_id
    )


# ==================== SESSION MANAGEMENT ====================

@bp.route('/sessions')
def get_sessions():
    """Get all sessions for this auction (API endpoint)"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = session.get('active_auction_id')
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team:
            return jsonify({'error': 'Not your auction'}), 403
        
        user_team_id = int(user_team['id'])
        
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
        
        cursor.execute("SELECT id, team_name FROM teams WHERE auction_id = %s", (auction_id,))
        all_teams = {int(row['id']): row['team_name'] for row in cursor.fetchall()}
        
        total_teams = len(all_teams)
        
        processed = []
        for sess in all_sessions:
            team_ids = parse_team_ids(sess['team_ids'])
            is_member = user_team_id in team_ids
            
            processed.append({
                'id': sess['id'],
                'session_name': sess['session_name'] or f'Session {sess["id"]}',
                'status': sess['status'],
                'start_time': str(sess['start_time'])[:16] if sess['start_time'] else None,
                'end_time': str(sess['end_time'])[:16] if sess['end_time'] else None,
                'team_ids_list': team_ids,
                'total_teams': total_teams,
                'slots_left': max(0, total_teams - len(team_ids)),
                'slots_filled': len(team_ids),
                'is_member': is_member,
                'session_icon': get_session_icon(sess['session_name']),
                'time_label': get_session_time_label(sess['session_name'], sess['start_time'], sess['end_time']),
                'join_button_text': get_join_button_text(sess['session_name'], is_member),
            })
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'sessions': processed,
        'my_team_id': user_team_id,
        'all_teams': all_teams,
        'auction_id': auction_id
    })



@bp.route('/leave-session', methods=['POST'])
def leave_session():
    """Leave current session - clears active session but keeps team in session"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Only clear the active session from Flask session
    # Team remains in session's team_ids for rejoining later
    if 'active_session_id' in session:
        session.pop('active_session_id', None)
    
    return jsonify({'success': True})


# ==================== PLAYERS & BIDDING ====================

@bp.route('/players')
def get_players():
    """Get players for current session"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = session.get('active_auction_id')
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    session_id = request.args.get('session_id') or session.get('active_session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        if session_id:
            cursor.execute("""
                SELECT p.*, sp.id as session_player_id, sp.base_price, sp.status, sp.sold_price
                FROM players p
                JOIN session_players sp ON p.id = sp.player_id
                WHERE sp.session_id = %s AND sp.status IN ('available', 'unsold')
                ORDER BY p.player_name
            """, (session_id,))
        else:
            cursor.execute("""
                SELECT p.*, sp.id as session_player_id, sp.base_price, sp.status, sp.sold_price
                FROM players p
                JOIN session_players sp ON p.id = sp.player_id
                WHERE sp.session_id IN (
                    SELECT id FROM auction_sessions WHERE auction_id = %s
                ) AND sp.status IN ('available', 'unsold')
                ORDER BY p.player_name
            """, (auction_id,))
        players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'players': players})


@bp.route('/bid', methods=['POST'])
def place_bid():
    """Place a bid on a player"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id') or session.get('active_auction_id')
    session_player_id = data.get('session_player_id')
    session_id = data.get('session_id') or session.get('active_session_id')
    amount = float(data.get('amount', 0))
    
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    if not session_player_id:
        return jsonify({'error': 'No player selected'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team:
            return jsonify({'error': 'No team'}), 400
        
        team_id = int(user_team['id'])
        
        # Check if already skipped
        cursor.execute("""
            SELECT * FROM player_skips 
            WHERE auction_id = %s AND session_player_id = %s AND team_id = %s
        """, (auction_id, session_player_id, team_id))
        existing_skip = cursor.fetchone()
        if existing_skip:
            return jsonify({'error': 'You already skipped this player. Cannot bid.'}), 400
        
        # Check auction status
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if not auction or auction['status'] not in ['live', 'paused']:
            return jsonify({'error': 'Auction not live'}), 400
        
        # Get base price from session_players
        cursor.execute("SELECT base_price FROM session_players WHERE id = %s", (session_player_id,))
        sp_row = cursor.fetchone()
        base_price = float(sp_row['base_price']) if sp_row else 2.0
        
        # Check current bids
        cursor.execute("""
            SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
            FROM bids 
            WHERE auction_id = %s AND session_player_id = %s
        """, (auction_id, session_player_id))
        bid_info = cursor.fetchone()
        has_bids = bid_info['bid_count'] > 0
        highest_bid = float(bid_info['highest_bid']) if bid_info['highest_bid'] else 0
        
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
                WHERE auction_id = %s AND session_player_id = %s 
                ORDER BY bid_amount DESC, created_at DESC LIMIT 1
            """, (auction_id, session_player_id))
            last_bidder = cursor.fetchone()
            if last_bidder and int(last_bidder['team_id']) == team_id:
                return jsonify({'error': 'You are already the highest bidder.'}), 400
        
        # Check funds
        available = float(user_team['purse_limit']) - float(user_team['spent'] or 0) - float(user_team['reserved'] or 0)
        cursor.execute("""
            SELECT max_bid FROM hidden_max_bids 
            WHERE session_player_id = %s AND team_id = %s AND is_active = TRUE
        """, (session_player_id, team_id))
        hidden = cursor.fetchone()
        hidden_amount = float(hidden['max_bid']) if hidden else 0
        
        if amount > available + hidden_amount:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available + hidden_amount:.2f}Cr'}), 400
        
        # Insert bid
        cursor.execute("""
            INSERT INTO bids (auction_id, session_player_id, team_id, bid_amount, session_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (auction_id, session_player_id, team_id, amount, session_id))
        
        # Update auction state
        cursor.execute("""
            UPDATE auctions SET current_bid = %s, current_bidder_id = %s, current_player_id = %s
            WHERE id = %s
        """, (amount, team_id, session_player_id, auction_id))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'success': True, 
        'current_bid': amount, 
        'bidder': user_team['team_name'], 
        'check_auto': True
    })


@bp.route('/hidden_bid', methods=['POST'])
def place_hidden_bid():
    """Set a hidden max bid (willing price)"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id') or session.get('active_auction_id')
    session_player_id = data.get('session_player_id')
    max_amount = float(data.get('max_amount', 0))
    
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team:
            return jsonify({'error': 'No team'}), 400
        
        team_id = int(user_team['id'])
        available = float(user_team['purse_limit']) - float(user_team['spent'] or 0) - float(user_team['reserved'] or 0)
        
        if max_amount > available:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        # Delete old hidden bid
        cursor.execute(
            "DELETE FROM hidden_max_bids WHERE session_player_id = %s AND team_id = %s", 
            (session_player_id, team_id)
        )
        
        # Insert new hidden bid
        cursor.execute("""
            INSERT INTO hidden_max_bids (auction_id, session_player_id, team_id, max_bid, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (auction_id, session_player_id, team_id, max_amount))
        
        # Update reservation
        cursor.execute(
            "SELECT id FROM purse_reservations WHERE team_id = %s AND session_player_id = %s", 
            (team_id, session_player_id)
        )
        existing_res = cursor.fetchone()
        
        if existing_res:
            cursor.execute(
                "UPDATE purse_reservations SET reserved_amount = %s WHERE id = %s", 
                (max_amount, existing_res['id'])
            )
        else:
            cursor.execute("""
                INSERT INTO purse_reservations (team_id, session_player_id, reserved_amount) 
                VALUES (%s, %s, %s)
            """, (team_id, session_player_id, max_amount))
        
        cursor.execute(
            "UPDATE teams SET reserved = reserved + %s WHERE id = %s", 
            (max_amount, team_id)
        )
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})


@bp.route('/skip', methods=['POST'])
def skip_player():
    """Skip a player"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id') or session.get('active_auction_id')
    session_player_id = data.get('session_player_id')
    session_id = data.get('session_id') or session.get('active_session_id')
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
        
        team_id = int(user_team['id'])
        user_id = session['user_id']
        
        # Check if already skipped
        cursor.execute("""
            SELECT * FROM player_skips 
            WHERE auction_id = %s AND session_player_id = %s AND team_id = %s
        """, (auction_id, session_player_id, team_id))
        if cursor.fetchone():
            return jsonify({'error': 'You already skipped this player'}), 400
        
        # Check if highest bidder
        cursor.execute("""
            SELECT team_id FROM bids 
            WHERE auction_id = %s AND session_player_id = %s 
            ORDER BY bid_amount DESC, created_at DESC LIMIT 1
        """, (auction_id, session_player_id))
        last_bid = cursor.fetchone()
        
        if last_bid and int(last_bid['team_id']) == team_id:
            return jsonify({'error': 'You are the current highest bidder. Cannot skip.'}), 400
        
        # Insert skip
        cursor.execute("""
            INSERT INTO player_skips (auction_id, session_player_id, player_id, reason, notes, skipped_by, team_id, session_id)
            SELECT %s, %s, sp.player_id, %s, %s, %s, %s, %s
            FROM session_players sp
            WHERE sp.id = %s
        """, (auction_id, session_player_id, reason, notes, user_id, team_id, session_id, session_player_id))
        
        cursor.execute(
            "UPDATE session_players SET skip_reason = %s, skip_notes = %s WHERE id = %s", 
            (reason, notes, session_player_id)
        )
        db.commit()
        
        skip_count = get_skip_count(cursor, auction_id, session_player_id)
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