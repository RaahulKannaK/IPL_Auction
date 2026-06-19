from flask import Blueprint, render_template, request, redirect, session, flash, jsonify,Response
from database.db import get_db, get_cached, clear_cache
import json
import csv
from io import StringIO

bp = Blueprint('admin_sessions', __name__, url_prefix='/admin/sessions')

SESSION_SLOTS = {
    'morning': {'start': '06:00', 'end': '12:00', 'label': 'Morning'},
    'afternoon': {'start': '12:00', 'end': '17:00', 'label': 'Afternoon'},
    'evening': {'start': '17:00', 'end': '21:00', 'label': 'Evening'},
    'night': {'start': '21:00', 'end': '23:59', 'label': 'Night'},
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
        
        # All sessions for this auction with player counts
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
            sess['remaining_teams'] = total_teams - participating
            sess['percentage'] = (participating / total_teams * 100) if total_teams > 0 else 0
            sess['has_players'] = (sess.get('player_count') or 0) > 0
            sess['is_full'] = participating == total_teams  # 10/10 teams
        
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
        new_session_id = cursor.lastrowid
        
    finally:
        cursor.close()
        db.close()
    
    # Redirect to player assignment page
    return redirect(f'/admin/sessions/{new_session_id}/assign-players')


@bp.route('/<int:session_id>/assign-players')
def assign_players_page(session_id):
    """Page to assign players to a session - smart logic based on team coverage"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
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
        
        # Get total teams in auction
        cursor.execute("SELECT COUNT(*) as cnt FROM teams WHERE auction_id = %s", (auction_id,))
        total_teams = cursor.fetchone()['cnt']
        
        # Parse team_ids for current session
        current_team_ids = []
        if current_sess.get('team_ids'):
            try:
                current_team_ids = json.loads(current_sess['team_ids']) if isinstance(current_sess['team_ids'], str) else current_sess['team_ids']
            except:
                current_team_ids = []
        
        current_sess['team_count'] = len(current_team_ids)
        current_sess['is_full'] = len(current_team_ids) == total_teams
        
        # Get all previous COMPLETED sessions in this auction
        cursor.execute("""
            SELECT s.*, 
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id) as total_count,
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id AND sp.status = 'sold') as sold_count,
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id AND sp.status = 'available') as available_count
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
        
        # === SMART LOGIC ===
        # If this session has ALL teams (10/10) and there's a previous session with same set
        # Auto-suggest continuing with same player set
        auto_continue = False
        if current_sess['is_full'] and previous_sessions:
            # Check if previous session also had full teams
            prev = previous_sessions[0]  # Most recent
            prev_team_ids = []
            if prev.get('team_ids'):
                try:
                    prev_team_ids = json.loads(prev['team_ids']) if isinstance(prev['team_ids'], str) else prev['team_ids']
                except:
                    prev_team_ids = []
            
            # If previous was also full (10/10), auto-continue with same set
            if len(prev_team_ids) == total_teams:
                auto_continue = True
                current_sess['auto_previous_id'] = prev['id']
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/assign_players.html',
        session=current_sess,
        previous_sessions=previous_sessions,
        all_players=all_players,
        has_players=has_players,
        session_players=session_players,
        total_teams=total_teams,
        auto_continue=auto_continue
    )


@bp.route('/<int:session_id>/assign-players', methods=['POST'])
def assign_players(session_id):
    """Assign players to session - from previous session, CSV upload, or fresh selection"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Check if this is a CSV upload
    if 'file' in request.files:
        return import_players_to_session(session_id)
    
    data = request.get_json() or request.form
    source_type = data.get('source_type')  # 'previous', 'fresh', 'same_set', 'csv'
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
        
        if source_type == 'same_set' and previous_session_id:
            # Copy ALL players from previous session, ALL reset to available
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
            message = f'Using same set of {count} players (all reset to available for fresh bidding)'
            
        elif source_type == 'previous' and previous_session_id:
            # Copy ONLY UNSOLD players from previous session
            cursor.execute("""
                SELECT sp.* FROM session_players sp
                WHERE sp.session_id = %s AND sp.status != 'sold'
            """, (previous_session_id,))
            unsold_players = cursor.fetchall()
            
            for p in unsold_players:
                cursor.execute("""
                    INSERT INTO session_players (session_id, player_id, base_price, status)
                    VALUES (%s, %s, %s, 'available')
                """, (session_id, p['player_id'], p['base_price']))
            
            count = len(unsold_players)
            message = f'Carried forward {count} unsold players from previous session'
            
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


def import_players_to_session(session_id):
    """Bulk import players from CSV into a session"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    auction_id = session.get('active_auction_id')
    db = get_db()
    cursor = db.cursor()
    
    try:
        # Clear existing session players
        cursor.execute("DELETE FROM session_players WHERE session_id = %s", (session_id,))
        
        stream = StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.reader(stream)
        next(csv_input)  # Skip header
        
        count = 0
        for row in csv_input:
            if len(row) >= 3:
                player_name = row[0].strip()
                category = row[1]
                overseas = row[2].lower() == 'true'
                base_price = float(row[3]) if len(row) > 3 else 0.5
                
                # Check if player exists in master pool
                cursor.execute("SELECT id FROM players WHERE player_name = %s", (player_name,))
                existing = cursor.fetchone()
                
                if existing:
                    player_id = existing[0]
                else:
                    # Create new player in master pool
                    cursor.execute(
                        "INSERT INTO players (player_name, category, overseas) VALUES (%s, %s, %s)",
                        (player_name, category, overseas)
                    )
                    player_id = cursor.lastrowid
                    
                    # Add to auction_players
                    cursor.execute(
                        "INSERT INTO auction_players (auction_id, player_id, base_price, status) VALUES (%s, %s, %s, 'available')",
                        (auction_id, player_id, base_price)
                    )
                
                # Add to session_players
                cursor.execute("""
                    INSERT INTO session_players (session_id, player_id, base_price, status)
                    VALUES (%s, %s, %s, 'available')
                """, (session_id, player_id, base_price))
                count += 1
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'message': f'{count} players imported to session', 'count': count})


@bp.route('/<int:session_id>/players')
def get_session_players(session_id):
    """Get players in a session with their session-local status"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
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
    
    return render_template('admin/session_room.html',
        session=sess,
        teams=teams,
        players=players,
        session_bids=session_bids,
        auction={'id': sess['auction_id']}
    )


@bp.route('/<int:session_id>/close', methods=['POST'])
def close_session(session_id):
    """Close session and summarize"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
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
    
    flash(f'Session closed! {sold} sold, {unsold} unsold.')
    return redirect('/admin/sessions')


@bp.route('/<int:session_id>/remove-team/<int:team_id>', methods=['POST'])
def remove_team_from_session(session_id, team_id):
    """Remove a team from session"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
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

@bp.route('/download-template')
def download_template():
    csv_content = """player_name,category,overseas,base_price
Andre Russell,all_rounder,true,2.0
Virat Kohli,batsman,false,2.0
Jasprit Bumrah,bowler,false,1.5
MS Dhoni,wicket_keeper,false,1.5
Ben Stokes,all_rounder,true,2.0
Rohit Sharma,batsman,false,2.0
Rashid Khan,bowler,true,1.5
Jos Buttler,wicket_keeper,true,1.5
Hardik Pandya,all_rounder,false,2.0
Trent Boult,bowler,true,1.5
"""
    return Response(
        csv_content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename="player_template.csv"',
            'Content-Type': 'text/csv; charset=utf-8'
        }
    )