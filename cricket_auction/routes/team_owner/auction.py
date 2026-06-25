from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('team_owner_auction', __name__, url_prefix='/team-owner')

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
    """Team owner auction room — session split view + viewer mode + squad view"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    active_auction_id = session.get('active_auction_id')
    active_team_id = session.get('active_team_id')
    
    if not active_auction_id or not active_team_id:
        flash('No active auction selected')
        return redirect('/team-owner/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify user still owns team in this auction
        cursor.execute("""
            SELECT t.*, a.league_name, a.status as auction_status, 
                   a.squad_size, a.overseas_limit, a.purse_limit
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            WHERE t.auction_id = %s AND t.owner_id = %s
        """, (active_auction_id, session['user_id']))
        team = cursor.fetchone()
        
        if not team:
            session.pop('active_auction_id', None)
            session.pop('active_team_id', None)
            session.pop('active_league_name', None)
            session.pop('active_session_id', None)
            flash('Invalid auction session. Please select again.')
            return redirect('/team-owner/dashboard')
        
        # Get total teams count
        cursor.execute("SELECT COUNT(*) as total FROM teams WHERE auction_id = %s", (active_auction_id,))
        result = cursor.fetchone()
        total_teams = result['total'] if result else 0
        
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
        """, (active_auction_id,))
        raw_sessions = cursor.fetchall()
        
        # Process sessions - split into my_sessions and other_sessions
        my_sessions = []
        other_sessions = []
        
        for sess in raw_sessions:
            team_ids = []
            if sess['team_ids']:
                try:
                    team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                    team_ids = [int(tid) for tid in team_ids]
                except:
                    team_ids = []
            
            is_member = team['id'] in team_ids if team_ids else False
            slots_filled = len(team_ids)
            
            session_name = sess['session_name'] or f'Session {sess["id"]}'
            name_lower = session_name.lower()
            
            if 'morning' in name_lower:
                session_icon = '🌅'
            elif 'evening' in name_lower or 'night' in name_lower:
                session_icon = '🌙'
            elif 'weekend' in name_lower:
                session_icon = '🏖️'
            elif 'afternoon' in name_lower:
                session_icon = '☀️'
            else:
                session_icon = '📅'
            
            session_data = {
                'id': sess['id'],
                'session_name': session_name,
                'status': sess['status'],
                'start_time': str(sess['start_time'])[:16] if sess['start_time'] else None,
                'end_time': str(sess['end_time'])[:16] if sess['end_time'] else None,
                'team_ids_list': team_ids,
                'total_teams': total_teams,
                'slots_filled': slots_filled,
                'slots_left': total_teams - slots_filled,
                'is_member': is_member,
                'session_icon': session_icon,
                'created_at': str(sess['created_at']) if sess['created_at'] else None
            }
            
            if is_member:
                my_sessions.append(session_data)
            else:
                other_sessions.append(session_data)
        
        # Check if user has active session selected
        active_session_id = session.get('active_session_id')
        is_viewer_mode = session.get('viewer_mode', False)
        has_active_session = False
        current_session = None
        
        if active_session_id:
            cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (active_session_id,))
            current_session = cursor.fetchone()
            if current_session:
                try:
                    sess_team_ids = json.loads(current_session['team_ids']) if isinstance(current_session['team_ids'], str) else current_session['team_ids'] or []
                    sess_team_ids = [int(tid) for tid in sess_team_ids]
                    if team['id'] in sess_team_ids:
                        has_active_session = True
                        is_viewer_mode = False  # Override viewer mode if actually member
                    else:
                        has_active_session = True  # Still has session selected but as viewer
                except:
                    pass
        
        # ============================================
        # GET SESSION DATA IF ACTIVE
        # ============================================
        players = []
        current_player = None
        current_bid = 0
        has_bids = False
        other_teams_squad = []
        
        if has_active_session and active_session_id:
            # Get session players
            cursor.execute("""
                SELECT sp.id as session_player_id, sp.base_price, sp.status,
                       p.id as player_id, p.player_name, p.category, p.overseas
                FROM session_players sp
                JOIN players p ON sp.player_id = p.id
                WHERE sp.session_id = %s AND sp.status IN ('available', 'unsold', 'in_auction')
                ORDER BY 
                    CASE sp.status WHEN 'in_auction' THEN 1 WHEN 'available' THEN 2 ELSE 3 END,
                    p.player_name
            """, (active_session_id,))
            players = cursor.fetchall()
            
            # Get current auction state
            if current_session and current_session.get('current_player_id'):
                cursor.execute("""
                    SELECT sp.*, p.player_name, p.category, p.overseas, p.id as player_id
                    FROM session_players sp
                    JOIN players p ON sp.player_id = p.id
                    WHERE sp.id = %s AND sp.session_id = %s
                """, (current_session['current_player_id'], active_session_id))
                current_player = cursor.fetchone()
                current_bid = float(current_session.get('current_bid') or 0)
                
                cursor.execute("""
                    SELECT COUNT(*) as bid_count FROM session_bids 
                    WHERE session_id = %s AND session_player_id = %s
                """, (active_session_id, current_session['current_player_id']))
                bid_result = cursor.fetchone()
                has_bids = bid_result['bid_count'] > 0 if bid_result else False
            
            # Format current_session times
            if current_session:
                current_session['start_time'] = str(current_session['start_time'])[:16] if current_session['start_time'] else None
                current_session['end_time'] = str(current_session['end_time'])[:16] if current_session['end_time'] else None
            
            # ============================================
            # GET OTHER TEAMS SQUAD (only purchase prices, not willing prices)
            # ============================================
            cursor.execute("""
                SELECT t.id, t.team_name, t.purse_limit, t.spent,
                       (t.purse_limit - COALESCE(t.spent, 0) - COALESCE(t.reserved, 0)) as available_purse
                FROM teams t
                WHERE t.auction_id = %s AND t.id != %s
            """, (active_auction_id, team['id']))
            other_teams = cursor.fetchall()
            
            for ot in other_teams:
                cursor.execute("""
                    SELECT p.player_name, stp.purchase_price, sp.willing_price
                    FROM session_team_players stp
                    JOIN session_players sp ON stp.session_player_id = sp.id
                    JOIN players p ON sp.player_id = p.id
                    WHERE stp.team_id = %s AND sp.session_id = %s
                    ORDER BY stp.purchase_price DESC
                """, (ot['id'], active_session_id))
                team_players = cursor.fetchall()
                
                # Only show willing price to the team owner who bought the player
                # For other teams viewing, hide willing_price
                for tp in team_players:
                    tp['show_willing'] = False  # Only true for own team view
                
                if team_players:
                    other_teams_squad.append({
                        'team_id': ot['id'],
                        'team_name': ot['team_name'],
                        'purse_limit': float(ot['purse_limit']),
                        'spent': float(ot['spent'] or 0),
                        'available_purse': float(ot['available_purse'] or 0),
                        'players': team_players
                    })
            
            # Get my team stats
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                WHERE stp.team_id = %s AND sp.session_id = %s
            """, (team['id'], active_session_id))
            result = cursor.fetchone()
            team['squad_count'] = result['cnt'] if result else 0
            
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                JOIN players p ON sp.player_id = p.id
                WHERE stp.team_id = %s AND p.overseas = TRUE AND sp.session_id = %s
            """, (team['id'], active_session_id))
            result = cursor.fetchone()
            team['overseas_count'] = result['cnt'] if result else 0
        
        # Build auction dict
        auction_dict = {
            'id': active_auction_id,
            'league_name': team['league_name'],
            'status': team['auction_status'],
            'squad_size': team.get('squad_size', 18),
            'purse_limit': team.get('purse_limit', 100),
            'overseas_limit': team.get('overseas_limit', 8)
        }
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/auction.html',
        team=team,
        user_team=team,
        auction=auction_dict,
        auction_id=active_auction_id,
        my_sessions=my_sessions,
        other_sessions=other_sessions,
        has_active_session=has_active_session,
        is_viewer_mode=is_viewer_mode,
        active_session_id=active_session_id,
        session_id=active_session_id,
        current_session=current_session,
        all_sessions=my_sessions + other_sessions,
        players=players,
        total_teams=total_teams,
        current_player=current_player,
        current_bid=current_bid,
        has_bids=has_bids,
        other_teams_squad=other_teams_squad
    )


