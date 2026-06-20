from flask import Blueprint, render_template, request, redirect, session, flash, jsonify, Response
from database.db import get_db, get_cached, clear_cache
import csv
from io import StringIO
import json

bp = Blueprint('admin_players', __name__, url_prefix='/admin/players')

@bp.route('/')
def list_players():
    """List all players — now with session tabs at top"""
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    auction_id = session.get('active_auction_id')
    if not auction_id:
        flash('Please enter an auction room first')
        return redirect('/admin/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Get all sessions for this auction (for the tabs)
        cursor.execute("""
            SELECT s.*, 
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id) as player_count,
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id AND sp.status = 'sold') as sold_count
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY s.created_at ASC
        """, (auction_id,))
        all_sessions = cursor.fetchall()
        
        # Parse team_ids for each session
        for sess in all_sessions:
            if sess.get('team_ids'):
                try:
                    sess['team_ids_list'] = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                except:
                    sess['team_ids_list'] = []
            else:
                sess['team_ids_list'] = []
            
            cursor.execute("SELECT COUNT(*) as cnt FROM teams WHERE auction_id = %s", (auction_id,))
            total_teams = cursor.fetchone()['cnt']
            sess['total_teams'] = total_teams
            sess['team_count'] = len(sess['team_ids_list'])
            sess['is_full'] = len(sess['team_ids_list']) == total_teams
        
        # Get selected session from query param
        selected_session_id = request.args.get('session_id', type=int)
        
        # If session selected, show session players
        session_players = []
        selected_session = None
        session_stats = None
        
        if selected_session_id:
            cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (selected_session_id,))
            selected_session = cursor.fetchone()
            
            if selected_session:
                cursor.execute("""
                    SELECT sp.*, p.player_name, p.category, p.overseas, 
                           t.team_name as sold_team_name
                    FROM session_players sp
                    JOIN players p ON sp.player_id = p.id
                    LEFT JOIN teams t ON sp.sold_team_id = t.id
                    WHERE sp.session_id = %s
                    ORDER BY 
                        CASE sp.status 
                            WHEN 'available' THEN 1 
                            WHEN 'in_auction' THEN 2 
                            WHEN 'sold' THEN 3 
                            WHEN 'unsold' THEN 4 
                        END,
                        p.player_name
                """, (selected_session_id,))
                session_players = cursor.fetchall()
                
                session_stats = {
                    'total': len(session_players),
                    'sold': sum(1 for p in session_players if p['status'] == 'sold'),
                    'available': sum(1 for p in session_players if p['status'] == 'available'),
                    'unsold': sum(1 for p in session_players if p['status'] == 'unsold'),
                    'total_sold': sum(p['sold_price'] or 0 for p in session_players if p['status'] == 'sold')
                }
        
        # Always get master pool players too (for reference)
        cursor.execute("""
            SELECT p.*, ap.base_price, ap.status as master_status, ap.sold_price, t.team_name
            FROM players p
            LEFT JOIN auction_players ap ON p.id = ap.player_id AND ap.auction_id = %s
            LEFT JOIN teams t ON ap.sold_team_id = t.id
            ORDER BY p.player_name
        """, (auction_id,))
        master_players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/players.html',
        all_sessions=all_sessions,
        selected_session_id=selected_session_id,
        selected_session=selected_session,
        session_players=session_players,
        session_stats=session_stats,
        players=master_players,  # backward compat
        view_mode='session' if selected_session_id else 'master'
    )

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
        cursor.execute(
            "INSERT INTO players (player_name, category, overseas) VALUES (%s, %s, %s)",
            (player_name, category, overseas)
        )
        player_id = cursor.lastrowid
        
        cursor.execute(
            "INSERT INTO auction_players (auction_id, player_id, base_price, status) VALUES (%s, %s, %s, 'available')",
            (auction_id, player_id, base_price)
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
                    "INSERT INTO auction_players (auction_id, player_id, base_price, status) VALUES (%s, %s, %s, 'available')",
                    (auction_id, player_id, base_price)
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
        cursor.execute(
            "UPDATE players SET player_name=%s, category=%s, overseas=%s WHERE id=%s",
            (player_name, category, overseas, id)
        )
        
        if base_price:
            cursor.execute(
                "UPDATE auction_players SET base_price=%s WHERE player_id=%s AND auction_id=%s",
                (float(base_price), id, auction_id)
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
        cursor.execute("SELECT status FROM auction_players WHERE player_id = %s AND status = 'sold'", (id,))
        sold = cursor.fetchone()
        
        if sold:
            return jsonify({'error': 'Cannot delete sold player'}), 400
        
        cursor.execute("SELECT id FROM auction_players WHERE player_id = %s", (id,))
        in_auction = cursor.fetchone()
        
        if in_auction:
            cursor.execute("DELETE FROM auction_players WHERE player_id = %s", (id,))
        
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