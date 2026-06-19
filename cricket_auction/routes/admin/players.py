from flask import Blueprint, render_template, request, redirect, session, flash, jsonify, Response
from database.db import get_db, get_cached, clear_cache
import csv
from io import StringIO

bp = Blueprint('admin_players', __name__, url_prefix='/admin/players')

@bp.route('/')
def list_players():
    """List all players with auction status and team info"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Join with auction_players to get status and base_price, order by player_name
        cursor.execute("""
            SELECT p.*, ap.base_price, ap.status, ap.sold_price, t.team_name
            FROM players p
            LEFT JOIN auction_players ap ON p.id = ap.player_id
            LEFT JOIN teams t ON ap.sold_team_id = t.id
            ORDER BY p.player_name
        """)
        players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/players.html', players=players)

@bp.route('/create', methods=['POST'])
def create_player():
    """Add new player to master database and default auction"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/admin/players')
    
    player_name = request.form['player_name'].strip()
    category = request.form['category']
    overseas = request.form.get('overseas') == 'on'
    base_price = float(request.form.get('base_price', 0.5))
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        # Insert into players table (master player database)
        cursor.execute(
            "INSERT INTO players (player_name, category, overseas) VALUES (%s, %s, %s)",
            (player_name, category, overseas)
        )
        player_id = cursor.lastrowid
        
        # Add to auction_players for default auction (auction_id = 1)
        cursor.execute(
            "INSERT INTO auction_players (auction_id, player_id, base_price, status) VALUES (1, %s, %s, 'available')",
            (player_id, base_price)
        )
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash('Player added to master database!')
    return redirect('/admin/players')

@bp.route('/import', methods=['POST'])
def import_players():
    """Bulk import players from CSV"""
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    if 'file' not in request.files:
        flash('No file uploaded')
        return redirect('/admin/players')
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected')
        return redirect('/admin/players')
    
    db = get_db()
    cursor = db.cursor()
    
    try:
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
                
                cursor.execute(
                    "INSERT INTO players (player_name, category, overseas) VALUES (%s, %s, %s)",
                    (player_name, category, overseas)
                )
                player_id = cursor.lastrowid
                
                cursor.execute(
                    "INSERT INTO auction_players (auction_id, player_id, base_price, status) VALUES (1, %s, %s, 'available')",
                    (player_id, base_price)
                )
                count += 1
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash(f'{count} players imported!')
    return redirect('/admin/players')

@bp.route('/edit/<int:id>', methods=['POST'])
def edit_player(id):
    """Edit player details"""
    if session.get('role') not in ['owner', 'admin']:
        flash('Unauthorized')
        return redirect('/admin/players')
    
    player_name = request.form['player_name'].strip()
    category = request.form['category']
    overseas = request.form.get('overseas') == 'on'
    base_price = request.form.get('base_price')
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        # Update master player table
        cursor.execute(
            "UPDATE players SET player_name=%s, category=%s, overseas=%s WHERE id=%s",
            (player_name, category, overseas, id)
        )
        
        # Update base price in auction_players if provided
        if base_price:
            cursor.execute(
                "UPDATE auction_players SET base_price=%s WHERE player_id=%s",
                (float(base_price), id)
            )
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    flash('Player updated!')
    return redirect('/admin/players')

@bp.route('/delete/<int:id>', methods=['POST'])
def delete_player(id):
    """Delete player if not sold in any auction"""
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Check if player is sold in any auction
        cursor.execute("SELECT status FROM auction_players WHERE player_id = %s AND status = 'sold'", (id,))
        sold = cursor.fetchone()
        
        if sold:
            return jsonify({'error': 'Cannot delete sold player'}), 400
        
        # Check if player exists in any auction
        cursor.execute("SELECT id FROM auction_players WHERE player_id = %s", (id,))
        in_auction = cursor.fetchone()
        
        if in_auction:
            # Remove from auction_players first (foreign key constraint)
            cursor.execute("DELETE FROM auction_players WHERE player_id = %s", (id,))
        
        # Delete from master players table
        cursor.execute("DELETE FROM players WHERE id = %s", (id,))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True})

@bp.route('/export')
def export_players():
    """Export all players to CSV"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT p.*, ap.base_price, ap.status, ap.sold_price, t.team_name
            FROM players p
            LEFT JOIN auction_players ap ON p.id = ap.player_id
            LEFT JOIN teams t ON ap.sold_team_id = t.id
            ORDER BY p.player_name
        """)
        players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Name', 'Category', 'Base Price', 'Status', 'Team', 'Sold Price', 'Overseas'])
    
    for p in players:
        writer.writerow([
            p['id'], p['player_name'], p['category'], p.get('base_price', ''),
            p.get('status', ''), p.get('team_name', ''), p.get('sold_price', ''), p['overseas']
        ])
    
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=players.csv'}
    )