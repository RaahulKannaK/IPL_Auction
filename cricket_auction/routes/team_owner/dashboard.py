from flask import Blueprint, render_template, session, flash, redirect, jsonify, request
from database.db import get_db, get_cached, clear_cache, db_transaction
import json

bp = Blueprint('team_owner_dashboard', __name__, url_prefix='/team-owner')


def _get_team_sessions(cursor, auction_ids, team_ids):
    """Batch fetch all session data for multiple auctions."""
    if not auction_ids:
        return {}
    
    format_auction_ids = ','.join(['%s'] * len(auction_ids))
    cursor.execute(f"""
        SELECT id, auction_id, team_ids, session_name, status, start_time, end_time, created_at
        FROM auction_sessions
        WHERE auction_id IN ({format_auction_ids})
        ORDER BY auction_id, created_at
    """, tuple(auction_ids))
    
    sessions = cursor.fetchall()
    
    # Build lookup: auction_id -> list of sessions with membership info
    result = {aid: {'total': 0, 'yours': 0, 'sessions': []} for aid in auction_ids}
    
    for sess in sessions:
        aid = sess['auction_id']
        result[aid]['total'] += 1
        
        # Parse team_ids
        team_ids_in_session = []
        raw = sess.get('team_ids')
        if raw:
            try:
                if isinstance(raw, str):
                    team_ids_in_session = json.loads(raw)
                else:
                    team_ids_in_session = raw
                team_ids_in_session = [int(t) for t in team_ids_in_session]
            except (json.JSONDecodeError, ValueError, TypeError):
                team_ids_in_session = []
        
        # Check if any of user's teams are in this session
        is_member = bool(set(team_ids) & set(team_ids_in_session))
        if is_member:
            result[aid]['yours'] += 1
        
        result[aid]['sessions'].append({
            'id': sess['id'],
            'session_name': sess.get('session_name') or f"Session {sess['id']}",
            'status': sess['status'],
            'start_time': str(sess['start_time'])[:16] if sess['start_time'] else None,
            'end_time': str(sess['end_time'])[:16] if sess['end_time'] else None,
            'is_member': is_member,
            'team_ids_list': team_ids_in_session,
        })
    
    return result


def _fetch_dashboard_data(user_id):
    """Single optimized query for ALL dashboard data. Returns list of team dicts."""
    
    with db_transaction() as cursor:
        # === STEP 1: Get teams + auction info + all player stats in ONE query ===
        cursor.execute("""
            SELECT 
                t.id, t.team_name, t.purse_limit, t.spent, t.reserved,
                t.owner_id, t.owner_ids, t.auction_id,
                a.league_name, a.status as auction_status,
                a.squad_size, a.overseas_limit,
                COUNT(DISTINCT stp.id) as squad_count,
                COUNT(DISTINCT CASE WHEN p.overseas = TRUE THEN stp.id END) as overseas_count,
                SUM(CASE WHEN p.category = 'batsman' THEN 1 ELSE 0 END) as batsmen,
                SUM(CASE WHEN p.category = 'bowler' THEN 1 ELSE 0 END) as bowlers,
                SUM(CASE WHEN p.category = 'all_rounder' THEN 1 ELSE 0 END) as all_rounders,
                SUM(CASE WHEN p.category = 'wicket_keeper' THEN 1 ELSE 0 END) as wicket_keepers
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            LEFT JOIN session_team_players stp ON stp.team_id = t.id
            LEFT JOIN session_players sp ON stp.session_player_id = sp.id
            LEFT JOIN players p ON sp.player_id = p.id
            WHERE t.owner_id = %s
            GROUP BY t.id, a.id
            ORDER BY a.created_at DESC
        """, (user_id,))
        
        rows = cursor.fetchall()
        if not rows:
            return []
        
        # Extract IDs for batch lookups
        auction_ids = list({r['auction_id'] for r in rows})
        team_ids = [r['id'] for r in rows]
        
        # === STEP 2: Batch fetch all sessions for all auctions ===
        session_data = _get_team_sessions(cursor, auction_ids, team_ids)
        
        # === STEP 3: Assemble final data ===
        teams = []
        for row in rows:
            aid = row['auction_id']
            sess_info = session_data.get(aid, {'total': 0, 'yours': 0, 'sessions': []})
            
            spent = float(row['spent'] or 0)
            reserved = float(row['reserved'] or 0)
            available = float(row['purse_limit'] or 0) - spent - reserved
            
            teams.append({
                'id': row['id'],
                'team_name': row['team_name'],
                'league_name': row['league_name'],
                'auction_status': row['auction_status'],
                'auction_id': aid,
                'purse_limit': float(row['purse_limit'] or 0),
                'available': available,
                'squad_count': row['squad_count'] or 0,
                'overseas': row['overseas_count'] or 0,
                'batsmen': row['batsmen'] or 0,
                'bowlers': row['bowlers'] or 0,
                'all_rounders': row['all_rounders'] or 0,
                'wicket_keepers': row['wicket_keepers'] or 0,
                'your_sessions_count': sess_info['yours'],
                'total_sessions': sess_info['total'],
                'sessions': sess_info['sessions'],  # For future use
            })
        
        return teams


