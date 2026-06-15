from flask import Blueprint, render_template, session, flash, redirect, jsonify, request, abort
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
        cursor.execute("""
            SELECT t.*, a.league_name, a.status as auction_status, a.id as auction_id
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            WHERE t.owner_id = %s
            ORDER BY a.created_at DESC
        """, (session['user_id'],))
        teams = cursor.fetchall()
        
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
            team['wicket_keepers'] = categories.get('wicket_keepers', 0)
            
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
    """Main auction page - validates session has active auction and loads sessions"""
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
            SELECT t.*, a.league_name, a.status as auction_status
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
        
        # ============================================
        # FETCH SESSIONS - MINIMAL SAFE QUERY
        # ============================================
        cursor.execute("""
            SELECT s.* 
            FROM auction_sessions s
            WHERE s.auction_id = %s
        """, (active_auction_id,))
        auction_sessions = cursor.fetchall()
        
        # Get currently selected session if any
        active_session_id = session.get('active_session_id')
        current_session = None
        
        if active_session_id:
            cursor.execute("""
                SELECT * FROM auction_sessions 
                WHERE id = %s AND auction_id = %s
            """, (active_session_id, active_auction_id))
            current_session = cursor.fetchone()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/auction.html', 
                         team=team, 
                         auction_id=active_auction_id,
                         auction_sessions=auction_sessions,
                         current_session=current_session)


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