from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('admin_sessions', __name__, url_prefix='/admin/sessions')

SESSION_SLOTS = {
    'morning': {'start': '09:00', 'end': '12:00', 'label': 'Morning'},
    'afternoon': {'start': '14:00', 'end': '17:00', 'label': 'Afternoon'},
    'evening': {'start': '17:00', 'end': '20:00', 'label': 'Evening'},
    'night': {'start': '20:00', 'end': '23:00', 'label': 'Night'},
    'custom': {'start': '', 'end': '', 'label': 'Custom'}
}



@bp.route('/')
def list_sessions():
    """Main sessions page - shows auction context and session management"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    auction_id = session.get('active_auction_id')
    if not auction_id:
        flash('Please enter an auction room first')
        return redirect('/admin/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        
        cursor.execute("""
            SELECT t.*, u.username as owner_name
            FROM teams t
            LEFT JOIN users u ON t.owner_id = u.id
            WHERE t.auction_id = %s
            ORDER BY t.created_at DESC
        """, (auction_id,))
        teams = cursor.fetchall()
        
        # Get sessions with player counts
        cursor.execute("""
            SELECT s.*, 
                   (SELECT COUNT(*) FROM teams WHERE JSON_CONTAINS(s.team_ids, CAST(id AS JSON))) as team_count,
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id) as player_count,
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id AND sp.status = 'sold') as sold_count
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY s.created_at DESC
        """, (auction_id,))
        sessions = cursor.fetchall()
        
        for sess in sessions:
            if sess['team_ids']:
                try:
                    sess['team_ids_list'] = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                except:
                    sess['team_ids_list'] = []
            else:
                sess['team_ids_list'] = []
        
        total_teams = len(teams)
        for sess in sessions:
            participating = len(sess['team_ids_list'])
            sess['participating'] = participating
            sess['remaining'] = total_teams - participating
            sess['percentage'] = (participating / total_teams * 100) if total_teams > 0 else 0
        
        active_team_ids = set()
        for sess in sessions:
            if sess['status'] == 'active':
                active_team_ids.update(sess['team_ids_list'])
        
        available_teams = [t for t in teams if t['id'] not in active_team_ids]
        
        # Check if any session has players assigned
        for sess in sessions:
            sess['has_players'] = (sess['player_count'] or 0) > 0
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/sessions.html',
        auction=auction,
        teams=teams,
        sessions=sessions,
        available_teams=available_teams,
        total_teams=total_teams,
        session_slots=SESSION_SLOTS
    )


@bp.route('/create', methods=['POST'])
def create_session():
    """Create a new session - then redirect to player assignment"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = session.get('active_auction_id')
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    session_name = request.form.get('session_name', '')
    slot_type = request.form.get('slot_type', 'custom')
    custom_start = request.form.get('custom_start', '')
    custom_end = request.form.get('custom_end', '')
    team_ids = request.form.getlist('team_ids')
    
    if len(team_ids) < 2:
        flash('Need at least 2 teams for a session')
        return redirect('/admin/sessions')
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO auction_sessions (auction_id, session_name, status, start_time, end_time, team_ids)
            VALUES (%s, %s, 'active', %s, %s, %s)
        """, (
            auction_id, 
            session_name, 
            custom_start, 
            custom_end, 
            json.dumps([int(t) for t in team_ids])
        ))
        db.commit()
        new_session_id = cursor.lastrowid
        
    finally:
        cursor.close()
        db.close()
    
    flash(f'Session "{session_name}" created! Now assign players.')
    return redirect(f'/admin/sessions/{new_session_id}/assign-players')


@bp.route('/<int:session_id>/assign-players')
def assign_players_page(session_id):
    """Page to assign players to a session - shows previous session options"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    auction_id = session.get('active_auction_id')
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get current session
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        current_sess = cursor.fetchone()
        
        if not current_sess:
            flash('Session not found')
            return redirect('/admin/sessions')
        
        # Get all previous sessions in this auction (before current)
        cursor.execute("""
            SELECT s.*, 
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id AND sp.status = 'sold') as sold_count,
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id AND sp.status = 'available') as available_count,
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id) as total_count
            FROM auction_sessions s
            WHERE s.auction_id = %s AND s.id < %s AND s.status = 'completed'
            ORDER BY s.created_at DESC
        """, (auction_id, session_id))
        previous_sessions = cursor.fetchall()
        
        # Get all master players (from auction_players)
        cursor.execute("""
            SELECT p.*, ap.base_price as default_base_price, ap.status as master_status
            FROM players p
            JOIN auction_players ap ON p.id = ap.player_id
            WHERE ap.auction_id = %s
            ORDER BY p.player_name
        """, (auction_id,))
        all_players = cursor.fetchall()
        
        # Check if this session already has players
        cursor.execute("SELECT COUNT(*) as cnt FROM session_players WHERE session_id = %s", (session_id,))
        has_players = cursor.fetchone()['cnt'] > 0
        
        # If session already has players, show them
        session_players = []
        if has_players:
            cursor.execute("""
                SELECT sp.*, p.player_name, p.category, p.overseas
                FROM session_players sp
                JOIN players p ON sp.player_id = p.id
                WHERE sp.session_id = %s
                ORDER BY p.player_name
            """, (session_id,))
            session_players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/players.html',
        session=current_sess,
        previous_sessions=previous_sessions,
        all_players=all_players,
        has_players=has_players,
        session_players=session_players
    )


