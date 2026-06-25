from flask import Blueprint, render_template, session, flash, redirect, jsonify, request, abort
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('team_owner_dashboard', __name__, url_prefix='/team-owner')


@bp.route('/dashboard')
def dashboard():
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # ONE query gets everything
        cursor.execute("""
            SELECT 
                t.*,
                a.league_name, a.status as auction_status, a.id as auction_id,
                COUNT(DISTINCT tp.id) as squad_count,
                COUNT(DISTINCT CASE WHEN p.overseas = TRUE THEN tp.id END) as overseas,
                SUM(CASE WHEN p.category = 'batsman' THEN 1 ELSE 0 END) as batsmen,
                SUM(CASE WHEN p.category = 'bowler' THEN 1 ELSE 0 END) as bowlers,
                SUM(CASE WHEN p.category = 'all_rounder' THEN 1 ELSE 0 END) as all_rounders,
                SUM(CASE WHEN p.category = 'wicket_keeper' THEN 1 ELSE 0 END) as wicket_keepers
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            LEFT JOIN team_players tp ON tp.team_id = t.id
            LEFT JOIN auction_players ap ON tp.auction_player_id = ap.id AND ap.auction_id = a.id
            LEFT JOIN players p ON ap.player_id = p.id
            WHERE t.owner_id = %s
            GROUP BY t.id, a.id
            ORDER BY a.created_at DESC
        """, (session['user_id'],))
        teams = cursor.fetchall()
        
        for team in teams:
            spent = float(team['spent'] or 0)
            reserved = float(team['reserved'] or 0)
            team['available'] = float(team['purse_limit']) - spent - reserved
            
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/dashboard.html', teams=teams)

@bp.route('/auction-room/<int:auction_id>')
def auction_room_entry(auction_id):
    """Entry point - validates auction_id matches user's team, sets session, redirects"""
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    # Check if user already has an active auction in session
    active_auction_id = session.get('active_auction_id')
    
    # If already in an auction room, verify it's the same one
    if active_auction_id is not None:
        if active_auction_id != auction_id:
            # User is trying to enter a different auction room while already in one
            flash(f'You are already in auction room {active_auction_id}. Exit first to enter another.')
            return redirect('/team-owner/auction')
        # Same auction - just redirect to auction page
        return redirect('/team-owner/auction')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify this user owns a team in THIS SPECIFIC auction
        cursor.execute("""
            SELECT t.*, a.league_name, a.status as auction_status
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            WHERE t.auction_id = %s AND t.owner_id = %s
        """, (auction_id, session['user_id']))
        team = cursor.fetchone()
        
    finally:
        cursor.close()
        db.close()
    
    if not team:
        flash('You do not own a team in this auction')
        return redirect('/team-owner/dashboard')
    
    # Set auction context in session
    session['active_auction_id'] = auction_id
    session['active_team_id'] = team['id']
    session['active_league_name'] = team['league_name']
    # Clear any previous session selection - user must select again
    session.pop('active_session_id', None)
    
    return redirect('/team-owner/auction')