# ==================== SELECT SESSION ====================

@bp.route('/select-session', methods=['POST'])
def select_session():
    """Handle session selection - member or viewer mode"""
    if session.get('role') != 'team_owner':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.get_json()
    session_id = data.get('session_id')
    viewer_mode = data.get('viewer_mode', False)
    
    if not session_id:
        return jsonify({'success': False, 'message': 'Session ID required'}), 400
    
    active_auction_id = session.get('active_auction_id')
    
    if not active_auction_id:
        return jsonify({'success': False, 'message': 'No active auction'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT * FROM auction_sessions 
            WHERE id = %s AND auction_id = %s
        """, (session_id, active_auction_id))
        sess = cursor.fetchone()
        
        if not sess:
            return jsonify({'success': False, 'message': 'Invalid session for this auction'}), 400
        
        # Check if team is member of this session
        team_ids = []
        if sess['team_ids']:
            try:
                team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                team_ids = [int(tid) for tid in team_ids]
            except:
                pass
        
        cursor.execute("SELECT id FROM teams WHERE owner_id = %s AND auction_id = %s", 
                      (session['user_id'], active_auction_id))
        user_team = cursor.fetchone()
        
        is_member = user_team and user_team['id'] in team_ids
        
        if viewer_mode and is_member:
            # If trying viewer mode but is member, force member mode
            viewer_mode = False
        
    finally:
        cursor.close()
        db.close()
    
    session['active_session_id'] = session_id
    session['viewer_mode'] = viewer_mode
    
    return jsonify({'success': True, 'redirect': '/team-owner/auction'})


# ==================== BIDDING ====================

@bp.route('/auction/bid', methods=['POST'])
def place_bid():
    """Place bid — team owner only for their own team"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    if session.get('viewer_mode'):
        return jsonify({'error': 'Viewer mode - cannot bid'}), 403
    
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
    """Team owner skips a player"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    if session.get('viewer_mode'):
        return jsonify({'error': 'Viewer mode - cannot skip'}), 403
    
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
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only skip for your own team'}), 403
        
        cursor.execute("""
            SELECT * FROM session_skips 
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        existing = cursor.fetchone()
        if existing:
            return jsonify({'error': 'Already skipped this player'}), 400
        
        cursor.execute("""
            INSERT INTO session_skips (session_id, session_player_id, team_id, skipped_by, reason)
            VALUES (%s, %s, %s, %s, %s)
        """, (active_session_id, session_player_id, team_id, session['user_id'], reason))
        
        cursor.execute("""
            SELECT COUNT(DISTINCT team_id) as skip_count
            FROM session_skips
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        skip_result = cursor.fetchone()
        
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
    """Hidden max bid — team owner only"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    if session.get('viewer_mode'):
        return jsonify({'error': 'Viewer mode'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = data.get('team_id')
    max_amount = float(data.get('max_amount', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
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
        
        cursor.execute("""
            DELETE FROM session_hidden_max_bids
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        
        cursor.execute("""
            INSERT INTO session_hidden_max_bids (session_id, session_player_id, team_id, max_bid, is_active) 
            VALUES (%s, %s, %s, %s, TRUE)
        """, (active_session_id, session_player_id, team_id, max_amount))
        
        cursor.execute("""
            DELETE FROM session_purse_reservations
            WHERE session_player_id = %s AND team_id = %s AND status = 'active'
        """, (session_player_id, team_id))
        
        cursor.execute("""
            INSERT INTO session_purse_reservations (session_id, session_player_id, team_id, reserved_amount, status)
            VALUES (%s, %s, %s, %s, 'active')
        """, (active_session_id, session_player_id, team_id, max_amount))
        
        cursor.execute("""
            UPDATE teams SET reserved = reserved + %s WHERE id = %s
        """, (max_amount, team_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})


# ==================== WILLING PRICE ====================

@bp.route('/auction/willing-price', methods=['POST'])
def set_willing_price():
    """Set willing price for a purchased player"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = data.get('team_id')
    willing_price = float(data.get('willing_price', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team ownership
        user_team = get_user_team(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Update willing price in session_players
        cursor.execute("""
            UPDATE session_players 
            SET willing_price = %s 
            WHERE id = %s AND sold_team_id = %s
        """, (willing_price, session_player_id, team_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True})


# ==================== AUTO BID ====================

@bp.route('/auction/auto_bid', methods=['POST'])
def auto_counter_bid():
    """Auto bid — session-scoped"""
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
    """Get auction status — session-scoped"""
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
                    result['just_sold'] = False
                    result['sold_to_team_id'] = None
                    result['sold_price'] = 0
                    result['needs_willing_price'] = False
                    
                    if sess.get('current_bidder_id'):
                        cursor.execute("SELECT team_name FROM teams WHERE id = %s", (sess['current_bidder_id'],))
                        bidder = cursor.fetchone()
                        if bidder:
                            result['current_bidder'] = bidder['team_name']
                    
                    session_player_id = sess.get('current_player_id')
                    if session_player_id:
                        cursor.execute("""
                            SELECT p.player_name, p.category, p.overseas, sp.base_price, sp.id as session_player_id,
                                   sp.status, sp.sold_team_id, sp.sold_price
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
                            
                            # Check if just sold and needs willing price
                            if player['status'] == 'sold' and player['sold_team_id']:
                                result['just_sold'] = True
                                result['sold_to_team_id'] = player['sold_team_id']
                                result['sold_price'] = float(player['sold_price'] or 0)
                                
                                # Check if willing price already set
                                if player.get('willing_price') is None:
                                    # Check if not all teams in session (condition for willing price)
                                    cursor.execute("SELECT team_ids FROM auction_sessions WHERE id = %s", (active_session_id,))
                                    team_data = cursor.fetchone()
                                    if team_data and team_data['team_ids']:
                                        try:
                                            all_team_ids = json.loads(team_data['team_ids']) if isinstance(team_data['team_ids'], str) else team_data['team_ids']
                                            total_in_session = len(all_team_ids)
                                            # Willing price needed if session doesn't have all teams
                                            if total_in_session < total_teams:
                                                result['needs_willing_price'] = True
                                        except:
                                            pass
                        
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
    """Get available players for current session"""
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
                CASE sp.status WHEN 'in_auction' THEN 1 WHEN 'available' THEN 2 ELSE 3 END,
                p.player_name
        """, (active_session_id,))
        players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'players': players})