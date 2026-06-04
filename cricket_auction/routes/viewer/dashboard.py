from flask import Blueprint, render_template, session, flash, redirect
import mysql.connector

bp = Blueprint('viewer_dashboard', __name__, url_prefix='/viewer')

def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='raahul@185',
        database='cricket_auction'
    )

@bp.route('/dashboard')
def dashboard():
    if session.get('role') != 'viewer':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # All active/live auctions for viewer to pick from
    cursor.execute("""
        SELECT a.*, 
               (SELECT COUNT(*) FROM teams WHERE auction_id = a.id) as team_count,
               (SELECT COUNT(*) FROM auction_players WHERE auction_id = a.id AND status = 'sold') as sold_count
        FROM auctions a
        WHERE a.status IN ('live', 'paused', 'pending')
        ORDER BY a.created_at DESC
    """)
    auctions = cursor.fetchall()
    
    # Also get completed auctions for history
    cursor.execute("""
        SELECT a.*,
               (SELECT COUNT(*) FROM teams WHERE auction_id = a.id) as team_count
        FROM auctions a
        WHERE a.status = 'completed'
        ORDER BY a.created_at DESC
        LIMIT 5
    """)
    completed_auctions = cursor.fetchall()
    
    cursor.close()
    db.close()
    
    return render_template('viewer/dashboard.html',
        auctions=auctions,
        completed_auctions=completed_auctions
    )

@bp.route('/enter-auction/<int:auction_id>')
def enter_auction(auction_id):
    if session.get('role') != 'viewer':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    cursor.close()
    db.close()
    
    if not auction:
        flash('Auction not found')
        return redirect('/viewer/dashboard')
    
    session['active_auction_id'] = auction_id
    session['active_league_name'] = auction['league_name']
    
    return redirect('/viewer/auction')

@bp.route('/exit-auction')
def exit_auction():
    session.pop('active_auction_id', None)
    session.pop('active_league_name', None)
    return redirect('/viewer/dashboard')