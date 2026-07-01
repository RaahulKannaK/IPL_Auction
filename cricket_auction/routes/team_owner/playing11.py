from flask import Blueprint, render_template, request, session, flash, redirect, jsonify
from database.db import get_db
import json

bp = Blueprint('team_owner_playing11', __name__, url_prefix='/team-owner/playing11')


def get_user_team_by_ids(cursor, user_id, auction_id=None):
    """Get team where user is owner (supports owner_ids JSON)"""
    if auction_id:
        cursor.execute("SELECT * FROM teams WHERE auction_id = %s", (auction_id,))
    else:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    
    all_teams = cursor.fetchall()
    for team in all_teams:
        if team.get('owner_id') == user_id:
            return team
        if team.get('owner_ids'):
            try:
                owner_ids = json.loads(team['owner_ids']) if isinstance(team['owner_ids'], str) else team['owner_ids']
                if user_id in owner_ids:
                    return team
            except:
                pass
    return None


@bp.route('/')
def playing11_builder():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        auction_id = session.get('active_auction_id')
        
        # Get user's team (with auction awareness like squad)
        if auction_id:
            user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        else:
            user_team = get_user_team_by_ids(cursor, session['user_id'])
        
        if not user_team:
            flash('No team assigned')
            return redirect('/team-owner/dashboard')
        
        team_id = user_team['id']
        auction_id = user_team['auction_id']
        
        # ===== GET SQUAD: Same query as your squad page =====
        cursor.execute("""
            SELECT 
                p.id as player_id,
                p.player_name,
                p.category,
                p.overseas,
                sp.id as session_player_id,
                stp.purchase_price as sold_price
            FROM session_team_players stp
            JOIN session_players sp ON stp.session_player_id = sp.id
            JOIN players p ON sp.player_id = p.id
            WHERE stp.team_id = %s
            ORDER BY stp.purchase_price DESC
        """, (team_id,))
        squad = cursor.fetchall()
        
        # ===== GET EXISTING PLAYING 11 =====
        cursor.execute("""
            SELECT 
                p.id as player_id,
                p.player_name,
                p.category,
                p.overseas,
                pp.position,
                pp.is_captain,
                pp.is_vice_captain,
                stp.purchase_price as sold_price
            FROM playing11 pp
            JOIN session_team_players stp ON stp.team_id = pp.team_id AND stp.session_player_id = (
                SELECT sp.id FROM session_players sp WHERE sp.player_id = pp.player_id LIMIT 1
            )
            JOIN players p ON pp.player_id = p.id
            WHERE pp.team_id = %s
            ORDER BY 
                CASE pp.position
                    WHEN 'wk' THEN 1 WHEN 'b1' THEN 2 WHEN 'b2' THEN 3 WHEN 'b3' THEN 4
                    WHEN 'b4' THEN 5 WHEN 'ar1' THEN 6 WHEN 'ar2' THEN 7
                    WHEN 'bw1' THEN 8 WHEN 'bw2' THEN 9 WHEN 'bw3' THEN 10
                    ELSE 11
                END
        """, (team_id,))
        playing11 = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/playing11.html', 
        squad=squad, 
        playing11=playing11, 
        team=user_team
    )


@bp.route('/save', methods=['POST'])
def save_playing11():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    team_id = int(data.get('team_id'))
    players = data.get('players', [])
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # Verify team belongs to user (supports owner_ids JSON)
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        is_owner = team.get('owner_id') == session['user_id']
        if not is_owner and team.get('owner_ids'):
            try:
                owner_ids = json.loads(team['owner_ids']) if isinstance(team['owner_ids'], str) else team['owner_ids']
                is_owner = session['user_id'] in owner_ids
            except:
                pass
        
        if not is_owner:
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
        
        # Validate all positions are unique
        positions = [p['position'] for p in players]
        if len(set(positions)) != 11:
            return jsonify({'error': 'All positions must be unique'}), 400
        
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
                1 if p.get('is_captain') else 0, 
                1 if p.get('is_vice_captain') else 0
            ))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'message': 'Playing 11 saved!'})


@bp.route('/view/<int:team_id>')
def view_playing11(team_id):
    """View playing11 for any team (public/viewer)"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer', 'viewer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # Get team
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        # If team_owner, verify they own this team
        if session.get('role') == 'team_owner':
            is_owner = team.get('owner_id') == session['user_id']
            if not is_owner and team.get('owner_ids'):
                try:
                    owner_ids = json.loads(team['owner_ids']) if isinstance(team['owner_ids'], str) else team['owner_ids']
                    is_owner = session['user_id'] in owner_ids
                except:
                    pass
            if not is_owner:
                return jsonify({'error': 'Unauthorized'}), 403
        
        # Get playing11
        cursor.execute("""
            SELECT 
                p.id as player_id,
                p.player_name,
                p.category,
                p.overseas,
                pp.position,
                pp.is_captain,
                pp.is_vice_captain,
                stp.purchase_price as sold_price
            FROM playing11 pp
            JOIN players p ON pp.player_id = p.id
            LEFT JOIN session_team_players stp ON stp.team_id = pp.team_id AND stp.session_player_id = (
                SELECT sp.id FROM session_players sp WHERE sp.player_id = pp.player_id LIMIT 1
            )
            WHERE pp.team_id = %s
            ORDER BY pp.position
        """, (team_id,))
        playing11 = cursor.fetchall()
        
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
        'total_players': len(playing11)
    })