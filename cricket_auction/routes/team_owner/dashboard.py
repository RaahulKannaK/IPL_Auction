from flask import Blueprint, render_template, session, flash, redirect, jsonify, request
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('team_owner_dashboard', __name__, url_prefix='/team-owner')


@bp.route('/dashboard')
def dashboard():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get ALL teams this user owns, with auction info
        cursor.execute("""
            SELECT t.*, a.league_name, a.status as auction_status, a.id as auction_id
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            WHERE t.owner_id = %s
            ORDER BY a.created_at DESC
        """, (session['user_id'],))
        teams = cursor.fetchall()
        
        # For each team, get quick stats and available sessions
        for team in teams:
            # Squad count
            cursor.execute("""
                SELECT COUNT(*) as cnt 
                FROM team_players tp
                JOIN auction_players ap ON tp.auction_player_id = ap.id
                WHERE tp.team_id = %s AND ap.auction_id = %s
            """, (team['id'], team['auction_id']))
            result = cursor.fetchone()
            team['squad_count'] = result['cnt'] if result else 0
            
            # Available purse
            spent = float(team['spent'] or 0)
            reserved = float(team['reserved'] or 0)
            team['available'] = float(team['purse_limit']) - spent - reserved
            
            # Category breakdown
            cursor.execute("""
                SELECT p.category, COUNT(*) as cnt
                FROM team_players tp
                JOIN auction_players ap ON tp.auction_player_id = ap.id
                JOIN players p ON ap.player_id = p.id
                WHERE tp.team_id = %s AND ap.auction_id = %s
                GROUP BY p.category
            """, (team['id'], team['auction_id']))
            categories = {row['category']: row['cnt'] for row in cursor.fetchall()}
            team['batsmen'] = categories.get('batsman', 0)
            team['bowlers'] = categories.get('bowler', 0)
            team['all_rounders'] = categories.get('all_rounder', 0)
            team['wicket_keepers'] = categories.get('wicket_keeper', 0)
            
            # Overseas count
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM team_players tp
                JOIN auction_players ap ON tp.auction_player_id = ap.id
                JOIN players p ON ap.player_id = p.id
                WHERE tp.team_id = %s AND ap.auction_id = %s AND p.overseas = TRUE
            """, (team['id'], team['auction_id']))
            result = cursor.fetchone()
            team['overseas'] = result['cnt'] if result else 0
            
            # Get active sessions for this auction where this team is included
            cursor.execute("""
                SELECT s.*, 
                       (SELECT COUNT(*) FROM teams WHERE JSON_CONTAINS(s.team_ids, CAST(id AS JSON))) as team_count
                FROM auction_sessions s
                WHERE s.auction_id = %s AND s.status IN ('active', 'paused')
                ORDER BY s.created_at DESC
            """, (team['auction_id'],))
            sessions = cursor.fetchall()
            
            # Parse team_ids and check if this team is in session
            team['sessions'] = []
            for sess in sessions:
                if sess['team_ids']:
                    try:
                        team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                        if team['id'] in team_ids:
                            sess['team_ids_list'] = team_ids
                            sess['participating'] = len(team_ids)
                            team['sessions'].append(sess)
                    except:
                        pass
            
            # Check if team is in any active session
            team['has_active_session'] = len(team['sessions']) > 0
            
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/dashboard.html', teams=teams)


@bp.route('/enter-auction/<int:auction_id>')
def enter_auction(auction_id):
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify this user owns a team in this auction
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
    
    # If auction is live/paused, check for sessions first
    if team['auction_status'] in ['live', 'paused']:
        # Redirect to session selection page
        return redirect(f'/team-owner/sessions/{auction_id}')
    
    # Set auction context in session
    session['active_auction_id'] = auction_id
    session['active_team_id'] = team['id']
    session['active_league_name'] = team['league_name']
    
    return redirect('/team-owner/auction')


@bp.route('/sessions/<int:auction_id>')
def list_sessions(auction_id):
    """Show available sessions for this auction that include user's team"""
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team ownership
        cursor.execute("""
            SELECT t.*, a.league_name, a.status as auction_status
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            WHERE t.auction_id = %s AND t.owner_id = %s
        """, (auction_id, session['user_id']))
        team = cursor.fetchone()
        
        if not team:
            flash('You do not own a team in this auction')
            return redirect('/team-owner/dashboard')
        
        # Get all sessions for this auction
        cursor.execute("""
            SELECT s.* 
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY s.created_at DESC
        """, (auction_id,))
        all_sessions = cursor.fetchall()
        
        # Categorize sessions
        available_sessions = []
        my_sessions = []
        completed_sessions = []
        
        for sess in all_sessions:
            team_ids = []
            if sess['team_ids']:
                try:
                    team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                except:
                    team_ids = []
            
            sess['team_ids_list'] = team_ids
            sess['team_count'] = len(team_ids)
            
            if team['id'] in team_ids:
                if sess['status'] in ['active', 'paused']:
                    my_sessions.append(sess)
                else:
                    completed_sessions.append(sess)
            elif sess['status'] in ['active', 'paused']:
                available_sessions.append(sess)
        
        # Get all teams for display names
        cursor.execute("SELECT id, team_name FROM teams WHERE auction_id = %s", (auction_id,))
        all_teams = {row['id']: row['team_name'] for row in cursor.fetchall()}
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/sessions.html',
        team=team,
        auction_id=auction_id,
        league_name=team['league_name'],
        available_sessions=available_sessions,
        my_sessions=my_sessions,
        completed_sessions=completed_sessions,
        all_teams=all_teams,
        total_teams=len(all_teams)
    )


@bp.route('/join-session/<int:session_id>', methods=['POST'])
def join_session(session_id):
    """Join a specific session and enter auction room"""
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get session details
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        sess = cursor.fetchone()
        
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        # Verify team ownership
        cursor.execute("""
            SELECT t.*, a.league_name
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            WHERE t.auction_id = %s AND t.owner_id = %s
        """, (sess['auction_id'], session['user_id']))
        team = cursor.fetchone()
        
        if not team:
            return jsonify({'error': 'Not your auction'}), 403
        
        # Check if team is already in session
        team_ids = []
        if sess['team_ids']:
            try:
                team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                team_ids = []
        
        # Add team to session if not already there
        if team['id'] not in team_ids:
            team_ids.append(team['id'])
            cursor.execute("""
                UPDATE auction_sessions SET team_ids = %s WHERE id = %s
            """, (json.dumps(team_ids), session_id))
            db.commit()
        
        # Set session context
        session['active_session_id'] = session_id
        session['active_auction_id'] = sess['auction_id']
        session['active_team_id'] = team['id']
        session['active_league_name'] = team['league_name']
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'redirect': '/team-owner/auction'})


@bp.route('/exit-auction')
def exit_auction():
    session.pop('active_auction_id', None)
    session.pop('active_team_id', None)
    session.pop('active_league_name', None)
    session.pop('active_session_id', None)
    return redirect('/team-owner/dashboard')