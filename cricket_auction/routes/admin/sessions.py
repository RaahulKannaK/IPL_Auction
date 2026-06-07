from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('admin_sessions', __name__, url_prefix='/admin/sessions')

# Session time slots configuration
SESSION_SLOTS = {
    'morning': {'name': 'Morning', 'start': '06:00', 'end': '12:00'},
    'afternoon': {'name': 'Afternoon', 'start': '12:00', 'end': '17:00'},
    'evening': {'name': 'Evening', 'start': '17:00', 'end': '21:00'},
    'night': {'name': 'Night', 'start': '21:00', 'end': '23:59'}
}

@bp.route('/')
def list_sessions():
    """Main sessions page - shows auction context and session management"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    auction_id = session.get('active_auction_id')
    if not auction_id:
        flash('Please enter an auction room first')
        return redirect('/admin/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Current auction details
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        
        # All teams in this auction
        cursor.execute("""
            SELECT t.*, u.username as owner_name
            FROM teams t
            LEFT JOIN users u ON t.owner_id = u.id
            WHERE t.auction_id = %s
            ORDER BY t.created_at DESC
        """, (auction_id,))
        teams = cursor.fetchall()
        
        # All sessions for this auction
        cursor.execute("""
            SELECT s.*, 
                   (SELECT COUNT(*) FROM teams WHERE JSON_CONTAINS(s.team_ids, CAST(id AS JSON))) as team_count
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY s.created_at DESC
        """, (auction_id,))
        sessions = cursor.fetchall()
        
        # Parse team_ids JSON for each session
        for sess in sessions:
            if sess['team_ids']:
                try:
                    sess['team_ids_list'] = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                except:
                    sess['team_ids_list'] = []
            else:
                sess['team_ids_list'] = []
        
        # Calculate session stats
        total_teams = len(teams)
        for sess in sessions:
            participating = len(sess['team_ids_list'])
            sess['participating'] = participating
            sess['remaining'] = total_teams - participating
            sess['percentage'] = (participating / total_teams * 100) if total_teams > 0 else 0
        
        # Available teams for new session (not in any active session)
        active_team_ids = set()
        for sess in sessions:
            if sess['status'] == 'active':
                active_team_ids.update(sess['team_ids_list'])
        
        available_teams = [t for t in teams if t['id'] not in active_team_ids]
        
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
    """Create a new session with selected teams and time slot"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = session.get('active_auction_id')
    if not auction_id:
        return jsonify({'error': 'No active auction'}), 400
    
    session_name = request.form.get('session_name', '')
    slot_type = request.form.get('slot_type', 'morning')
    custom_start = request.form.get('custom_start', '')
    custom_end = request.form.get('custom_end', '')
    team_ids = request.form.getlist('team_ids')
    
    if len(team_ids) < 2:
        flash('Need at least 2 teams for a session')
        return redirect('/admin/sessions')
    
    # Get time range
    slot = SESSION_SLOTS.get(slot_type, SESSION_SLOTS['morning'])
    start_time = custom_start if custom_start else slot['start']
    end_time = custom_end if custom_end else slot['end']
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO auction_sessions (auction_id, session_name, status, start_time, end_time, team_ids)
            VALUES (%s, %s, 'active', %s, %s, %s)
        """, (
            auction_id, 
            session_name, 
            start_time, 
            end_time, 
            json.dumps([int(t) for t in team_ids])
        ))
        db.commit()
        session_id = cursor.lastrowid
        
    finally:
        cursor.close()
        db.close()
    
    flash(f'Session "{session_name}" created with {len(team_ids)} teams!')
    return redirect('/admin/sessions')

@bp.route('/<int:session_id>/teams')
def get_session_teams(session_id):
    """Get teams participating in a session"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        sess = cursor.fetchone()
        
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        team_ids = []
        if sess['team_ids']:
            try:
                team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                team_ids = []
        
        if team_ids:
            format_ids = ','.join(['%s'] * len(team_ids))
            cursor.execute(f"""
                SELECT t.*, u.username as owner_name
                FROM teams t
                LEFT JOIN users u ON t.owner_id = u.id
                WHERE t.id IN ({format_ids})
            """, tuple(team_ids))
            teams = cursor.fetchall()
        else:
            teams = []
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'session_id': session_id,
        'session_name': sess['session_name'],
        'teams': teams,
        'team_count': len(teams)
    })

@bp.route('/<int:session_id>/add-teams', methods=['POST'])
def add_teams_to_session(session_id):
    """Add teams to an existing session"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    new_team_ids = request.form.getlist('team_ids')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        sess = cursor.fetchone()
        
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        # Get existing teams
        existing_ids = []
        if sess['team_ids']:
            try:
                existing_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                existing_ids = []
        
        # Merge and deduplicate
        all_ids = list(set(existing_ids + [int(t) for t in new_team_ids]))
        
        cursor.execute("""
            UPDATE auction_sessions SET team_ids = %s WHERE id = %s
        """, (json.dumps(all_ids), session_id))
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash(f'Added {len(new_team_ids)} teams to session!')
    return redirect('/admin/sessions')

@bp.route('/<int:session_id>/remove-team/<int:team_id>', methods=['POST'])
def remove_team_from_session(session_id, team_id):
    """Remove a team from session (withdrawal)"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        sess = cursor.fetchone()
        
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        # Get existing teams
        existing_ids = []
        if sess['team_ids']:
            try:
                existing_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                existing_ids = []
        
        # Remove team
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

