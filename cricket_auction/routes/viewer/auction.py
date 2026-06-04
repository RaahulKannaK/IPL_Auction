from flask import Blueprint, render_template, session, flash, redirect
import mysql.connector

bp = Blueprint('viewer_auction', __name__, url_prefix='/viewer')

def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='raahul@185',
        database='cricket_auction'
    )

@bp.route('/auction')
def auction_view():
    if session.get('role') != 'viewer':
        flash('Unauthorized')
        return redirect('/')
    
    if not session.get('active_auction_id'):
        flash('Please select an auction first')
        return redirect('/viewer/dashboard')
    
    auction_id = session['active_auction_id']
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Current auction
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    
    league_name = auction['league_name'] if auction else 'No Active Auction'
    current_session = auction['status'] if auction else 'none'
    
    # Current player & bid
    current_player = None
    current_bid = 0
    if auction and auction.get('current_player_id'):
        cursor.execute("""
            SELECT p.player_name, t.team_name as bidder
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            LEFT JOIN teams t ON ap.sold_team_id = t.id
            WHERE ap.id = %s
        """, (auction['current_player_id'],))
        current_player = cursor.fetchone()
        current_bid = float(auction.get('current_bid') or 0)
    
    # All teams with squads IN THIS AUCTION
    cursor.execute("""
        SELECT t.*, u.username as owner_name
        FROM teams t
        LEFT JOIN users u ON t.owner_id = u.id
        WHERE t.auction_id = %s
    """, (auction_id,))
    teams = cursor.fetchall()
    
    for team in teams:
        cursor.execute("""
            SELECT p.*, tp.purchase_price as sold_price
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE tp.team_id = %s AND ap.auction_id = %s
            ORDER BY tp.purchase_price DESC
        """, (team['id'], auction_id))
        team['squad'] = cursor.fetchall()
        team['spent'] = float(team['spent'] or 0)
    
    # Sold players history IN THIS AUCTION
    cursor.execute("""
        SELECT p.player_name, ap.sold_price, t.team_name
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        JOIN teams t ON ap.sold_team_id = t.id
        WHERE ap.auction_id = %s AND ap.status = 'sold'
        ORDER BY ap.sold_at DESC
    """, (auction_id,))
    sold_players = cursor.fetchall()
    
    cursor.close()
    db.close()
    
    return render_template('viewer/auction.html',
        league_name=league_name,
        current_session=current_session,
        current_player=current_player,
        current_bid=current_bid,
        teams=teams,
        sold_players=sold_players
    )