@bp.route('/auction')
def auction_page():
    """Main auction page - shows session selector first, then auction room after joining"""
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
        
        # Process sessions with enriched data for template
        auction_sessions = []
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
            slots_left = total_teams - slots_filled
            
            # Determine icon and labels based on session name
            session_name = sess['session_name'] or f'Session {sess["id"]}'
            name_lower = session_name.lower()
            
            if 'morning' in name_lower:
                session_icon = '🌅'
                time_label = '🌅 Morning Session'
                join_text = '➕ Join Morning Session'
            elif 'evening' in name_lower or 'night' in name_lower:
                session_icon = '🌙'
                time_label = '🌙 Evening Session'
                join_text = '➕ Join Evening Session'
            elif 'weekend' in name_lower:
                session_icon = '🏖️'
                time_label = '🏖️ Weekend Session'
                join_text = '➕ Join Weekend Session'
            elif 'afternoon' in name_lower:
                session_icon = '☀️'
                time_label = '☀️ Afternoon Session'
                join_text = '➕ Join Afternoon Session'
            else:
                session_icon = '📅'
                time_label = f'📅 {session_name}'
                join_text = '➕ Join Session'
            
            auction_sessions.append({
                'id': sess['id'],
                'session_name': session_name,
                'status': sess['status'],
                'start_time': str(sess['start_time'])[:16] if sess['start_time'] else None,
                'end_time': str(sess['end_time'])[:16] if sess['end_time'] else None,
                'team_ids_list': team_ids,
                'total_teams': total_teams,
                'slots_left': slots_left,
                'slots_filled': slots_filled,
                'is_member': is_member,
                'session_icon': session_icon,
                'time_label': time_label,
                'join_button_text': join_text if not is_member else '🚀 Enter Auction Room',
                'created_at': str(sess['created_at']) if sess['created_at'] else None
            })
        
        # Get all teams for names lookup
        cursor.execute("SELECT id, team_name FROM teams WHERE auction_id = %s", (active_auction_id,))
        all_teams = {row['id']: row['team_name'] for row in cursor.fetchall()}
        
        # Check if user already has an active session selected
        active_session_id = session.get('active_session_id')
        has_active_session = False
        current_session = None
        
        if active_session_id:
            cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (active_session_id,))
            current_session = cursor.fetchone()
            if current_session:
                # Verify user is still in this session
                try:
                    sess_team_ids = json.loads(current_session['team_ids']) if isinstance(current_session['team_ids'], str) else current_session['team_ids'] or []
                    sess_team_ids = [int(tid) for tid in sess_team_ids]
                    if team['id'] in sess_team_ids:
                        has_active_session = True
                except:
                    pass
        
        # ============================================
        # GET SESSION PLAYERS (if in active session)
        # ============================================
        players = []
        if has_active_session and active_session_id:
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
        
        # ============================================
        # GET CURRENT AUCTION STATE (if in active session)
        # ============================================
        current_player = None
        current_bid = 0
        has_bids = False
        
        if has_active_session and current_session and current_session.get('current_player_id'):
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
        
        # Format current_session times for template
        if current_session:
            current_session['start_time'] = str(current_session['start_time'])[:16] if current_session['start_time'] else None
            current_session['end_time'] = str(current_session['end_time'])[:16] if current_session['end_time'] else None
        
        # Build auction dict (template expects this)
        auction_dict = {
            'id': active_auction_id,
            'league_name': team['league_name'],
            'status': team['auction_status'],
            'squad_size': team.get('squad_size', 18),
            'purse_limit': team.get('purse_limit', 100),
            'overseas_limit': team.get('overseas_limit', 8)
        }
        
        # Calculate team stats for display
        squad_count = 0
        overseas_count = 0
        if has_active_session and active_session_id:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                WHERE stp.team_id = %s AND sp.session_id = %s
            """, (team['id'], active_session_id))
            result = cursor.fetchone()
            squad_count = result['cnt'] if result else 0
            
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                JOIN players p ON sp.player_id = p.id
                WHERE stp.team_id = %s AND p.overseas = TRUE AND sp.session_id = %s
            """, (team['id'], active_session_id))
            result = cursor.fetchone()
            overseas_count = result['cnt'] if result else 0
        
        team['squad_count'] = squad_count
        team['overseas_count'] = overseas_count
        
    finally:
        cursor.close()
        db.close()
    
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
    """Handle session selection from dropdown"""
    if session.get('role') != 'team_owner':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.get_json()
    session_id = data.get('session_id')
    
    if not session_id:
        return jsonify({'success': False, 'message': 'Session ID required'}), 400
    
    active_auction_id = session.get('active_auction_id')
    
    if not active_auction_id:
        return jsonify({'success': False, 'message': 'No active auction'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify session belongs to this auction
        cursor.execute("""
            SELECT * FROM auction_sessions 
            WHERE id = %s AND auction_id = %s
        """, (session_id, active_auction_id))
        sess = cursor.fetchone()
        
        if not sess:
            return jsonify({'success': False, 'message': 'Invalid session for this auction'}), 400
        
    finally:
        cursor.close()
        db.close()
    
    # Set active session in session
    session['active_session_id'] = session_id
    
    return jsonify({'success': True, 'redirect': '/team-owner/auction'})


@bp.route('/exit-auction')
def exit_auction():
    """Clear all auction/session context and return to dashboard"""
    session.pop('active_auction_id', None)
    session.pop('active_team_id', None)
    session.pop('active_league_name', None)
    session.pop('active_session_id', None)
    return redirect('/team-owner/dashboard')