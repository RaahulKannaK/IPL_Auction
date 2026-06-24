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
# SET ACTIVE SESSION — CRITICAL: persists session choice
# ============================================================
@bp.route('/set-session', methods=['POST'])
def set_session():
    """Team owner selects a session — persist in Flask session"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({'error': 'No session ID'}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT s.*, t.auction_id
            FROM auction_sessions s
            JOIN teams t ON s.auction_id = t.auction_id
            WHERE s.id = %s AND t.owner_id = %s
        """, (session_id, session['user_id']))
        sess = cursor.fetchone()

        if not sess:
            return jsonify({'error': 'Session not found or not your auction'}), 404

        # CRITICAL: Persist in Flask session and mark modified
        session['active_session_id'] = int(session_id)
        session['active_auction_id'] = sess['auction_id']
        session.modified = True

        team_ids = parse_team_ids(sess['team_ids'])
        user_team = get_user_team(cursor, session['user_id'], sess['auction_id'])
        is_member = user_team and int(user_team['id']) in team_ids

    finally:
        cursor.close()
        db.close()

    return jsonify({
        'success': True,
        'session_id': session_id,
        'session_name': sess.get('session_name', f'Session {session_id}'),
        'is_member': is_member
    })

# ============================================================
# LEAVE SESSION — no reload, just clear
# ============================================================
@bp.route('/leave-session', methods=['POST'])
def leave_session():
    """Leave current session — clear active session"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403

    session.pop('active_session_id', None)
    session.modified = True

    return jsonify({'success': True})

# ============================================================
# ENTRY POINT FROM DASHBOARD
# ============================================================
@bp.route('/auction-room/<int:auction_id>')
def enter_auction_room(auction_id):
    """Entry point from dashboard — set auction and redirect"""
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')

    session['active_auction_id'] = auction_id
    session.pop('active_session_id', None)
    session.modified = True

    return redirect(url_for('team_owner_auction.auction_room'))

# ============================================================
# MAIN AUCTION PAGE — session-scoped like admin
# ============================================================
@bp.route('/')
def auction_room():
    """Main auction page — session selector first, then auction room"""
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')

    auction_id = session.get('active_auction_id')

    if not auction_id:
        db = get_db()
        cursor = db.cursor(dictionary=True)
        try:
            user_team = get_user_team(cursor, session['user_id'])
            if user_team:
                auction_id = user_team['auction_id']
                session['active_auction_id'] = auction_id
                session.modified = True
        finally:
            cursor.close()
            db.close()

    if not auction_id:
        flash('No active auction selected')
        return redirect('/team-owner/dashboard')

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team:
            flash('You do not own a team in this auction')
            session.pop('active_auction_id', None)
            return redirect('/team-owner/dashboard')

        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()

        if not auction:
            flash('Auction not found')
            return redirect('/team-owner/dashboard')

        total_teams = get_total_teams(cursor, auction_id)
        user_team_id = int(user_team['id'])

        # ============================================
        # FIXED: Read active_session_id from Flask session
        # ============================================
        active_session_id = session.get('active_session_id')
        has_active_session = False
        is_member_session = False
        current_session = None

        if active_session_id:
            cursor.execute(
                "SELECT * FROM auction_sessions WHERE id = %s AND auction_id = %s",
                (active_session_id, auction_id)
            )
            current_session = cursor.fetchone()
            if current_session:
                sess_team_ids = parse_team_ids(current_session['team_ids'])
                is_member_session = user_team_id in sess_team_ids
                has_active_session = current_session['status'] in ['active', 'paused']
                if not has_active_session:
                    session.pop('active_session_id', None)
                    session.modified = True
                    active_session_id = None
                    current_session = None
            else:
                session.pop('active_session_id', None)
                session.modified = True
                active_session_id = None

        # ============================================
        # FETCH ALL SESSIONS for selector
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

        cursor.execute("SELECT id, team_name FROM teams WHERE auction_id = %s", (auction_id,))
        all_teams = {int(row['id']): row['team_name'] for row in cursor.fetchall()}

        # ============================================
        # FIXED: Get players ONLY for THIS session
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

        # Current player for THIS session
        current_player = None
        if has_active_session and active_session_id and current_session and current_session.get('current_player_id'):
            cursor.execute("""
                SELECT p.*, sp.id as session_player_id, sp.base_price, sp.status
                FROM session_players sp
                JOIN players p ON sp.player_id = p.id
                WHERE sp.id = %s AND sp.session_id = %s
            """, (current_session['current_player_id'], active_session_id))
            current_player = cursor.fetchone()

        # Public bids for THIS session only
        public_bids = []
        if active_session_id:
            cursor.execute("""
                SELECT b.*, p.player_name, t.team_name
                FROM session_bids b
                JOIN session_players sp ON b.session_player_id = sp.id
                JOIN players p ON sp.player_id = p.id
                JOIN teams t ON b.team_id = t.id
                WHERE b.session_id = %s
                ORDER BY b.created_at DESC
                LIMIT 20
            """, (active_session_id,))
            public_bids = cursor.fetchall()

        # Hidden bids for THIS session only
        hidden_bids = []
        if active_session_id:
            cursor.execute("""
                SELECT h.*, p.player_name, sp.id as session_player_id
                FROM session_hidden_max_bids h
                JOIN session_players sp ON h.session_player_id = sp.id
                JOIN players p ON sp.player_id = p.id
                WHERE h.team_id = %s AND h.is_active = TRUE AND sp.session_id = %s
            """, (user_team_id, active_session_id))
            hidden_bids = cursor.fetchall()

        # Skip votes for current player in THIS session
        skip_votes = []
        if current_player and active_session_id:
            cursor.execute("""
                SELECT ss.*, t.team_name, u.username as skipped_by_name
                FROM session_skips ss
                JOIN teams t ON ss.team_id = t.id
                JOIN users u ON ss.skipped_by = u.id
                WHERE ss.session_id = %s AND ss.session_player_id = %s
                ORDER BY ss.skipped_at DESC
            """, (active_session_id, current_player['session_player_id']))
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

# ============================================================
# GET SESSIONS API
# ============================================================
@bp.route('/sessions')
def get_sessions():
    """Get all sessions for this auction (API)"""
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

# ============================================================
# GET PLAYERS — CRITICAL: session_id REQUIRED, no fallback
# ============================================================
@bp.route('/players')
def get_players():
    """Get players for a SPECIFIC session — must pass session_id"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403

    # CRITICAL: session_id MUST be passed in URL
    session_id = request.args.get('session_id', type=int)

    if not session_id:
        return jsonify({'error': 'No session_id provided'}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:
        # Verify this session belongs to user's auction
        cursor.execute("""
            SELECT s.auction_id, t.owner_id
            FROM auction_sessions s
            JOIN teams t ON s.auction_id = t.auction_id
            WHERE s.id = %s AND t.owner_id = %s
        """, (session_id, session['user_id']))
        valid = cursor.fetchone()

        if not valid:
            return jsonify({'error': 'Session not found or access denied'}), 403

        cursor.execute("""
            SELECT p.*, sp.id as session_player_id, sp.base_price, sp.status, sp.sold_price
            FROM players p
            JOIN session_players sp ON p.id = sp.player_id
            WHERE sp.session_id = %s AND sp.status IN ('available', 'unsold')
            ORDER BY p.player_name
        """, (session_id,))
        players = cursor.fetchall()

    finally:
        cursor.close()
        db.close()

    return jsonify({
        'players': players,
        'session_id': session_id,
        'count': len(players)
    })

# ============================================================
# BIDDING
# ============================================================
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
            SELECT * FROM session_skips
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (session_id, session_player_id, team_id))
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

        # Check current bids in THIS session
        cursor.execute("""
            SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
            FROM session_bids
            WHERE session_id = %s AND session_player_id = %s
        """, (session_id, session_player_id))
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
                SELECT team_id FROM session_bids
                WHERE session_id = %s AND session_player_id = %s
                ORDER BY bid_amount DESC, created_at DESC LIMIT 1
            """, (session_id, session_player_id))
            last_bidder = cursor.fetchone()
            if last_bidder and int(last_bidder['team_id']) == team_id:
                return jsonify({'error': 'You are already the highest bidder.'}), 400

        # Check funds
        available = float(user_team['purse_limit']) - float(user_team['spent'] or 0) - float(user_team['reserved'] or 0)
        cursor.execute("""
            SELECT max_bid FROM session_hidden_max_bids
            WHERE session_player_id = %s AND team_id = %s AND is_active = TRUE
        """, (session_player_id, team_id))
        hidden = cursor.fetchone()
        hidden_amount = float(hidden['max_bid']) if hidden else 0

        if amount > available + hidden_amount:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available + hidden_amount:.2f}Cr'}), 400

        # Insert bid
        cursor.execute("""
            INSERT INTO session_bids (session_id, session_player_id, team_id, bid_amount)
            VALUES (%s, %s, %s, %s)
        """, (session_id, session_player_id, team_id, amount))

        # Update session state
        cursor.execute("""
            UPDATE auction_sessions
            SET current_bid = %s, current_bidder_id = %s
            WHERE id = %s
        """, (amount, team_id, session_id))

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

# ============================================================
# HIDDEN BID
# ============================================================
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

        cursor.execute(
            "DELETE FROM session_hidden_max_bids WHERE session_player_id = %s AND team_id = %s",
            (session_player_id, team_id)
        )

        cursor.execute("""
            INSERT INTO session_hidden_max_bids (session_id, session_player_id, team_id, max_bid, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
        """, (session.get('active_session_id'), session_player_id, team_id, max_amount))

        cursor.execute(
            "SELECT id FROM session_purse_reservations WHERE team_id = %s AND session_player_id = %s",
            (team_id, session_player_id)
        )
        existing_res = cursor.fetchone()

        if existing_res:
            cursor.execute(
                "UPDATE session_purse_reservations SET reserved_amount = %s WHERE id = %s",
                (max_amount, existing_res['id'])
            )
        else:
            cursor.execute("""
                INSERT INTO session_purse_reservations (team_id, session_player_id, reserved_amount)
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

# ============================================================
# SKIP PLAYER
# ============================================================
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
            SELECT * FROM session_skips
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (session_id, session_player_id, team_id))
        if cursor.fetchone():
            return jsonify({'error': 'You already skipped this player'}), 400

        # Check if highest bidder
        cursor.execute("""
            SELECT team_id FROM session_bids
            WHERE session_id = %s AND session_player_id = %s
            ORDER BY bid_amount DESC, created_at DESC LIMIT 1
        """, (session_id, session_player_id))
        last_bid = cursor.fetchone()

        if last_bid and int(last_bid['team_id']) == team_id:
            return jsonify({'error': 'You are the current highest bidder. Cannot skip.'}), 400

        # Insert skip
        cursor.execute("""
            INSERT INTO session_skips (session_id, session_player_id, team_id, reason, notes, skipped_by)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (session_id, session_player_id, team_id, reason, notes, user_id))

        db.commit()

        # Count skips
        cursor.execute("""
            SELECT COUNT(DISTINCT team_id) as skip_count
            FROM session_skips
            WHERE session_id = %s AND session_player_id = %s
        """, (session_id, session_player_id))
        skip_result = cursor.fetchone()
        skip_count = skip_result['skip_count'] if skip_result else 0

    finally:
        cursor.close()
        db.close()

    return jsonify({
        'success': True,
        'skip_count': skip_count,
        'total_teams': get_total_teams(get_db().cursor(dictionary=True).execute("SELECT COUNT(*) as total FROM teams WHERE auction_id = %s", (auction_id,)).fetchone()['total'] if False else 0),
        'all_skipped': False,
        'message': f'Skipped ({skip_count}/{total_teams}) teams'
    })