@bp.route('/dashboard')
def dashboard():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    user_id = session['user_id']
    
    # Cache for 30 seconds — dashboard data changes slowly
    cache_key = f'dashboard:team_owner:{user_id}'
    teams = get_cached(cache_key, lambda: _fetch_dashboard_data(user_id), ttl_seconds=30)
    
    return render_template('team_owner/dashboard.html', teams=teams)


@bp.route('/auction-room/<int:auction_id>')
def auction_room_entry(auction_id):
    """Entry point — validates auction_id matches user's team, sets session, redirects."""
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    active_auction_id = session.get('active_auction_id')
    
    # Already in same auction? Redirect directly
    if active_auction_id == auction_id:
        return redirect('/team-owner/auction')
    
    # Already in different auction? Block
    if active_auction_id is not None:
        flash(f'You are already in auction room {active_auction_id}. Exit first to enter another.')
        return redirect('/team-owner/auction')
    
    # Validate and set session
    with db_transaction() as cursor:
        cursor.execute("""
            SELECT t.id, t.team_name, a.league_name, a.status as auction_status
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            WHERE t.auction_id = %s AND t.owner_id = %s
            LIMIT 1
        """, (auction_id, session['user_id']))
        team = cursor.fetchone()
    
    if not team:
        flash('You do not own a team in this auction')
        return redirect('/team-owner/dashboard')
    
    session['active_auction_id'] = auction_id
    session['active_team_id'] = team['id']
    session['active_league_name'] = team['league_name']
    session.pop('active_session_id', None)
    
    return redirect('/team-owner/auction')


