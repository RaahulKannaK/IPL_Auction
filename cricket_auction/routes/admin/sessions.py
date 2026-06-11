from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db

bp = Blueprint('admin_sessions', __name__, url_prefix='/admin')

@bp.route('/sessions')
def sessions_page():
    if not session.get('user_id'):
        return redirect('/')
    
    # Allow admin, auctioneer, owner, AND team_owner
    if session.get('role') not in ['admin', 'auctioneer', 'owner', 'team_owner']:
        flash('Unauthorized')
        return redirect('/')
    
    auction_id = request.args.get('auction', type=int)
    
    if not auction_id:
        flash('No auction specified')
        return redirect('/admin/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify auction exists
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            flash('Auction not found')
            return redirect('/admin/')
        
        # Store in session
        session['active_auction_id'] = auction_id
        session['active_league_name'] = auction['league_name']
        
        # Get ALL teams in this auction
        cursor.execute("""
            SELECT t.*, 
                   u1.username as owner_1_name,
                   u2.username as owner_2_name,
                   u3.username as owner_3_name
            FROM teams t
            LEFT JOIN users u1 ON t.owner_id = u1.id
            LEFT JOIN users u2 ON t.owner_id_2 = u2.id
            LEFT JOIN users u3 ON t.owner_id_3 = u3.id
            WHERE t.auction_id = %s
            ORDER BY t.team_name
        """, (auction_id,))
        all_teams = cursor.fetchall()
        
        # Get sessions for this auction with team counts
        cursor.execute("""
            SELECT s.*, 
                   (SELECT COUNT(*) FROM session_teams st WHERE st.session_id = s.id) as participating,
                   (SELECT GROUP_CONCAT(team_id) FROM session_teams st WHERE st.session_id = s.id) as team_ids_list
            FROM auction_sessions s 
            WHERE s.auction_id = %s
            ORDER BY s.created_at DESC
        """, (auction_id,))
        sessions = cursor.fetchall()
        
        # Process team_ids_list from string to list
        for sess in sessions:
            if sess['team_ids_list']:
                sess['team_ids_list'] = [int(x) for x in sess['team_ids_list'].split(',')]
            else:
                sess['team_ids_list'] = []
            # Calculate remaining and percentage
            total = len(all_teams)
            participating = sess['participating'] or 0
            sess['remaining'] = total - participating
            sess['percentage'] = (participating / total * 100) if total > 0 else 0
        
        # Find teams NOT in any active session
        active_session_ids = [s['id'] for s in sessions if s['status'] in ('active', 'live', 'paused')]
        
        if active_session_ids:
            format_ids = ','.join(['%s'] * len(active_session_ids))
            cursor.execute(f"""
                SELECT DISTINCT team_id FROM session_teams 
                WHERE session_id IN ({format_ids})
            """, tuple(active_session_ids))
            busy_team_ids = [row['team_id'] for row in cursor.fetchall()]
        else:
            busy_team_ids = []
        
        available_teams = [t for t in all_teams if t['id'] not in busy_team_ids]
        
        # Stats
        stats = {
            'total': len(all_teams),
            'assigned': len([t for t in all_teams if t.get('owner_id')]),
            'unassigned': len([t for t in all_teams if not t.get('owner_id')])
        }
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/sessions.html',
        auction=auction,
        sessions=sessions,
        all_teams=all_teams,
        available_teams=available_teams,
        total_teams=len(all_teams),
        stats=stats
    )


@bp.route('/sessions/create', methods=['POST'])
def create_session():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = request.form.get('auction_id', type=int)
    session_name = request.form.get('session_name')
    slot_type = request.form.get('slot_type', 'morning')
    team_ids = request.form.getlist('team_ids', type=int)
    
    # Time slots
    time_slots = {
        'morning': ('06:00:00', '12:00:00'),
        'afternoon': ('12:00:00', '17:00:00'),
        'evening': ('17:00:00', '21:00:00'),
        'night': ('21:00:00', '23:59:59'),
        'custom': (request.form.get('custom_start') + ':00', request.form.get('custom_end') + ':00')
    }
    
    start_time, end_time = time_slots.get(slot_type, time_slots['morning'])
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO auction_sessions (auction_id, session_name, start_time, end_time, status, created_by)
            VALUES (%s, %s, %s, %s, 'active', %s)
        """, (auction_id, session_name, start_time, end_time, session['user_id']))
        
        session_id = cursor.lastrowid
        
        for team_id in team_ids:
            cursor.execute("""
                INSERT INTO session_teams (session_id, team_id) VALUES (%s, %s)
            """, (session_id, team_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    return redirect(f'/admin/sessions?auction={auction_id}')


@bp.route('/sessions/<int:session_id>/close', methods=['POST'])
def close_session(session_id):
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT auction_id FROM auction_sessions WHERE id = %s", (session_id,))
        sess = cursor.fetchone()
        
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        cursor.execute("UPDATE auction_sessions SET status = 'completed' WHERE id = %s", (session_id,))
        db.commit()
        
        auction_id = sess['auction_id']
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True})


@bp.route('/sessions/<int:session_id>/continue', methods=['POST'])
def continue_session(session_id):
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get original session
        cursor.execute("""
            SELECT s.*, 
                   (SELECT GROUP_CONCAT(team_id) FROM session_teams WHERE session_id = s.id) as team_ids
            FROM auction_sessions s 
            WHERE s.id = %s
        """, (session_id,))
        orig = cursor.fetchone()
        
        if not orig:
            return jsonify({'error': 'Session not found'}), 404
        
        # Get all teams in auction
        cursor.execute("SELECT id FROM teams WHERE auction_id = %s", (orig['auction_id'],))
        all_team_ids = [r['id'] for r in cursor.fetchall()]
        
        # Get teams already in any session
        cursor.execute("""
            SELECT DISTINCT team_id FROM session_teams st
            JOIN auction_sessions s ON st.session_id = s.id
            WHERE s.auction_id = %s AND s.status IN ('active', 'live', 'paused')
        """, (orig['auction_id'],))
        busy_ids = [r['team_id'] for r in cursor.fetchall()]
        
        # Remaining teams
        remaining = [tid for tid in all_team_ids if tid not in busy_ids]
        
        if not remaining:
            return jsonify({'error': 'No remaining teams'}), 400
        
        # Create continuation session
        cursor.execute("""
            INSERT INTO auction_sessions (auction_id, session_name, start_time, end_time, status, created_by, parent_session_id)
            VALUES (%s, %s, %s, %s, 'active', %s, %s)
        """, (orig['auction_id'], orig['session_name'] + ' (Continued)', orig['start_time'], orig['end_time'], session['user_id'], session_id))
        
        new_session_id = cursor.lastrowid
        
        for team_id in remaining:
            cursor.execute("INSERT INTO session_teams (session_id, team_id) VALUES (%s, %s)", (new_session_id, team_id))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return redirect(f'/admin/sessions?auction={orig["auction_id"]}')


@bp.route('/sessions/<int:session_id>/remove-team/<int:team_id>', methods=['POST'])
def remove_team_from_session(session_id, team_id):
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("DELETE FROM session_teams WHERE session_id = %s AND team_id = %s", (session_id, team_id))
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True})