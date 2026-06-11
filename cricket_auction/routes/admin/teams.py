from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db

bp = Blueprint('admin_teams', __name__, url_prefix='/admin/teams', strict_slashes=False)

@bp.route('/')
def list_teams():
    """List all teams for current auction with owner details"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    auction_id = session.get('active_auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        if auction_id:
            cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
            auction = cursor.fetchone()
        else:
            auction = None
        
        # Single owner only - matches your database schema
        cursor.execute("""
            SELECT t.*, 
                   u.username as owner_name,
                   a.league_name
            FROM teams t 
            LEFT JOIN users u ON t.owner_id = u.id
            LEFT JOIN auctions a ON t.auction_id = a.id
            ORDER BY t.created_at DESC
        """)
        teams = cursor.fetchall()
        
        cursor.execute("""
            SELECT id, username, role 
            FROM users 
            WHERE role IN ('team_owner', 'admin', 'auctioneer', 'owner')
            ORDER BY username
        """)
        available_owners = cursor.fetchall()
        
        total_teams = len(teams)
        assigned_teams = sum(1 for t in teams if t['owner_id'])
        unassigned_teams = total_teams - assigned_teams
        
        stats = {
            'total': total_teams,
            'assigned': assigned_teams,
            'unassigned': unassigned_teams
        }
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/teams.html', 
        teams=teams, 
        auction=auction,
        available_owners=available_owners,
        stats=stats
    )


@bp.route('/create', methods=['POST'])
def create_team():
    """Create new team with single owner"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/admin/teams')
    
    auction_id = session.get('active_auction_id') or request.form.get('auction_id', 1)
    team_name = request.form['team_name'].strip()
    purse_limit = float(request.form.get('purse_limit', 100))
    squad_size = int(request.form.get('squad_size', 18))
    overseas_limit = int(request.form.get('overseas_limit', 8))
    owner_id = request.form.get('owner_id') or None
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO teams (auction_id, team_name, owner_id, purse_limit, squad_size, overseas_limit)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (auction_id, team_name, owner_id, purse_limit, squad_size, overseas_limit))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash(f'Team "{team_name}" created successfully!')
    return redirect('/admin/teams')


@bp.route('/edit/<int:id>', methods=['POST'])
def edit_team(id):
    """Edit team details and owner"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    team_name = request.form['team_name'].strip()
    purse_limit = float(request.form.get('purse_limit', 100))
    squad_size = int(request.form.get('squad_size', 18))
    overseas_limit = int(request.form.get('overseas_limit', 8))
    owner_id = request.form.get('owner_id') or None
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            UPDATE teams 
            SET team_name = %s, 
                owner_id = %s,
                purse_limit = %s,
                squad_size = %s,
                overseas_limit = %s
            WHERE id = %s
        """, (team_name, owner_id, purse_limit, squad_size, overseas_limit, id))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash('Team updated successfully!')
    return redirect('/admin/teams')


@bp.route('/delete/<int:id>', methods=['POST'])
def delete_team(id):
    """Delete team (only if no players assigned)"""
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT COUNT(*) as cnt FROM team_players WHERE team_id = %s", (id,))
        result = cursor.fetchone()
        
        if result['cnt'] > 0:
            flash('Cannot delete team with assigned players!')
            return redirect('/admin/teams')
        
        cursor.execute("DELETE FROM teams WHERE id = %s", (id,))
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash('Team deleted!')
    return redirect('/admin/teams')


@bp.route('/remove_owner/<int:team_id>', methods=['POST'])
def remove_owner(team_id):
    """Remove owner from team"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("UPDATE teams SET owner_id = NULL WHERE id = %s", (team_id,))
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash('Owner removed!')
    return redirect('/admin/teams')


@bp.route('/purse/<int:team_id>')
def view_purse(team_id):
    """Get team purse details"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        
    finally:
        cursor.close()
        db.close()
    
    if not team:
        return jsonify({'error': 'Team not found'}), 404
    
    return jsonify({
        'team_name': team['team_name'],
        'purse_limit': float(team['purse_limit']),
        'spent': float(team['spent'] or 0),
        'reserved': float(team['reserved'] or 0),
        'available': float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
    })


@bp.route('/squad/<int:team_id>')
def view_squad(team_id):
    """Get team squad details"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT p.*, tp.purchase_price as sold_price, tp.purchased_at
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE tp.team_id = %s
            ORDER BY tp.purchase_price DESC
        """, (team_id,))
        players = cursor.fetchall()
        
        categories = {}
        for player in players:
            cat = player['category']
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(player)
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'players': players,
        'categories': categories,
        'total_players': len(players),
        'overseas_count': sum(1 for p in players if p['overseas'])
    })


@bp.route('/available_owners')
def available_owners():
    """Get list of users available for team ownership"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT id, username, role 
            FROM users 
            WHERE role IN ('team_owner', 'admin', 'auctioneer', 'owner')
            ORDER BY username
        """)
        users = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'users': users})