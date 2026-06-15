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
            
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/dashboard.html', teams=teams)


@bp.route('/auction-room/<int:auction_id>')
def auction_room_entry(auction_id):
    """Entry point - sets auction context and redirects to auction page with session selector"""
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
    
    # Set auction context in session
    session['active_auction_id'] = auction_id
    session['active_team_id'] = team['id']
    session['active_league_name'] = team['league_name']
    # Clear any previous session - user must select again
    session.pop('active_session_id', None)
    
    return redirect('/team-owner/auction')

@bp.route('/verify-passcode', methods=['POST'])
def verify_passcode():
    """Verify auction passcode before allowing entry"""
    if session.get('role') != 'team_owner':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    entered_passcode = data.get('passcode', '').strip()
    
    if not auction_id:
        return jsonify({'success': False, 'message': 'Auction ID required'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get the passcode for this auction and verify user owns a team here
        cursor.execute("""
            SELECT a.passcode, t.id as team_id
            FROM auctions a
            JOIN teams t ON t.auction_id = a.id
            WHERE a.id = %s AND t.owner_id = %s
        """, (auction_id, session['user_id']))
        result = cursor.fetchone()
        
        if not result:
            return jsonify({'success': False, 'message': 'Auction not found'}), 404
        
        stored_passcode = result.get('passcode', '')
        
        # If no passcode is set, allow entry (optional - remove this if you require passcodes)
        if not stored_passcode:
            return jsonify({'success': True})
        
        # Check if entered passcode matches
        if entered_passcode == stored_passcode:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'Invalid passcode'})
            
    finally:
        cursor.close()
        db.close()
        
@bp.route('/exit-auction')
def exit_auction():
    """Clear all auction/session context and return to dashboard"""
    session.pop('active_auction_id', None)
    session.pop('active_team_id', None)
    session.pop('active_league_name', None)
    session.pop('active_session_id', None)
    return redirect('/team-owner/dashboard')

