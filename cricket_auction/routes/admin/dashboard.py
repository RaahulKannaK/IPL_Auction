from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
import mysql.connector

bp = Blueprint('admin_dashboard', __name__, url_prefix='/admin')

def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='raahul@185',
        database='cricket_auction'
    )

@bp.route('/')
def admin_panel():
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized access')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Auction stats
    cursor.execute("SELECT COUNT(*) as count FROM auctions")
    total_auctions = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM auctions WHERE status = 'live'")
    live_auctions = cursor.fetchone()['count']
    
    # Current auction
    cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
    current_auction = cursor.fetchone()
    
    # League info
    league_name = current_auction['league_name'] if current_auction else 'No Active Auction'
    auction_status = current_auction['status'] if current_auction else 'none'
    
    # Current player
    current_player = None
    current_bid = 0
    if current_auction and current_auction.get('current_player_id'):
        cursor.execute("""
            SELECT p.player_name, ap.sold_price, t.team_name as bidder
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            LEFT JOIN teams t ON ap.sold_team_id = t.id
            WHERE ap.id = %s
        """, (current_auction['current_player_id'],))
        current_player = cursor.fetchone()
        current_bid = float(current_auction.get('current_bid') or 0)
    
    # Stats
    cursor.execute("SELECT COUNT(*) as count FROM teams")
    total_teams = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM players")
    total_players = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM auction_players WHERE status = 'sold'")
    sold_players = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM auction_players WHERE status = 'available'")
    unsold_players = cursor.fetchone()['count']
    
    # Recent bids (global)
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
    
    # Hidden bid status (global - admin sees all)
    cursor.execute("""
        SELECT h.*, p.player_name, t.team_name
        FROM hidden_max_bids h
        JOIN auction_players ap ON h.auction_player_id = ap.id
        JOIN players p ON ap.player_id = p.id
        JOIN teams t ON h.team_id = t.id
        WHERE h.is_active = TRUE
        ORDER BY h.created_at DESC
    """)
    hidden_bids = cursor.fetchall()
    
    cursor.close()
    db.close()
    
    return render_template('admin/dashboard.html',
        league_name=league_name,
        auction_status=auction_status,
        current_player=current_player,
        current_bid=current_bid,
        stats={
            'total_auctions': total_auctions,
            'live_auctions': live_auctions,
            'teams': total_teams,
            'players': total_players,
            'sold': sold_players,
            'unsold': unsold_players
        },
        recent_bids=recent_bids,
        hidden_bids=hidden_bids,
        current_auction=current_auction
    )

@bp.route('/dashboard')
def admin_dashboard():
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized access')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # All auctions list
    cursor.execute("""
        SELECT a.*, 
               (SELECT COUNT(*) FROM teams WHERE auction_id = a.id) as team_count,
               (SELECT COUNT(*) FROM auction_players WHERE auction_id = a.id) as player_count,
               (SELECT COUNT(*) FROM auction_players WHERE auction_id = a.id AND status = 'sold') as sold_count
        FROM auctions a
        ORDER BY a.created_at DESC
    """)
    auctions = cursor.fetchall()
    
    # Active/live auctions
    cursor.execute("""
        SELECT a.*, 
               (SELECT COUNT(*) FROM teams WHERE auction_id = a.id) as team_count
        FROM auctions a
        WHERE a.status IN ('live', 'paused')
        ORDER BY a.created_at DESC
    """)
    active_auctions = cursor.fetchall()
    
    # Global stats for summary cards
    cursor.execute("SELECT COUNT(*) as count FROM teams")
    total_teams = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM players")
    total_players = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM auction_players WHERE status = 'sold'")
    sold_players = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM auction_players WHERE status = 'available'")
    unsold_players = cursor.fetchone()['count']
    
    # Current live auction stats
    current_bid = 0
    current_player = None
    current_auction = None
    
    cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
    live = cursor.fetchone()
    
    if live:
        current_auction = live
        current_bid = float(live.get('current_bid') or 0)
        
        if live.get('current_player_id'):
            cursor.execute("""
                SELECT p.player_name, t.team_name as bidder
                FROM auction_players ap
                JOIN players p ON ap.player_id = p.id
                LEFT JOIN teams t ON ap.sold_team_id = t.id
                WHERE ap.id = %s
            """, (live['current_player_id'],))
            current_player = cursor.fetchone()
    
    cursor.close()
    db.close()
    
    return render_template('admin/dashboard.html',
        auctions=auctions,
        active_auctions=active_auctions,
        stats={                          # ADD THIS
            'teams': total_teams,
            'players': total_players,
            'sold': sold_players,
            'unsold': unsold_players
        },
        current_bid=current_bid,         # ADD THIS
        current_player=current_player,     # ADD THIS
        current_auction=current_auction,   # ADD THIS
        is_admin_dashboard=True
    )

@bp.route('/enter-auction/<int:auction_id>')
def enter_auction(auction_id):
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
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
        return redirect('/admin/dashboard')
    
    session['active_auction_id'] = auction_id
    session['active_league_name'] = auction['league_name']
    
    return redirect('/auction')

@bp.route('/exit-auction')
def exit_auction():
    session.pop('active_auction_id', None)
    session.pop('active_team_id', None)
    session.pop('active_league_name', None)
    return redirect('/admin/dashboard')