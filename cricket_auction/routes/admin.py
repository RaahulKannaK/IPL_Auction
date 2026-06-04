from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
import mysql.connector
import csv
from io import StringIO
from flask import Response

bp = Blueprint('admin', __name__, url_prefix='/admin')

def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='raahul@185',
        database='cricket_auction'
    )

@bp.route('/')
def admin_panel():
    if session.get('role') not in ['owner', 'admin']:
        flash('Unauthorized access')
        return redirect('/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Stats
    cursor.execute("SELECT COUNT(*) as count FROM users")
    total_users = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM teams")
    total_teams = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM players")
    total_players = cursor.fetchone()['count']
    
    # Sold/unsold from auction_players, not players
    cursor.execute("SELECT COUNT(*) as count FROM auction_players WHERE status = 'sold'")
    sold_players = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM auction_players WHERE status = 'available'")
    unsold_players = cursor.fetchone()['count']
    
    cursor.execute("SELECT SUM(spent) as total FROM teams")
    total_spent = cursor.fetchone()['total'] or 0
    
    # Users list - no team_id in users table, use teams.owner_id
    cursor.execute("""
        SELECT u.*, t.team_name 
        FROM users u 
        LEFT JOIN teams t ON u.id = t.owner_id 
        ORDER BY u.created_at DESC
    """)
    users = cursor.fetchall()
    
    # Auctions
    cursor.execute("SELECT * FROM auctions ORDER BY created_at DESC")
    auctions = cursor.fetchall()
    
    # Recent bids - join through auction_players to get player names
    cursor.execute("""
        SELECT b.*, p.player_name, t.team_name
        FROM bids b
        JOIN auction_players ap ON b.auction_player_id = ap.id
        JOIN players p ON ap.player_id = p.id
        JOIN teams t ON b.team_id = t.id
        ORDER BY b.created_at DESC
        LIMIT 20
    """)
    recent_bids = cursor.fetchall()
    
    cursor.close()
    db.close()
    
    return render_template('admin.html', 
        stats={
            'users': total_users,
            'teams': total_teams,
            'players': total_players,
            'sold': sold_players,
            'unsold': unsold_players,
            'spent': total_spent
        },
        users=users,
        auctions=auctions,
        recent_bids=recent_bids
    )

@bp.route('/users/create', methods=['POST'])
def create_user():
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    username = request.form['username']
    password = request.form['password']
    role = request.form['role']
    
    # Schema has no team_id in users table - owner_id is in teams table
    # If assigning team, update teams table separately or use existing owner_id logic
    
    from werkzeug.security import generate_password_hash
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO users (username, password_hash, role) 
            VALUES (%s, %s, %s)
        """, (username, generate_password_hash(password), role))
        db.commit()
        flash('User created successfully!')
    except Exception as e:
        flash('Error: ' + str(e))
    
    cursor.close()
    db.close()
    return redirect('/admin')

@bp.route('/users/delete/<int:id>', methods=['POST'])
def delete_user(id):
    if session.get('role') != 'owner':
        return jsonify({'error': 'Only owner can delete'}), 403
    
    if id == session.get('user_id'):
        return jsonify({'error': 'Cannot delete yourself'}), 400
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM users WHERE id = %s", (id,))
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True})

@bp.route('/auction/reset', methods=['POST'])
def reset_auction():
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    
    # Reset all auction_players (not players table)
    cursor.execute("UPDATE auction_players SET status = 'available', sold_team_id = NULL, sold_price = NULL")
    
    # Reset all teams
    cursor.execute("UPDATE teams SET spent = 0, reserved = 0")
    
    # Clear bids
    cursor.execute("DELETE FROM bids")
    
    # Clear hidden_max_bids
    cursor.execute("DELETE FROM hidden_max_bids")
    
    # Clear purse_reservations
    cursor.execute("DELETE FROM purse_reservations")
    
    # Clear team_players
    cursor.execute("DELETE FROM team_players")
    
    # Clear playing11
    cursor.execute("DELETE FROM playing11")
    
    # Reset auction_sessions
    cursor.execute("UPDATE auction_sessions SET status = 'completed', current_player_id = NULL, current_bid = NULL, current_bidder_id = NULL")
    
    # Close auctions
    cursor.execute("UPDATE auctions SET status = 'completed', current_player_id = NULL, current_bid = 0, current_bidder_id = NULL")
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True, 'message': 'Auction reset complete'})

@bp.route('/reports')
def reports():
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Top buys - from auction_players joined with players and teams
    cursor.execute("""
        SELECT p.player_name, ap.sold_price, t.team_name
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        JOIN teams t ON ap.sold_team_id = t.id
        WHERE ap.status = 'sold'
        ORDER BY ap.sold_price DESC
        LIMIT 10
    """)
    top_buys = cursor.fetchall()
    
    # Team spending - use correct column names (purse_limit, not purse)
    cursor.execute("""
        SELECT t.team_name, t.purse_limit, t.spent, t.reserved,
               (t.spent / t.purse_limit * 100) as spent_pct
        FROM teams t
        ORDER BY t.spent DESC
    """)
    team_spending = cursor.fetchall()
    
    # Category distribution - join auction_players with players
    cursor.execute("""
        SELECT p.category, COUNT(*) as count, AVG(ap.sold_price) as avg_price
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        WHERE ap.status = 'sold'
        GROUP BY p.category
    """)
    category_stats = cursor.fetchall()
    
    cursor.close()
    db.close()
    
    return jsonify({
        'top_buys': top_buys,
        'team_spending': team_spending,
        'category_stats': category_stats
    })

@bp.route('/export/players')
def export_players():
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT p.*, ap.base_price, ap.status, ap.sold_price, t.team_name
        FROM players p
        LEFT JOIN auction_players ap ON p.id = ap.player_id
        LEFT JOIN teams t ON ap.sold_team_id = t.id
    """)
    players = cursor.fetchall()
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