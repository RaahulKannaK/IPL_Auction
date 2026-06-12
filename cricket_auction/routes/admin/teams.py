from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db

bp = Blueprint('admin_teams', __name__, url_prefix='/admin/teams')

@bp.route('/')
def list_teams():
    """List all teams for current auction with owner details"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    auction_id = request.args.get('auction') or session.get('active_auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        if auction_id:
            cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
            auction = cursor.fetchone()
        else:
            cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
            auction = cursor.fetchone()
        
        if auction:
            session['active_auction_id'] = auction['id']
            session['active_league_name'] = auction['league_name']
        
        # Multiple owners support
        cursor.execute("""
            SELECT t.*, 
                   u1.username as owner_1_name,
                   u2.username as owner_2_name,
                   u3.username as owner_3_name,
                   a.league_name
            FROM teams t 
            LEFT JOIN users u1 ON t.owner_id = u1.id
            LEFT JOIN users u2 ON t.owner_id_2 = u2.id
            LEFT JOIN users u3 ON t.owner_id_3 = u3.id
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
    """Create new team with up to 3 owners"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/admin/teams')
    
    auction_id_raw = request.form.get('auction_id') or session.get('active_auction_id') or '1'
    if str(auction_id_raw).strip() == '':
        auction_id_raw = '1'
    
    try:
        auction_id = int(auction_id_raw)
    except (ValueError, TypeError):
        flash('Invalid auction ID.')
        return redirect('/admin/teams')
    
    session['active_auction_id'] = auction_id
    
    team_name = request.form['team_name'].strip()
    purse_limit = float(request.form.get('purse_limit', 100))
    
    # Get up to 3 owner IDs from form
    owner_ids = []
    for i in range(1, 4):
        owner_id = request.form.get(f'owner_id_{i}')
        if owner_id and owner_id.strip():
            owner_ids.append(int(owner_id))
    
    # Remove duplicates while preserving order
    owner_ids = list(dict.fromkeys(owner_ids))
    
    owner_id = owner_ids[0] if len(owner_ids) > 0 else None
    owner_id_2 = owner_ids[1] if len(owner_ids) > 1 else None
    owner_id_3 = owner_ids[2] if len(owner_ids) > 2 else None
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO teams (auction_id, team_name, owner_id, owner_id_2, owner_id_3, purse_limit)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (auction_id, team_name, owner_id, owner_id_2, owner_id_3, purse_limit))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash(f'Team "{team_name}" created successfully!')
    return redirect('/admin/teams')


@bp.route('/edit/<int:id>', methods=['POST'])
def edit_team(id):
    """Edit team details and up to 3 owners"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    team_name = request.form['team_name'].strip()
    purse_limit = float(request.form.get('purse_limit', 100))
    
    # Get up to 3 owner IDs from form
    owner_ids = []
    for i in range(1, 4):
        owner_id = request.form.get(f'owner_id_{i}')
        if owner_id and owner_id.strip():
            owner_ids.append(int(owner_id))
    
    owner_ids = list(dict.fromkeys(owner_ids))
    
    owner_id = owner_ids[0] if len(owner_ids) > 0 else None
    owner_id_2 = owner_ids[1] if len(owner_ids) > 1 else None
    owner_id_3 = owner_ids[2] if len(owner_ids) > 2 else None
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            UPDATE teams 
            SET team_name = %s, 
                owner_id = %s,
                owner_id_2 = %s,
                owner_id_3 = %s,
                purse_limit = %s
            WHERE id = %s
        """, (team_name, owner_id, owner_id_2, owner_id_3, purse_limit, id))
        
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


@bp.route('/remove_owner/<int:team_id>/<int:owner_num>', methods=['POST'])
def remove_owner(team_id, owner_num):
    """Remove specific owner from team (1, 2, or 3)"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        if owner_num == 1:
            # Shift owner 2 to 1, owner 3 to 2
            cursor.execute("""
                UPDATE teams 
                SET owner_id = owner_id_2,
                    owner_id_2 = owner_id_3,
                    owner_id_3 = NULL
                WHERE id = %s
            """, (team_id,))
        elif owner_num == 2:
            cursor.execute("UPDATE teams SET owner_id_2 = owner_id_3, owner_id_3 = NULL WHERE id = %s", (team_id,))
        elif owner_num == 3:
            cursor.execute("UPDATE teams SET owner_id_3 = NULL WHERE id = %s", (team_id,))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash('Owner removed!')
    return redirect('/admin/teams')


@bp.route('/purse/<int:team_id>')
def view_purse(team_id):
    """Get team purse details"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
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
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
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
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
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