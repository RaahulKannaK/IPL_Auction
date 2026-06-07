from flask import Blueprint, render_template, session, flash, redirect, jsonify
from database.db import get_db

bp = Blueprint('viewer_teams', __name__, url_prefix='/viewer/teams')

@bp.route('/')
def view_teams():
    """View all teams and their squads for viewers"""
    if session.get('role') != 'viewer':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get all teams with owner details
        cursor.execute("""
            SELECT t.*, u1.username as owner_1_name
            FROM teams t
            LEFT JOIN users u1 ON t.owner_id = u1.id
            ORDER BY t.created_at DESC
        """)
        teams = cursor.fetchall()
        
        # Get squad for each team
        for team in teams:
            cursor.execute("""
                SELECT p.*, tp.purchase_price as sold_price, ap.status
                FROM team_players tp
                JOIN auction_players ap ON tp.auction_player_id = ap.id
                JOIN players p ON ap.player_id = p.id
                WHERE tp.team_id = %s
                ORDER BY tp.purchase_price DESC
            """, (team['id'],))
            team['squad'] = cursor.fetchall()
            team['spent'] = float(team['spent'] or 0)
            team['available'] = float(team['purse_limit'] or 100) - team['spent'] - float(team['reserved'] or 0)
            
            # Category breakdown
            team['squad_batsmen'] = [p for p in team['squad'] if p['category'] == 'batsman']
            team['squad_bowlers'] = [p for p in team['squad'] if p['category'] == 'bowler']
            team['squad_all_rounders'] = [p for p in team['squad'] if p['category'] == 'all_rounder']
            team['squad_keepers'] = [p for p in team['squad'] if p['category'] == 'wicket_keeper']
            team['squad_overseas'] = [p for p in team['squad'] if p['overseas']]
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('viewer/teams.html', teams=teams)

@bp.route('/<int:team_id>')
def view_team_detail(team_id):
    """View specific team details for viewers"""
    if session.get('role') != 'viewer':
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get team details
        cursor.execute("""
            SELECT t.*, u1.username as owner_1_name
            FROM teams t
            LEFT JOIN users u1 ON t.owner_id = u1.id
            WHERE t.id = %s
        """, (team_id,))
        team = cursor.fetchone()
        
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        # Get squad
        cursor.execute("""
            SELECT p.*, tp.purchase_price as sold_price
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE tp.team_id = %s
            ORDER BY tp.purchase_price DESC
        """, (team_id,))
        squad = cursor.fetchall()
        
        # Category breakdown
        categories = {
            'batsmen': [p for p in squad if p['category'] == 'batsman'],
            'bowlers': [p for p in squad if p['category'] == 'bowler'],
            'all_rounders': [p for p in squad if p['category'] == 'all_rounder'],
            'wicket_keepers': [p for p in squad if p['category'] == 'wicket_keeper'],
            'overseas': [p for p in squad if p['overseas']]
        }
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'team': {
            'id': team['id'],
            'name': team['team_name'],
            'owner': team['owner_1_name'],
            'purse_limit': float(team['purse_limit'] or 100),
            'spent': float(team['spent'] or 0),
            'reserved': float(team['reserved'] or 0),
            'available': float(team['purse_limit'] or 100) - float(team['spent'] or 0) - float(team['reserved'] or 0),
            'squad_size': len(squad),
            'overseas_count': len(categories['overseas'])
        },
        'squad': squad,
        'categories': categories
    })