@bp.route('/auction')
def auction_page():
    """Main auction page — session selector OR auction room."""
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    active_auction_id = session.get('active_auction_id')
    active_team_id = session.get('active_team_id')
    # If auction_id came from query param, we need to look up the team
    if not active_team_id and active_auction_id:
        cursor.execute("SELECT id FROM teams WHERE auction_id = %s AND owner_id = %s LIMIT 1", 
                    (active_auction_id, session['user_id']))
        team_row = cursor.fetchone()
        if team_row:
            active_team_id = team_row['id']
    

    
    with db_transaction() as cursor:
        # === STEP 1: Verify team + get auction info ===
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
        
        # === STEP 2: Get total teams count ===
        cursor.execute("SELECT COUNT(*) as total FROM teams WHERE auction_id = %s", (active_auction_id,))
        total_teams = cursor.fetchone()['total'] or 0
        
        # === STEP 3: Get all sessions for this auction ===
        cursor.execute("""
            SELECT id, session_name, status, start_time, end_time, 
                   team_ids, created_at
            FROM auction_sessions
            WHERE auction_id = %s
            ORDER BY 
                CASE status WHEN 'active' THEN 1 WHEN 'paused' THEN 2 ELSE 3 END,
                created_at DESC
        """, (active_auction_id,))
        raw_sessions = cursor.fetchall()
        
        # === STEP 4: Process sessions ===
        auction_sessions = []
        for sess in raw_sessions:
            # Parse team_ids
            team_ids = []
            raw = sess.get('team_ids')
            if raw:
                try:
                    team_ids = json.loads(raw) if isinstance(raw, str) else raw
                    team_ids = [int(t) for t in team_ids]
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
            
            is_member = team['id'] in team_ids if team_ids else False
            slots_filled = len(team_ids)
            
            # Icon based on session name
            name_lower = (sess.get('session_name') or '').lower()
            if 'morning' in name_lower:
                icon, label = '🌅', '🌅 Morning Session'
            elif 'evening' in name_lower or 'night' in name_lower:
                icon, label = '🌙', '🌙 Evening Session'
            elif 'weekend' in name_lower:
                icon, label = '🏖️', '🏖️ Weekend Session'
            elif 'afternoon' in name_lower:
                icon, label = '☀️', '☀️ Afternoon Session'
            else:
                icon, label = '📅', f'📅 {sess.get("session_name") or ("Session " + str(sess["id"]))}'
            
            auction_sessions.append({
                'id': sess['id'],
                'session_name': sess.get('session_name') or f'Session {sess["id"]}',
                'status': sess['status'],
                'start_time': str(sess['start_time'])[:16] if sess['start_time'] else None,
                'end_time': str(sess['end_time'])[:16] if sess['end_time'] else None,
                'team_ids_list': team_ids,
                'total_teams': total_teams,
                'slots_left': total_teams - slots_filled,
                'slots_filled': slots_filled,
                'is_member': is_member,
                'session_icon': icon,
                'time_label': label,
                'join_button_text': '🚀 Enter Auction Room' if is_member else '➕ Join Session',
            })
        
        # === STEP 5: Get all teams for lookup ===
        cursor.execute("SELECT id, team_name FROM teams WHERE auction_id = %s", (active_auction_id,))
        all_teams = {row['id']: row['team_name'] for row in cursor.fetchall()}
        
        # === STEP 6: Check active session ===
        active_session_id = session.get('active_session_id')
        has_active_session = False
        current_session = None
        players = []
        current_player = None
        current_bid = 0
        has_bids = False
        
        if active_session_id:
            cursor.execute("""
                SELECT id, session_name, status, start_time, end_time,
                       team_ids, current_player_id, current_bid
                FROM auction_sessions
                WHERE id = %s
            """, (active_session_id,))
            current_session = cursor.fetchone()
            
            if current_session:
                # Verify membership
                try:
                    sess_team_ids = json.loads(current_session['team_ids']) if isinstance(current_session['team_ids'], str) else current_session['team_ids'] or []
                    sess_team_ids = [int(t) for t in sess_team_ids]
                    has_active_session = team['id'] in sess_team_ids
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
                
                if has_active_session:
                    # Get players for this session
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
                    
                    # Get current player if any
                    cp_id = current_session.get('current_player_id')
                    if cp_id:
                        cursor.execute("""
                            SELECT sp.id as session_player_id, sp.base_price, sp.status,
                                   p.player_name, p.category, p.overseas, p.id as player_id
                            FROM session_players sp
                            JOIN players p ON sp.player_id = p.id
                            WHERE sp.id = %s AND sp.session_id = %s
                        """, (cp_id, active_session_id))
                        current_player = cursor.fetchone()
                        current_bid = float(current_session.get('current_bid') or 0)
                        
                        cursor.execute("""
                            SELECT COUNT(*) as cnt FROM session_bids 
                            WHERE session_id = %s AND session_player_id = %s
                        """, (active_session_id, cp_id))
                        has_bids = cursor.fetchone()['cnt'] > 0
        
        # === STEP 7: Get team stats for active session ===
        squad_count = 0
        overseas_count = 0
        if has_active_session and active_session_id:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                WHERE stp.team_id = %s AND sp.session_id = %s
            """, (team['id'], active_session_id))
            squad_count = cursor.fetchone()['cnt'] or 0
            
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                JOIN players p ON sp.player_id = p.id
                WHERE stp.team_id = %s AND p.overseas = TRUE AND sp.session_id = %s
            """, (team['id'], active_session_id))
            overseas_count = cursor.fetchone()['cnt'] or 0
        
        team['squad_count'] = squad_count
        team['overseas_count'] = overseas_count
        
        # Format times
        if current_session:
            current_session['start_time'] = str(current_session['start_time'])[:16] if current_session['start_time'] else None
            current_session['end_time'] = str(current_session['end_time'])[:16] if current_session['end_time'] else None
        
        auction_dict = {
            'id': active_auction_id,
            'league_name': team['league_name'],
            'status': team['auction_status'],
            'squad_size': team.get('squad_size', 18),
            'purse_limit': team.get('purse_limit', 100),
            'overseas_limit': team.get('overseas_limit', 8)
        }
    
    return render_template('team_owner/auction.html',
        team=team,
        user_team=team,
        auction=auction_dict,
        auction_id=active_auction_id,
        auction_sessions=auction_sessions,
        has_active_session=has_active_session,
        active_session_id=active_session_id,
        session_id=active_session_id,
        current_session=current_session,
        all_sessions=auction_sessions,
        all_teams=all_teams,
        players=players,
        total_teams=total_teams,
        current_player=current_player,
        current_bid=current_bid,
        has_bids=has_bids
    )