@bp.route('/<int:session_id>/assign-players', methods=['POST'])
def assign_players(session_id):
    """Assign players to session - either from previous session or fresh selection"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json() or request.form
    source_type = data.get('source_type')  # 'previous', 'fresh', 'same_set'
    previous_session_id = data.get('previous_session_id')
    player_ids = data.getlist('player_ids') if hasattr(data, 'getlist') else data.get('player_ids', [])
    if isinstance(player_ids, str):
        player_ids = [player_ids]
    
    auction_id = session.get('active_auction_id')
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify session exists and belongs to this auction
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s AND auction_id = %s", (session_id, auction_id))
        sess = cursor.fetchone()
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        # Clear any existing session players for this session
        cursor.execute("DELETE FROM session_players WHERE session_id = %s", (session_id,))
        
        if source_type == 'previous' and previous_session_id:
            # Copy UNSOLD players from previous session with fresh status
            cursor.execute("""
                SELECT sp.*, p.player_name
                FROM session_players sp
                JOIN players p ON sp.player_id = p.id
                WHERE sp.session_id = %s AND sp.status != 'sold'
            """, (previous_session_id,))
            unsold_players = cursor.fetchall()
            
            for p in unsold_players:
                cursor.execute("""
                    INSERT INTO session_players (session_id, player_id, base_price, status)
                    VALUES (%s, %s, %s, 'available')
                """, (session_id, p['player_id'], p['base_price']))
            
            # Also copy SOLD players but keep them as available for this new session
            # (Each session is independent - Russell can be sold in morning AND night)
            cursor.execute("""
                SELECT sp.* FROM session_players sp
                WHERE sp.session_id = %s AND sp.status = 'sold'
            """, (previous_session_id,))
            sold_players = cursor.fetchall()
            
            for p in sold_players:
                cursor.execute("""
                    INSERT INTO session_players (session_id, player_id, base_price, status)
                    VALUES (%s, %s, %s, 'available')
                """, (session_id, p['player_id'], p['base_price']))
            
            count = len(unsold_players) + len(sold_players)
            message = f'Copied {count} players from previous session (all reset to available for bidding)'
            
        elif source_type == 'same_set':
            # Use EXACT same player set as previous session (same IDs)
            cursor.execute("""
                SELECT sp.* FROM session_players sp
                WHERE sp.session_id = %s
            """, (previous_session_id,))
            prev_players = cursor.fetchall()
            
            for p in prev_players:
                cursor.execute("""
                    INSERT INTO session_players (session_id, player_id, base_price, status)
                    VALUES (%s, %s, %s, 'available')
                """, (session_id, p['player_id'], p['base_price']))
            
            count = len(prev_players)
            message = f'Using same set of {count} players (all reset to available)'
            
        else:
            # Fresh selection - user selected specific players
            if not player_ids:
                return jsonify({'error': 'No players selected'}), 400
            
            # Get base prices from auction_players
            format_ids = ','.join(['%s'] * len(player_ids))
            cursor.execute(f"""
                SELECT ap.player_id, ap.base_price 
                FROM auction_players ap 
                WHERE ap.auction_id = %s AND ap.player_id IN ({format_ids})
            """, (auction_id,) + tuple(int(p) for p in player_ids))
            player_data = {r['player_id']: r['base_price'] for r in cursor.fetchall()}
            
            for pid in player_ids:
                pid = int(pid)
                base_price = player_data.get(pid, 2.0)
                cursor.execute("""
                    INSERT INTO session_players (session_id, player_id, base_price, status)
                    VALUES (%s, %s, %s, 'available')
                """, (session_id, pid, base_price))
            
            count = len(player_ids)
            message = f'Assigned {count} players to session'
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'message': message, 'count': count})


@bp.route('/<int:session_id>/players')
def get_session_players(session_id):
    """Get players in a session with their session-local status"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT sp.*, p.player_name, p.category, p.overseas
            FROM session_players sp
            JOIN players p ON sp.player_id = p.id
            WHERE sp.session_id = %s
            ORDER BY p.player_name
        """, (session_id,))
        players = cursor.fetchall()
        
        # Stats
        stats = {
            'total': len(players),
            'sold': sum(1 for p in players if p['status'] == 'sold'),
            'available': sum(1 for p in players if p['status'] == 'available'),
            'in_auction': sum(1 for p in players if p['status'] == 'in_auction'),
            'unsold': sum(1 for p in players if p['status'] == 'unsold')
        }
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'players': players, 'stats': stats})


@bp.route('/<int:session_id>/enter')
def enter_session_room(session_id):
    """Enter auction room for a specific session - shows ONLY session players"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        sess = cursor.fetchone()
        
        if not sess:
            flash('Session not found')
            return redirect('/admin/sessions')
        
        # Get session team IDs
        team_ids = []
        if sess['team_ids']:
            try:
                team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                team_ids = []
        
        # Get participating teams
        teams = []
        if team_ids:
            format_ids = ','.join(['%s'] * len(team_ids))
            cursor.execute(f"""
                SELECT t.*, 
                       (t.purse_limit - COALESCE(t.spent, 0) - COALESCE(t.reserved, 0)) as available_purse
                FROM teams t
                WHERE t.id IN ({format_ids})
            """, tuple(team_ids))
            teams = cursor.fetchall()
        
        # Get session players (NOT global players - session-local status)
        cursor.execute("""
            SELECT sp.*, p.player_name, p.category, p.overseas
            FROM session_players sp
            JOIN players p ON sp.player_id = p.id
            WHERE sp.session_id = %s
            ORDER BY sp.base_price DESC
        """, (session_id,))
        players = cursor.fetchall()
        
        # Get session-specific bid history
        cursor.execute("""
            SELECT b.*, p.player_name, t.team_name
            FROM bids b
            JOIN session_players sp ON b.auction_player_id = sp.id
            JOIN players p ON sp.player_id = p.id
            JOIN teams t ON b.team_id = t.id
            WHERE b.session_id = %s
            ORDER BY b.created_at DESC
            LIMIT 50
        """, (session_id,))
        session_bids = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/sessions.html',
        session=sess,
        teams=teams,
        players=players,
        session_bids=session_bids,
        auction={'id': sess['auction_id']}
    )


