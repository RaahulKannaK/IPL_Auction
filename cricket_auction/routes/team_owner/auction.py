from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('team_owner_auction', __name__, url_prefix='/team_owner')

def get_user_team(cursor, user_id, auction_id):
    """Get team owned by user"""
    cursor.execute("SELECT * FROM teams WHERE owner_id = %s AND auction_id = %s", (user_id, auction_id))
    return cursor.fetchone()

def get_min_bid_increment(current_bid):
    """Get minimum bid increment based on current bid amount"""
    if current_bid < 1.0:
        return 0.05
    elif current_bid < 2.0:
        return 0.10
    elif current_bid < 7.0:
        return 0.25
    else:
        return 0.25


# ==================== MAIN AUCTION ROOM (TEAM OWNER) ====================

@bp.route('/auction')
def auction_room():
    """Team owner auction room — view only, bid enabled"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    # Accept session from URL param OR flask session
    url_session_id = request.args.get('session', type=int)
    active_session_id = url_session_id or session.get('active_session_id')
    active_auction_id = session.get('active_auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Find the auction
        if active_auction_id:
            cursor.execute("SELECT * FROM auctions WHERE id = %s", (active_auction_id,))
        else:
            cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
        auction = cursor.fetchone()
        
        if not auction:
            flash('No active auction found')
            return redirect('/team_owner/')
        
        auction_id = auction['id']
        session['active_auction_id'] = auction_id
        
        # MUST HAVE ACTIVE SESSION
        if not active_session_id:
            cursor.execute("""
                SELECT * FROM auction_sessions 
                WHERE auction_id = %s AND status IN ('active', 'paused')
                ORDER BY created_at DESC LIMIT 1
            """, (auction_id,))
            existing_session = cursor.fetchone()
            
            if not existing_session:
                flash('No active session available. Contact admin.')
                return redirect('/team_owner/')
            
            session['active_session_id'] = existing_session['id']
            active_session_id = existing_session['id']
        
        session['active_session_id'] = active_session_id
        
        # LOAD SESSION DETAILS
        cursor.execute("""
            SELECT s.*, a.league_name, a.status as auction_status, a.squad_size, 
                   a.purse_limit, a.overseas_limit
            FROM auction_sessions s
            JOIN auctions a ON s.auction_id = a.id
            WHERE s.id = %s
        """, (active_session_id,))
        auction_session = cursor.fetchone()
        
        if not auction_session:
            session.pop('active_session_id', None)
            flash('Session expired. Contact admin.')
            return redirect('/team_owner/')
        
        # GET SESSION TEAMS
        session_team_ids = []
        if auction_session.get('team_ids'):
            try:
                session_team_ids = json.loads(auction_session['team_ids']) if isinstance(auction_session['team_ids'], str) else auction_session['team_ids']
            except:
                session_team_ids = []
        
        session_teams = []
        if session_team_ids:
            format_ids = ','.join(['%s'] * len(session_team_ids))
            cursor.execute(f"""
                SELECT t.*, 
                       (t.purse_limit - COALESCE(t.spent, 0) - COALESCE(t.reserved, 0)) as available_purse,
                       (SELECT COUNT(*) FROM session_team_players stp 
                        JOIN session_players sp ON stp.session_player_id = sp.id 
                        WHERE stp.team_id = t.id AND sp.session_id = {active_session_id}) as squad_count,
                       (SELECT COUNT(*) FROM session_team_players stp 
                        JOIN session_players sp ON stp.session_player_id = sp.id 
                        JOIN players p ON sp.player_id = p.id 
                        WHERE stp.team_id = t.id AND p.overseas = TRUE AND sp.session_id = {active_session_id}) as overseas_count
                FROM teams t
                WHERE t.id IN ({format_ids})
            """, tuple(session_team_ids))
            session_teams = cursor.fetchall()
        
        # Get user team
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if user_team and user_team['id'] not in session_team_ids:
            flash('Your team is not part of this session')
            return redirect('/team_owner/')
        
        # GET SESSION PLAYERS (available + unsold + in_auction)
        cursor.execute("""
            SELECT sp.id as session_player_id, sp.base_price, sp.status, 
                   sp.sold_price, sp.sold_team_id,
                   p.id as player_id, p.player_name, p.category, p.overseas
            FROM session_players sp
            JOIN players p ON sp.player_id = p.id
            WHERE sp.session_id = %s AND sp.status IN ('available', 'unsold', 'in_auction')
            ORDER BY 
                CASE sp.status 
                    WHEN 'in_auction' THEN 1 
                    WHEN 'available' THEN 2 
                    WHEN 'unsold' THEN 3 
                END,
                p.player_name
        """, (active_session_id,))
        players = cursor.fetchall()
        
        # CURRENT PLAYER (from session state)
        current_player = None
        current_bid = 0
        has_bids = False
        
        if auction_session.get('current_player_id'):
            cursor.execute("""
                SELECT sp.*, p.player_name, p.category, p.overseas, p.id as player_id
                FROM session_players sp
                JOIN players p ON sp.player_id = p.id
                WHERE sp.id = %s AND sp.session_id = %s
            """, (auction_session['current_player_id'], active_session_id))
            current_player = cursor.fetchone()
            current_bid = float(auction_session.get('current_bid') or 0)
            
            cursor.execute("""
                SELECT COUNT(*) as bid_count FROM session_bids 
                WHERE session_id = %s AND session_player_id = %s
            """, (active_session_id, auction_session['current_player_id']))
            bid_result = cursor.fetchone()
            has_bids = bid_result['bid_count'] > 0 if bid_result else False
        
        # GET ALL SESSIONS FOR NAVIGATION
        cursor.execute("""
            SELECT s.*, 
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id) as player_count
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY s.created_at ASC
        """, (auction_id,))
        all_sessions = cursor.fetchall()
        
        for sess in all_sessions:
            if sess.get('team_ids'):
                try:
                    sess['team_ids_list'] = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                except:
                    sess['team_ids_list'] = []
            else:
                sess['team_ids_list'] = []
            sess['team_count'] = len(sess['team_ids_list'])
        
        # Build auction dict
        auction_dict = {
            'id': auction_id,
            'league_name': auction['league_name'],
            'status': auction['status'],
            'squad_size': auction.get('squad_size', 18),
            'purse_limit': auction.get('purse_limit', 100),
            'overseas_limit': auction.get('overseas_limit', 8)
        }
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/auction.html', 
        auction=auction_dict,
        auction_session=auction_session,
        session_id=active_session_id,
        current_session=auction_session,
        players=players, 
        teams=session_teams,
        user_team=user_team,
        all_sessions=all_sessions,
        current_player=current_player,
        current_bid=current_bid,
        has_bids=has_bids
    )


# ==================== BIDDING ====================

@bp.route('/auction/bid', methods=['POST'])
def place_bid():
    """Place bid — team owner only for their own team"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = data.get('team_id')
    amount = float(data.get('amount', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team belongs to logged-in user
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only bid for your own team'}), 403
        
        # Verify team is in session
        cursor.execute("SELECT team_ids FROM auction_sessions WHERE id = %s", (active_session_id,))
        sess = cursor.fetchone()
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        session_team_ids = []
        if sess['team_ids']:
            try:
                session_team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                pass
        
        if team_id not in session_team_ids:
            return jsonify({'error': 'Team not in this session'}), 403
        
        # Check if player was skipped by this team
        cursor.execute("""
            SELECT * FROM session_skips 
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        existing_skip = cursor.fetchone()
        if existing_skip:
            return jsonify({'error': 'You skipped this player. Cannot bid.'}), 400
        
        # Check last bid in this session
        cursor.execute("""
            SELECT team_id FROM session_bids 
            WHERE session_id = %s AND session_player_id = %s 
            ORDER BY bid_amount DESC, created_at DESC LIMIT 1
        """, (active_session_id, session_player_id))
        last_bid = cursor.fetchone()
        if last_bid and last_bid['team_id'] == team_id:
            return jsonify({'error': 'You are already the highest bidder.'}), 400
        
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if auction['status'] != 'live':
            return jsonify({'error': 'Auction not live'}), 400
        
        # Get base price from session_players
        cursor.execute("SELECT base_price FROM session_players WHERE id = %s", (session_player_id,))
        sp_row = cursor.fetchone()
        base_price = float(sp_row['base_price']) if sp_row else 2.0
        
        # Check highest bid in this session
        cursor.execute("""
            SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
            FROM session_bids 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
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
        
        # Check funds
        available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
        if amount > available:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        # Insert into session_bids
        cursor.execute("""
            INSERT INTO session_bids (session_id, session_player_id, team_id, bid_amount) 
            VALUES (%s, %s, %s, %s)
        """, (active_session_id, session_player_id, team_id, amount))
        
        # Update auction_sessions with current bid
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_bid = %s, current_bidder_id = %s
            WHERE id = %s
        """, (amount, team_id, active_session_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'success': True, 
        'current_bid': amount, 
        'bidder': team['team_name'],
        'check_auto': True
    })


# ==================== SKIP PLAYER ====================

@bp.route('/auction/skip', methods=['POST'])
def skip_player():
    """Team owner skips a player — cannot bid on them later"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = data.get('team_id')
    reason = data.get('reason', 'manual_skip')
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team belongs to logged-in user
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only skip for your own team'}), 403
        
        # Check if already skipped
        cursor.execute("""
            SELECT * FROM session_skips 
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        existing = cursor.fetchone()
        if existing:
            return jsonify({'error': 'Already skipped this player'}), 400
        
        # Record skip
        cursor.execute("""
            INSERT INTO session_skips (session_id, session_player_id, team_id, skipped_by, reason)
            VALUES (%s, %s, %s, %s, %s)
        """, (active_session_id, session_player_id, team_id, session['user_id'], reason))
        
        # Count total skips for this player
        cursor.execute("""
            SELECT COUNT(DISTINCT team_id) as skip_count
            FROM session_skips
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        skip_result = cursor.fetchone()
        
        # Count total teams in session
        cursor.execute("SELECT team_ids FROM auction_sessions WHERE id = %s", (active_session_id,))
        sess = cursor.fetchone()
        total_teams = 0
        if sess and sess['team_ids']:
            try:
                team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                total_teams = len(team_ids)
            except:
                pass
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'success': True,
        'skip_count': skip_result['skip_count'],
        'total_teams': total_teams,
        'all_skipped': skip_result['skip_count'] >= total_teams and total_teams > 0
    })


# ==================== HIDDEN BID ====================

@bp.route('/auction/hidden_bid', methods=['POST'])
def place_hidden_bid():
    """Hidden max bid — team owner only for their own team"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = data.get('team_id')
    max_amount = float(data.get('max_amount', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team belongs to logged-in user
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only set hidden bids for your own team'}), 403
        
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
        if max_amount > available:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        # Remove old hidden bid for this player+team
        cursor.execute("""
            DELETE FROM session_hidden_max_bids
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        
        # Insert new hidden max bid
        cursor.execute("""
            INSERT INTO session_hidden_max_bids (session_id, session_player_id, team_id, max_bid, is_active) 
            VALUES (%s, %s, %s, %s, TRUE)
        """, (active_session_id, session_player_id, team_id, max_amount))
        
        # Update purse reservation
        cursor.execute("""
            DELETE FROM session_purse_reservations
            WHERE session_player_id = %s AND team_id = %s AND status = 'active'
        """, (session_player_id, team_id))
        
        cursor.execute("""
            INSERT INTO session_purse_reservations (session_id, session_player_id, team_id, reserved_amount, status)
            VALUES (%s, %s, %s, %s, 'active')
        """, (active_session_id, session_player_id, team_id, max_amount))
        
        # Update team reserved amount
        cursor.execute("""
            UPDATE teams SET reserved = reserved + %s WHERE id = %s
        """, (max_amount, team_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})


# ==================== AUTO BID ====================

@bp.route('/auction/auto_bid', methods=['POST'])
def auto_counter_bid():
    """Auto bid — session-scoped (same logic as admin)"""
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    current_bid = float(data.get('current_bid', 0))
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT h.*, t.team_name, t.purse_limit, t.spent, t.reserved 
            FROM session_hidden_max_bids h 
            JOIN teams t ON h.team_id = t.id 
            WHERE h.session_player_id = %s AND h.is_active = TRUE AND h.max_bid > %s
            ORDER BY h.max_bid DESC
        """, (session_player_id, current_bid))
        
        hidden_bids = cursor.fetchall()
        
        if not hidden_bids:
            return jsonify({'auto_bid': False})
        
        winner = hidden_bids[0]
        increment = get_min_bid_increment(current_bid)
        next_bid = current_bid + increment
        if next_bid > winner['max_bid']:
            next_bid = winner['max_bid']
        
        cursor.execute("""
            SELECT reserved_amount FROM session_purse_reservations 
            WHERE session_player_id = %s AND team_id = %s AND status = 'active'
        """, (session_player_id, winner['team_id']))
        res = cursor.fetchone()
        reserved_amount = float(res['reserved_amount']) if res else 0
        
        available = float(winner['purse_limit']) - float(winner['spent'] or 0) - (float(winner['reserved'] or 0) - reserved_amount)
        
        if next_bid > available + reserved_amount:
            return jsonify({'auto_bid': False, 'reason': 'Insufficient funds'})
        
        cursor.execute("""
            INSERT INTO session_bids (session_id, session_player_id, team_id, bid_amount) 
            VALUES (%s, %s, %s, %s)
        """, (active_session_id, session_player_id, winner['team_id'], next_bid))
        
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_bid = %s, current_bidder_id = %s
            WHERE id = %s
        """, (next_bid, winner['team_id'], active_session_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'auto_bid': True,
        'team': winner['team_name'],
        'amount': next_bid
    })


# ==================== STATUS POLLING ====================

@bp.route('/auction/status')
def get_status():
    """Get auction status — session-scoped (same as admin)"""
    auction_id = request.args.get('auction_id')
    team_id = request.args.get('team_id', type=int)
    active_session_id = request.args.get('session_id') or session.get('active_session_id')
    
    cache_key = f'auction:status:{auction_id or "active"}:{active_session_id or "no_session"}'
    
    def fetch_status():
        db = get_db()
        cursor = db.cursor(dictionary=True)
        
        try:
            if auction_id:
                cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
            else:
                cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
            
            auction = cursor.fetchone()
            
            if not auction:
                return {'status': 'none'}
            
            result = {
                'status': auction['status'],
                'league_name': auction.get('league_name'),
                'auction_id': auction['id'],
                'session_id': active_session_id
            }
            
            # Get session-specific state
            if active_session_id:
                cursor.execute("""
                    SELECT current_player_id, current_bid, current_bidder_id
                    FROM auction_sessions WHERE id = %s
                """, (active_session_id,))
                sess = cursor.fetchone()
                
                if sess:
                    result['current_bid'] = float(sess.get('current_bid') or 0)
                    result['current_bidder_id'] = sess.get('current_bidder_id')
                    result['current_bidder'] = None
                    result['current_player'] = None
                    result['player_category'] = None
                    result['base_price'] = 0
                    result['session_player_id'] = None
                    result['overseas'] = False
                    result['has_bids'] = False
                    result['skip_count'] = 0
                    result['total_teams'] = 0
                    result['all_skipped'] = False
                    
                    if sess.get('current_bidder_id'):
                        cursor.execute("SELECT team_name FROM teams WHERE id = %s", (sess['current_bidder_id'],))
                        bidder = cursor.fetchone()
                        if bidder:
                            result['current_bidder'] = bidder['team_name']
                    
                    session_player_id = sess.get('current_player_id')
                    if session_player_id:
                        cursor.execute("""
                            SELECT p.player_name, p.category, p.overseas, sp.base_price, sp.id as session_player_id
                            FROM session_players sp
                            JOIN players p ON sp.player_id = p.id
                            WHERE sp.id = %s
                        """, (session_player_id,))
                        player = cursor.fetchone()
                        if player:
                            result['current_player'] = player['player_name']
                            result['player_category'] = player['category']
                            result['base_price'] = float(player['base_price'])
                            result['session_player_id'] = player['session_player_id']
                            result['overseas'] = player.get('overseas', False)
                        
                        cursor.execute("""
                            SELECT COUNT(*) as bid_count FROM session_bids 
                            WHERE session_id = %s AND session_player_id = %s
                        """, (active_session_id, session_player_id))
                        bid_result = cursor.fetchone()
                        result['has_bids'] = bid_result['bid_count'] > 0 if bid_result else False
                        
                        cursor.execute("""
                            SELECT COUNT(DISTINCT team_id) as skip_count
                            FROM session_skips
                            WHERE session_id = %s AND session_player_id = %s
                        """, (active_session_id, session_player_id))
                        skip_result = cursor.fetchone()
                        result['skip_count'] = skip_result['skip_count'] if skip_result else 0
                        
                        # Count teams in session
                        cursor.execute("SELECT team_ids FROM auction_sessions WHERE id = %s", (active_session_id,))
                        team_data = cursor.fetchone()
                        if team_data and team_data['team_ids']:
                            try:
                                team_ids = json.loads(team_data['team_ids']) if isinstance(team_data['team_ids'], str) else team_data['team_ids']
                                result['total_teams'] = len(team_ids)
                            except:
                                result['total_teams'] = 0
                        
                        result['all_skipped'] = result['skip_count'] >= result['total_teams'] and result['total_teams'] > 0
            
            return result
            
        finally:
            cursor.close()
            db.close()
    
    result = get_cached(cache_key, fetch_status, ttl_seconds=0)
    return jsonify(result)


# ==================== PLAYERS LIST (for refresh) ====================

@bp.route('/auction/players')
def get_players():
    """Get available players for current session — team owner view"""
    auction_id = request.args.get('auction_id')
    active_session_id = request.args.get('session_id') or session.get('active_session_id')
    
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT sp.id as session_player_id, sp.base_price, sp.status,
                   p.id as player_id, p.player_name, p.category, p.overseas
            FROM session_players sp
            JOIN players p ON sp.player_id = p.id
            WHERE sp.session_id = %s AND sp.status IN ('available', 'unsold', 'in_auction')
            ORDER BY 
                CASE sp.status 
                    WHEN 'in_auction' THEN 1 
                    WHEN 'available' THEN 2 
                    WHEN 'unsold' THEN 3 
                END,
                p.player_name
        """, (active_session_id,))
        players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'players': players})