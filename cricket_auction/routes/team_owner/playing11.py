from flask import Blueprint, render_template, request, session, flash, redirect, jsonify
from database.db import get_db

bp = Blueprint('team_owner_playing11', __name__, url_prefix='/team-owner/playing11')

def get_user_team(cursor, user_id):
    """Get team owned by user — uses passed cursor, no new connection"""
    cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    return cursor.fetchone()

@bp.route('/')
def playing11_builder():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team:
            flash('No team assigned')
            return redirect('/dashboard')
        
        team_id = user_team['id']
        
        # Get squad from team_players joined with auction_players and players
        cursor.execute("""
            SELECT p.*, tp.purchase_price as sold_price, tp.purchased_at
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE tp.team_id = %s
            ORDER BY tp.purchase_price DESC
        """, (team_id,))
        squad = cursor.fetchall()
        
        # Get existing playing11
        cursor.execute("""
            SELECT p.*, pp.position, pp.is_captain, pp.is_vice_captain
            FROM playing11 pp
            JOIN players p ON pp.player_id = p.id
            WHERE pp.team_id = %s
            ORDER BY pp.position
        """, (team_id,))
        playing11 = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/playing11.html', squad=squad, playing11=playing11, team_id=team_id)

@bp.route('/save', methods=['POST'])
def save_playing11():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    team_id = data.get('team_id')
    players = data.get('players', [])
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team belongs to user
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Validate exactly 11 players
        if len(players) != 11:
            return jsonify({'error': 'Playing 11 must have exactly 11 players'}), 400
        
        # Validate one captain and one vice-captain
        captains = [p for p in players if p.get('is_captain')]
        vice_captains = [p for p in players if p.get('is_vice_captain')]
        
        if len(captains) != 1:
            return jsonify({'error': 'Must select exactly 1 captain'}), 400
        if len(vice_captains) != 1:
            return jsonify({'error': 'Must select exactly 1 vice-captain'}), 400
        if captains[0]['player_id'] == vice_captains[0]['player_id']:
            return jsonify({'error': 'Captain and vice-captain must be different players'}), 400
        
        # Clear existing playing11
        cursor.execute("DELETE FROM playing11 WHERE team_id = %s", (team_id,))
        
        # Insert new playing11
        for p in players:
            cursor.execute("""
                INSERT INTO playing11 (team_id, player_id, position, is_captain, is_vice_captain)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                team_id, 
                p['player_id'], 
                p['position'], 
                p.get('is_captain', False), 
                p.get('is_vice_captain', False)
            ))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'message': 'Playing 11 saved!'})

@bp.route('/<int:team_id>')
def view_playing11(team_id):
    """View playing11 for any team (for admin/viewer reference)"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer', 'viewer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # If team_owner, verify they own this team
        if session.get('role') == 'team_owner':
            user_team = get_user_team(cursor, session['user_id'])
            if not user_team or user_team['id'] != team_id:
                return jsonify({'error': 'Unauthorized'}), 403
        
        # Get team details
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        # Get playing11
        cursor.execute("""
            SELECT p.*, pp.position, pp.is_captain, pp.is_vice_captain
            FROM playing11 pp
            JOIN players p ON pp.player_id = p.id
            WHERE pp.team_id = %s
            ORDER BY pp.position
        """, (team_id,))
        playing11 = cursor.fetchall()
        
        # Get full squad for reference
        cursor.execute("""
            SELECT p.*, tp.purchase_price as sold_price
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE tp.team_id = %s
            ORDER BY tp.purchase_price DESC
        """, (team_id,))
        squad = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'team': {
            'id': team['id'],
            'name': team['team_name'],
            'owner_id': team['owner_id']
        },
        'playing11': playing11,
        'squad': squad,
        'total_squad': len(squad),
        'playing11_count': len(playing11)
    })