@bp.route('/<int:session_id>/close', methods=['POST'])
def close_session(session_id):
    """Close a session and make teams available for next session"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            UPDATE auction_sessions SET status = 'completed', end_time = NOW() 
            WHERE id = %s
        """, (session_id,))
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash('Session closed! Teams are now available for next session.')
    return redirect('/admin/sessions')

@bp.route('/<int:session_id>/continue', methods=['POST'])
def continue_session(session_id):
    """Continue session - create new session with remaining/unused teams"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        old_session = cursor.fetchone()
        
        if not old_session:
            return jsonify({'error': 'Session not found'}), 404
        
        auction_id = old_session['auction_id']
        
        # Get all teams in auction
        cursor.execute("SELECT id FROM teams WHERE auction_id = %s", (auction_id,))
        all_teams = [t['id'] for t in cursor.fetchall()]
        
        # Get teams used in any active/completed session
        cursor.execute("""
            SELECT team_ids FROM auction_sessions 
            WHERE auction_id = %s AND status IN ('active', 'completed')
        """, (auction_id,))
        used_teams = set()
        for row in cursor.fetchall():
            if row['team_ids']:
                try:
                    ids = json.loads(row['team_ids']) if isinstance(row['team_ids'], str) else row['team_ids']
                    used_teams.update(ids)
                except:
                    pass
        
        # Available teams = all - used
        available_teams = [t for t in all_teams if t not in used_teams]
        
        if len(available_teams) < 2:
            flash('Not enough remaining teams for a new session!')
            return redirect('/admin/sessions')
        
        # Create continuation session
        cursor.execute("""
            INSERT INTO auction_sessions (auction_id, session_name, team_ids, status, start_time)
            VALUES (%s, %s, %s, 'active', NOW())
        """, (
            auction_id,
            old_session['session_name'] + ' (Continued)',
            json.dumps(available_teams)
        ))
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash(f'Continuation session created with {len(available_teams)} remaining teams!')
    return redirect('/admin/sessions')

@bp.route('/history')
def session_history():
    """Get full session history for current auction"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = session.get('active_auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT s.*, a.league_name
            FROM auction_sessions s
            JOIN auctions a ON s.auction_id = a.id
            WHERE s.auction_id = %s
            ORDER BY s.created_at DESC
        """, (auction_id,))
        sessions = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'sessions': sessions})

@bp.route('/<int:session_id>/bid', methods=['POST'])
def place_bid(session_id):
    """Place bid in a session"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    player_id = data.get('player_id')
    team_id = data.get('team_id')
    amount = data.get('amount')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Update session current bid
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_bid = %s, current_bidder_id = %s, current_player_id = %s
            WHERE id = %s
        """, (amount, team_id, player_id, session_id))
        db.commit()
        
        # Record bid history
        cursor.execute("""
            INSERT INTO session_bids (session_id, player_id, team_id, amount, created_at)
            VALUES (%s, %s, %s, %s, NOW())
        """, (session_id, player_id, team_id, amount))
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'success': True,
        'current_bid': amount,
        'bidder': f'Team #{team_id}'
    })

@bp.route('/<int:session_id>/status')
def get_session_status(session_id):
    """Get real-time session status"""
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT current_bid, current_bidder_id, current_player_id
            FROM auction_sessions WHERE id = %s
        """, (session_id,))
        sess = cursor.fetchone()
        
    finally:
        cursor.close()
        db.close()
    
    if not sess:
        return jsonify({'error': 'Session not found'}), 404
    
    return jsonify({
        'current_bid': float(sess['current_bid'] or 0),
        'current_bidder': sess['current_bidder_id'],
        'current_player': sess['current_player_id']
    })

@bp.route('/<int:auction_id>/master-status')
def get_master_status(auction_id):
    """Get master bid across all sessions"""
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT MAX(current_bid) as master_bid,
                   current_bidder_id,
                   current_player_id
            FROM auction_sessions
            WHERE auction_id = %s AND status = 'active'
        """, (auction_id,))
        result = cursor.fetchone()
        
        cursor.execute("""
            SELECT COUNT(*) as active_count
            FROM auction_sessions
            WHERE auction_id = %s AND status = 'active'
        """, (auction_id,))
        active = cursor.fetchone()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'master_bid': float(result['master_bid'] or 0),
        'master_bidder': f'Team #{result["current_bidder_id"]}' if result['current_bidder_id'] else 'None',
        'master_player': f'Player #{result["current_player_id"]}' if result['current_player_id'] else 'None',
        'active_sessions': active['active_count']
    })

@bp.route('/room/<int:session_id>')
def session_room(session_id):
    """Enter a session room for bidding"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
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
        
        # Get available players
        cursor.execute("""
            SELECT * FROM players 
            WHERE status = 'available' AND auction_id = %s
            ORDER BY base_price DESC
        """, (sess['auction_id'],))
        players = cursor.fetchall()
        
        # Get bid history
        cursor.execute("""
            SELECT b.*, p.player_name, t.team_name
            FROM session_bids b
            JOIN players p ON b.player_id = p.id
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
        view='room',
        session=sess,
        teams=teams,
        players=players,
        session_bids=session_bids,
        auction={'id': sess['auction_id']}
    )