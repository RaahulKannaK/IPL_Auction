from flask import Blueprint, render_template, session, flash, jsonify

from database.db import get_db

bp = Blueprint('admin_reports', __name__, url_prefix='/admin/reports')



@bp.route('/')
def reports_dashboard():
    if session.get('role') not in [ 'team_owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    # Team spending
    cursor.execute("""
        SELECT t.team_name, t.purse_limit, t.spent, t.reserved,
               (t.spent / t.purse_limit * 100) as spent_pct
        FROM teams t
        ORDER BY t.spent DESC
    """)
    team_spending = cursor.fetchall()
    
    # Auction history
    cursor.execute("""
        SELECT p.player_name, ap.sold_price, t.team_name, ap.sold_at
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        JOIN teams t ON ap.sold_team_id = t.id
        WHERE ap.status = 'sold'
        ORDER BY ap.sold_at DESC
    """)
    auction_history = cursor.fetchall()
    
    # Sold players
    cursor.execute("""
        SELECT p.*, ap.sold_price, t.team_name
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        JOIN teams t ON ap.sold_team_id = t.id
        WHERE ap.status = 'sold'
        ORDER BY ap.sold_price DESC
    """)
    sold_players = cursor.fetchall()
    
    # Unsold players
    cursor.execute("""
        SELECT p.*, ap.base_price
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        WHERE ap.status = 'available'
        ORDER BY ap.base_price DESC
    """)
    unsold_players = cursor.fetchall()
    
    cursor.close()
    db.close()
    
    return render_template('admin/reports.html',
        team_spending=team_spending,
        auction_history=auction_history,
        sold_players=sold_players,
        unsold_players=unsold_players
    )

@bp.route('/team_spending')
def team_spending_api():
    if session.get('role') not in [ 'team_owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    cursor.execute("""
        SELECT t.team_name, t.purse_limit, t.spent, t.reserved
        FROM teams t
    """)
    data = cursor.fetchall()
    cursor.close()
    db.close()
    
    return jsonify({'team_spending': data})

@bp.route('/auction_history')
def auction_history_api():
    if session.get('role') not in [ 'team_owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    cursor.execute("""
        SELECT p.player_name, ap.sold_price, t.team_name
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        JOIN teams t ON ap.sold_team_id = t.id
        WHERE ap.status = 'sold'
        ORDER BY ap.sold_price DESC
    """)
    data = cursor.fetchall()
    cursor.close()
    db.close()
    
    return jsonify({'auction_history': data})