@bp.route('/select-session', methods=['POST'])
def select_session():
    """Handle session selection."""
    if session.get('role') != 'team_owner':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.get_json() or {}
    session_id = data.get('session_id')
    
    if not session_id:
        return jsonify({'success': False, 'message': 'Session ID required'}), 400
    
    active_auction_id = session.get('active_auction_id')
    if not active_auction_id:
        return jsonify({'success': False, 'message': 'No active auction'}), 400
    
    # Quick validation
    with db_transaction() as cursor:
        cursor.execute("""
            SELECT 1 FROM auction_sessions 
            WHERE id = %s AND auction_id = %s
            LIMIT 1
        """, (session_id, active_auction_id))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': 'Invalid session'}), 400
    
    session['active_session_id'] = session_id
    
    # Clear cache for this user
    clear_cache(f'dashboard:team_owner:{session["user_id"]}')
    
    return jsonify({'success': True, 'redirect': '/team-owner/auction'})


@bp.route('/exit-auction')
def exit_auction():
    """Clear all auction/session context."""
    session.pop('active_auction_id', None)
    session.pop('active_team_id', None)
    session.pop('active_league_name', None)
    session.pop('active_session_id', None)
    return redirect('/team-owner/dashboard')

@bp.route('/change-password', methods=['POST'])
def change_password():
    """Team owner changes their own password — stored as plain text."""
    if session.get('role') != 'team_owner':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.get_json() or {}
    new_pwd = data.get('new_password', '').strip()
    confirm = data.get('confirm_password', '').strip()
    
    if not new_pwd or not confirm:
        return jsonify({'success': False, 'message': 'Both fields are required'}), 400
    
    if new_pwd != confirm:
        return jsonify({'success': False, 'message': 'Passwords do not match'}), 400
    
    user_id = session['user_id']
    
    with db_transaction() as cursor:
        cursor.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (new_pwd, user_id)
        )
    
    return jsonify({'success': True, 'message': 'Password saved'})