@bp.route('/<int:session_id>/close', methods=['POST'])
def close_session(session_id):
    """Close session and summarize"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get summary before closing
        cursor.execute("""
            SELECT status, COUNT(*) as cnt 
            FROM session_players 
            WHERE session_id = %s 
            GROUP BY status
        """, (session_id,))
        summary = {r['status']: r['cnt'] for r in cursor.fetchall()}
        
        cursor.execute("""
            UPDATE auction_sessions 
            SET status = 'completed', end_time = NOW() 
            WHERE id = %s
        """, (session_id,))
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    sold = summary.get('sold', 0)
    unsold = summary.get('available', 0) + summary.get('unsold', 0)
    
    flash(f'Session closed! {sold} sold, {unsold} unsold. Unsold players available for next session.')
    return redirect('/admin/sessions')


@bp.route('/<int:session_id>/remove-team/<int:team_id>', methods=['POST'])
def remove_team_from_session(session_id, team_id):
    """Remove a team from session"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        sess = cursor.fetchone()
        
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        existing_ids = []
        if sess['team_ids']:
            try:
                existing_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                existing_ids = []
        
        if team_id in existing_ids:
            existing_ids.remove(team_id)
            cursor.execute("""
                UPDATE auction_sessions SET team_ids = %s WHERE id = %s
            """, (json.dumps(existing_ids), session_id))
            db.commit()
            return jsonify({'success': True, 'message': 'Team removed', 'remaining': len(existing_ids)})
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': False, 'error': 'Team not in session'})


@bp.route('/<int:session_id>/status')
def get_session_status(session_id):
    """Get real-time session status with player stats"""
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT current_bid, current_bidder_id, current_player_id
            FROM auction_sessions WHERE id = %s
        """, (session_id,))
        sess = cursor.fetchone()
        
        cursor.execute("""
            SELECT status, COUNT(*) as cnt 
            FROM session_players 
            WHERE session_id = %s 
            GROUP BY status
        """, (session_id,))
        player_stats = {r['status']: r['cnt'] for r in cursor.fetchall()}
        
    finally:
        cursor.close()
        db.close()
    
    if not sess:
        return jsonify({'error': 'Session not found'}), 404
    
    return jsonify({
        'current_bid': float(sess['current_bid'] or 0),
        'current_bidder': sess['current_bidder_id'],
        'current_player': sess['current_player_id'],
        'player_stats': player_stats
    })