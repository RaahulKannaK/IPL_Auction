from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db

bp = Blueprint('admin_auction', __name__, url_prefix='/admin')


def get_user_team(user_id, auction_id=None):
    """Get team owned by user"""
    db = get_db()
    cursor = db.cursor(dictionary=True)
    if auction_id:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s AND auction_id = %s", (user_id, auction_id))
    else:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    team = cursor.fetchone()
    cursor.close()
    db.close()
    return team

def get_min_bid_increment(current_bid):
    """Get minimum bid increment based on current bid amount"""
    if current_bid < 1.0:
        return 0.05  # 5L
    elif current_bid < 2.0:
        return 0.10  # 10L
    elif current_bid < 7.0:
        return 0.25  # 25L
    else:
        return 0.25  # 25L minimum for 7Cr+

@bp.route('/auction')
def auction_room():
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # FIX: Look for live/paused auction first (same logic as team owner)
    cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
    auction = cursor.fetchone()
    
    # If session has a different auction ID, verify it's still active
    session_auction_id = session.get('active_auction_id')
    if session_auction_id and (not auction or auction['id'] != session_auction_id):
        cursor.execute("SELECT * FROM auctions WHERE id = %s AND status IN ('live', 'paused')", (session_auction_id,))
        session_auction = cursor.fetchone()
        if session_auction:
            auction = session_auction
    
    # Update session with correct active auction
    if auction:
        session['active_auction_id'] = auction['id']
        session['active_league_name'] = auction['league_name']
    
    players = []
    if auction:
        cursor.execute("""
            SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, ap.sold_price, ap.skip_reason, ap.skip_notes
            FROM players p
            JOIN auction_players ap ON p.id = ap.player_id
            WHERE ap.auction_id = %s AND ap.status IN ('available', 'unsold')
            ORDER BY RAND()
        """, (auction['id'],))
        players = cursor.fetchall()
    
    teams = []
    if auction:
        cursor.execute("SELECT * FROM teams WHERE auction_id = %s", (auction['id'],))
        teams = cursor.fetchall()
    
    user_team = None
    if auction and session.get('role') == 'team_owner':
        user_team = get_user_team(session['user_id'], auction['id'])
    
    sessions_count = 0
    if auction:
        cursor.execute("SELECT COUNT(*) as count FROM auction_sessions WHERE auction_id = %s", (auction['id'],))
        result = cursor.fetchone()
        sessions_count = result['count'] if result else 0
    
    current_player = None
    current_bid = 0
    skip_votes = []
    total_teams = 0
    all_skipped = False
    has_bids = False
    
    if auction and auction.get('current_player_id'):
        cursor.execute("""
            SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, ap.skip_reason, ap.skip_notes
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            WHERE ap.id = %s
        """, (auction['current_player_id'],))
        current_player = cursor.fetchone()
        current_bid = float(auction.get('current_bid') or 0)
        
        # Check if any actual bids exist
        cursor.execute("""
            SELECT COUNT(*) as bid_count FROM bids 
            WHERE auction_id = %s AND auction_player_id = %s
        """, (auction['id'], auction['current_player_id']))
        bid_result = cursor.fetchone()
        has_bids = bid_result['bid_count'] > 0 if bid_result else False
        
        cursor.execute("""
            SELECT ps.*, t.team_name, u.username as skipped_by_name
            FROM player_skips ps
            JOIN teams t ON ps.team_id = t.id
            JOIN users u ON ps.skipped_by = u.id
            WHERE ps.auction_id = %s AND ps.auction_player_id = %s
            ORDER BY ps.skipped_at DESC
        """, (auction['id'], auction['current_player_id']))
        skip_votes = cursor.fetchall()
        
        cursor.execute("SELECT COUNT(*) as total FROM teams WHERE auction_id = %s", (auction['id'],))
        total_result = cursor.fetchone()
        total_teams = total_result['total'] if total_result else 0
        
        all_skipped = len(skip_votes) >= total_teams and total_teams > 0
    
    cursor.close()
    db.close()
    
    return render_template('admin/auction.html', 
        auction=auction, 
        players=players, 
        teams=teams,
        user_team=user_team,
        sessions_count=sessions_count,
        current_player=current_player,
        current_bid=current_bid,
        has_bids=has_bids,
        skip_votes=skip_votes,
        total_teams=total_teams,
        all_skipped=all_skipped
    )

@bp.route('/auction/start', methods=['POST'])
def start_auction():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        flash('Unauthorized')
        return redirect('/admin/auction')
    
    league_name = request.form.get('league_name', 'IPL Auction 2024')
    
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute(
        "INSERT INTO auctions (league_name, status, created_by) VALUES (%s, 'live', %s)",
        (league_name, session['user_id'])
    )
    db.commit()
    auction_id = cursor.lastrowid
    
    cursor.execute("SELECT id, base_price FROM players")
    all_players = cursor.fetchall()
    
    for player in all_players:
        cursor.execute("""
            INSERT INTO auction_players (auction_id, player_id, base_price, status)
            VALUES (%s, %s, %s, 'available')
            ON DUPLICATE KEY UPDATE auction_id = auction_id
        """, (auction_id, player['id'], player.get('base_price', 2.00)))
    
    db.commit()
    cursor.close()
    db.close()
    
    flash('Auction started!')
    return jsonify({'success': True, 'auction_id': auction_id})

@bp.route('/auction/pause', methods=['POST'])
def pause_auction():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE auctions SET status = 'paused' WHERE status = 'live'")
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'status': 'paused', 'success': True})

@bp.route('/auction/resume', methods=['POST'])
def resume_auction():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE auctions SET status = 'live' WHERE status = 'paused'")
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'status': 'live', 'success': True})

@bp.route('/auction/sell', methods=['POST'])
def sell_player():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    
    if not auction or not auction.get('current_bidder_id') or float(auction.get('current_bid') or 0) <= 0:
        cursor.close()
        db.close()
        return jsonify({'error': 'No active bid - cannot sell. Player goes unsold.'}), 400
    
    team_id = auction['current_bidder_id']
    sold_price = auction['current_bid']
    
    # Get player info for last_sold tracking
    cursor.execute("""
        SELECT p.player_name 
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        WHERE ap.id = %s
    """, (auction_player_id,))
    player_info = cursor.fetchone()
    player_name = player_info['player_name'] if player_info else 'Unknown'
    
    cursor.execute("SELECT * FROM auction_players WHERE id = %s AND auction_id = %s", (auction_player_id, auction_id))
    ap = cursor.fetchone()
    
    if not ap:
        cursor.close()
        db.close()
        return jsonify({'error': 'Player not in auction'}), 404
    
    # Update auction_player status
    cursor.execute(
        "UPDATE auction_players SET status = 'sold', sold_team_id = %s, sold_price = %s WHERE id = %s",
        (team_id, sold_price, auction_player_id)
    )
    
    # Update team spent
    cursor.execute(
        "UPDATE teams SET spent = spent + %s WHERE id = %s",
        (sold_price, team_id)
    )
    
    # Add to team_players with timestamp
    cursor.execute(
        "INSERT INTO team_players (team_id, auction_player_id, purchase_price, purchased_at) VALUES (%s, %s, %s, NOW())",
        (team_id, auction_player_id, sold_price)
    )
    
    # Update auction - clear current player BUT store last_sold info
    cursor.execute("""
        UPDATE auctions 
        SET current_player_id = NULL, 
            current_bid = 0, 
            current_bidder_id = NULL,
            last_sold_team_id = %s,
            last_sold_player_name = %s,
            last_sold_price = %s,
            last_sold_auction_player_id = %s,
            last_sold_at = NOW()
        WHERE id = %s
    """, (team_id, player_name, sold_price, auction_player_id, auction_id))
    
    # Clear skip records
    cursor.execute("DELETE FROM player_skips WHERE auction_id = %s AND auction_player_id = %s", (auction_id, auction_player_id))
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({
        'success': True, 
        'sold_to': team_id, 
        'price': sold_price,
        'player_name': player_name,
        'auction_player_id': auction_player_id
    })

@bp.route('/auction/unsold', methods=['POST'])
def mark_unsold():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    force_unsold = data.get('force', False)
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM auction_players WHERE id = %s AND auction_id = %s", (auction_player_id, auction_id))
    ap = cursor.fetchone()
    
    if not ap:
        cursor.close()
        db.close()
        return jsonify({'error': 'Player not in auction'}), 404
    
    # FIX: Check current_bid from auctions table, not auction_players
    cursor.execute("SELECT current_bid FROM auctions WHERE id = %s", (auction_id,))
    auction_row = cursor.fetchone()
    current_bid = float(auction_row['current_bid'] or 0) if auction_row else 0
    
    cursor.execute("""
        SELECT COUNT(DISTINCT team_id) as skip_count
        FROM player_skips
        WHERE auction_id = %s AND auction_player_id = %s
    """, (auction_id, auction_player_id))
    skip_result = cursor.fetchone()
    skip_count = skip_result['skip_count'] if skip_result else 0
    
    cursor.execute("SELECT COUNT(*) as total FROM teams WHERE auction_id = %s", (auction_id,))
    total_result = cursor.fetchone()
    total_teams = total_result['total'] if total_result else 0
    
    all_skipped = skip_count >= total_teams and total_teams > 0
    
    if current_bid > 0 and not all_skipped and not force_unsold:
        cursor.close()
        db.close()
        return jsonify({
            'error': 'There are active bids! Use "Sell Player" or wait for all teams to skip.',
            'skip_count': skip_count,
            'total_teams': total_teams,
            'all_skipped': all_skipped
        }), 400
    
    cursor.execute(
        "UPDATE auction_players SET status = 'unsold' WHERE id = %s",
        (auction_player_id,)
    )
    
    # Clear auction current player AND last_sold
    cursor.execute("""
        UPDATE auctions 
        SET current_player_id = NULL, 
            current_bid = 0, 
            current_bidder_id = NULL,
            last_sold_team_id = NULL,
            last_sold_player_name = NULL,
            last_sold_price = NULL,
            last_sold_auction_player_id = NULL,
            last_sold_at = NULL
        WHERE id = %s
    """, (auction_id,))
    
    cursor.execute("DELETE FROM player_skips WHERE auction_id = %s AND auction_player_id = %s", (auction_id, auction_player_id))
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({
        'success': True,
        'status': 'unsold',
        'skip_count': skip_count,
        'total_teams': total_teams
    })

@bp.route('/auction/undo', methods=['POST'])
def undo_sale():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_player_id = data.get('auction_player_id')
    auction_id = data.get('auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM auction_players WHERE id = %s AND auction_id = %s", (auction_player_id, auction_id))
    ap = cursor.fetchone()
    
    if not ap or ap['status'] != 'sold':
        cursor.close()
        db.close()
        return jsonify({'error': 'Player not sold'}), 400
    
    cursor.execute(
        "UPDATE teams SET spent = spent - %s WHERE id = %s",
        (ap['sold_price'], ap['sold_team_id'])
    )
    
    cursor.execute(
        "UPDATE auction_players SET status = 'available', sold_team_id = NULL, sold_price = NULL WHERE id = %s",
        (ap['id'],)
    )
    
    cursor.execute(
        "DELETE FROM team_players WHERE team_id = %s AND auction_player_id = %s",
        (ap['sold_team_id'], ap['id'])
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True})

# ==================== NEW ROUTE: REBID ====================
@bp.route('/auction/rebid', methods=['POST'])
def rebid_player():
    """
    Reset bidding for current player back to base price.
    Clears all bids, skips, and bidder info but keeps same player active.
    """
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    
    if not auction_id or not auction_player_id:
        return jsonify({'error': 'Missing auction_id or auction_player_id'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Verify auction exists and is live/paused
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    if not auction:
        cursor.close()
        db.close()
        return jsonify({'error': 'Auction not found'}), 404
    
    # Verify player is in this auction and currently active
    cursor.execute("""
        SELECT ap.*, p.player_name, p.category, p.overseas
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        WHERE ap.id = %s AND ap.auction_id = %s
    """, (auction_player_id, auction_id))
    player = cursor.fetchone()
    
    if not player:
        cursor.close()
        db.close()
        return jsonify({'error': 'Player not found in this auction'}), 404
    
    # Check if player is actually the current player
    if auction.get('current_player_id') != auction_player_id:
        cursor.close()
        db.close()
        return jsonify({'error': 'Player is not currently active in auction'}), 400
    
    # Delete all bids for this player in this auction
    cursor.execute(
        "DELETE FROM bids WHERE auction_id = %s AND auction_player_id = %s",
        (auction_id, auction_player_id)
    )
    
    # Clear all skip records for this player
    cursor.execute(
        "DELETE FROM player_skips WHERE auction_id = %s AND auction_player_id = %s",
        (auction_id, auction_player_id)
    )
    
    # Reset auction state to base price, clear bidder
    cursor.execute("""
        UPDATE auctions 
        SET current_bid = %s,
            current_bidder_id = NULL
        WHERE id = %s
    """, (player['base_price'], auction_id))
    
    # Clear any hidden max bids for this player (optional - remove if you want to keep them)
    cursor.execute(
        "UPDATE hidden_max_bids SET is_active = FALSE WHERE auction_player_id = %s",
        (auction_player_id,)
    )
    
    # Clear any purse reservations for this player
    cursor.execute(
        "UPDATE purse_reservations SET status = 'released' WHERE auction_player_id = %s",
        (auction_player_id,)
    )
    
    # Reset reserved amounts on teams (subtract reserved amounts for this player)
    cursor.execute("""
        UPDATE teams t
        SET reserved = GREATEST(0, t.reserved - COALESCE((
            SELECT reserved_amount FROM purse_reservations 
            WHERE auction_player_id = %s AND team_id = t.id AND status = 'released'
        ), 0))
        WHERE t.auction_id = %s
    """, (auction_player_id, auction_id))
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({
        'success': True,
        'player_name': player['player_name'],
        'base_price': float(player['base_price']),
        'auction_player_id': auction_player_id,
        'message': 'Bidding reset to base price'
    })

# ==================== NEW ROUTE: DESELECT PLAYER ====================
@bp.route('/auction/deselect_player', methods=['POST'])
def deselect_player():
    """
    Completely remove current player from auction display.
    Clears all bid state and returns player to pool.
    """
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    
    if not auction_id:
        return jsonify({'error': 'Missing auction_id'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Verify auction exists
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    if not auction:
        cursor.close()
        db.close()
        return jsonify({'error': 'Auction not found'}), 404
    
    # If auction_player_id provided, verify it matches current player
    if auction_player_id and auction.get('current_player_id') != auction_player_id:
        cursor.close()
        db.close()
        return jsonify({'error': 'Player mismatch'}), 400
    
    # Get the current player ID to clear skips if needed
    current_player_id = auction.get('current_player_id')
    
    # Clear auction current player and all bid state
    cursor.execute("""
        UPDATE auctions 
        SET current_player_id = NULL, 
            current_bid = 0, 
            current_bidder_id = NULL
        WHERE id = %s
    """, (auction_id,))
    
    # Clear skip records for the deselected player
    if current_player_id:
        cursor.execute(
            "DELETE FROM player_skips WHERE auction_id = %s AND auction_player_id = %s",
            (auction_id, current_player_id)
        )
    
    # Delete bids for the deselected player (optional - keep history if you want)
    if current_player_id:
        cursor.execute(
            "DELETE FROM bids WHERE auction_id = %s AND auction_player_id = %s",
            (auction_id, current_player_id)
        )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({
        'success': True,
        'message': 'Player deselected',
        'previous_player_id': current_player_id
    })

@bp.route('/auction/select_player', methods=['POST'])
def select_player():
    if session.get('role') not in ['admin', 'auctioneer', 'team_owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    
    if not auction_id:
        return jsonify({'error': 'Missing auction_id'}), 400
    
    try:
        auction_player_id = int(auction_player_id) if auction_player_id else None
        auction_id = int(auction_id)
    except (ValueError, TypeError):
        return jsonify({'error': f'Invalid IDs'}), 400
    
    if not auction_player_id:
        return jsonify({'error': 'Missing auction_player_id'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    if not auction:
        cursor.close()
        db.close()
        return jsonify({'error': f'Auction {auction_id} not found'}), 404
    
    cursor.execute("""
        SELECT ap.*, p.player_name, p.category, p.overseas
        FROM auction_players ap
        JOIN players p ON ap.player_id = p.id
        WHERE ap.id = %s AND ap.auction_id = %s
    """, (auction_player_id, auction_id))
    player = cursor.fetchone()
    
    if not player:
        cursor.close()
        db.close()
        return jsonify({'error': 'Player not found in this auction'}), 404
    
    # Clear previous skip records
    cursor.execute("DELETE FROM player_skips WHERE auction_id = %s AND auction_player_id = %s", (auction_id, auction_player_id))
    
    cursor.execute(
        "UPDATE auction_players SET skip_reason = NULL, skip_notes = NULL WHERE id = %s",
        (auction_player_id,)
    )
    
    # Set current bid to base price (this is NOT a real bid, just the starting point)
    cursor.execute("""
        UPDATE auctions 
        SET current_player_id = %s, current_bid = %s, current_bidder_id = NULL
        WHERE id = %s
    """, (auction_player_id, player['base_price'], auction_id))
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({
        'success': True,
        'player_name': player['player_name'],
        'category': player['category'],
        'base_price': float(player['base_price']),
        'auction_player_id': auction_player_id,
        'overseas': player.get('overseas', False)
    })

@bp.route('/auction/status')
def get_status():
    auction_id = request.args.get('auction_id')
    team_id = request.args.get('team_id', type=int)  # Get team_id from request
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    if auction_id:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    else:
        cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
    
    auction = cursor.fetchone()
    
    if not auction:
        cursor.close()
        db.close()
        return jsonify({'status': 'none'})
    
    result = {
        'status': auction['status'],
        'league_name': auction.get('league_name'),
        'auction_id': auction['id'],
        'current_bid': float(auction.get('current_bid') or 0),
        'current_bidder_id': auction.get('current_bidder_id'),
        'current_bidder': None,
        'current_player': None,
        'player_category': None,
        'base_price': 0,
        'auction_player_id': None,
        'overseas': False,
        'has_bids': False,
        'skip_count': 0,
        'total_teams': 0,
        'all_skipped': False,
        # Last sold tracking
        'last_sold_team_id': auction.get('last_sold_team_id'),
        'last_sold_player_name': auction.get('last_sold_player_name'),
        'last_sold_price': float(auction.get('last_sold_price') or 0),
        'last_sold_auction_player_id': auction.get('last_sold_auction_player_id'),
        'last_sold_at': str(auction.get('last_sold_at')) if auction.get('last_sold_at') else None,
        # Willing price notification
        'sold_to_team_id': None,
        'sold_player_name': None,
        'sold_price': 0,
        'sold_auction_player_id': None,
        'willing_price_set': False
    }
    
    if auction.get('current_bidder_id'):
        cursor.execute("SELECT team_name FROM teams WHERE id = %s", (auction['current_bidder_id'],))
        bidder = cursor.fetchone()
        if bidder:
            result['current_bidder'] = bidder['team_name']
    
    if auction.get('current_player_id'):
        cursor.execute("""
            SELECT p.player_name, p.category, p.overseas, ap.base_price, ap.id as auction_player_id
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            WHERE ap.id = %s
        """, (auction['current_player_id'],))
        player = cursor.fetchone()
        if player:
            result['current_player'] = player['player_name']
            result['player_category'] = player['category']
            result['base_price'] = float(player['base_price'])
            result['auction_player_id'] = player['auction_player_id']
            result['overseas'] = player.get('overseas', False)
        
        cursor.execute("""
            SELECT COUNT(*) as bid_count FROM bids 
            WHERE auction_id = %s AND auction_player_id = %s
        """, (auction['id'], auction['current_player_id']))
        bid_result = cursor.fetchone()
        result['has_bids'] = bid_result['bid_count'] > 0 if bid_result else False
        
        cursor.execute("""
            SELECT COUNT(DISTINCT team_id) as skip_count
            FROM player_skips
            WHERE auction_id = %s AND auction_player_id = %s
        """, (auction['id'], auction['current_player_id']))
        skip_result = cursor.fetchone()
        result['skip_count'] = skip_result['skip_count'] if skip_result else 0
        
        cursor.execute("SELECT COUNT(*) as total FROM teams WHERE auction_id = %s", (auction['id'],))
        total_result = cursor.fetchone()
        result['total_teams'] = total_result['total'] if total_result else 0
        result['all_skipped'] = result['skip_count'] >= result['total_teams'] and result['total_teams'] > 0
    
    # Check if THIS team needs to set willing price for last sold player
    if team_id and auction.get('last_sold_team_id') == team_id:
        # Check if willing price already set
        cursor.execute("""
            SELECT id FROM hidden_max_bids 
            WHERE auction_player_id = %s AND team_id = %s AND is_active = TRUE
        """, (auction['last_sold_auction_player_id'], team_id))
        has_willing = cursor.fetchone()
        
        if not has_willing:
            result['sold_to_team_id'] = team_id
            result['sold_player_name'] = auction.get('last_sold_player_name')
            result['sold_price'] = float(auction.get('last_sold_price') or 0)
            result['sold_auction_player_id'] = auction.get('last_sold_auction_player_id')
            result['willing_price_set'] = False
        else:
            result['willing_price_set'] = True
    
    cursor.close()
    db.close()
    
    return jsonify(result)

@bp.route('/auction/bid', methods=['POST'])
def place_bid():
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    team_id = data.get('team_id')
    amount = float(data.get('amount', 0))
    
    user_role = session.get('role')
    
    if user_role == 'team_owner':
        user_team = get_user_team(session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only bid for your own team'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Check if this team skipped this player
    cursor.execute("""
        SELECT * FROM player_skips 
        WHERE auction_id = %s AND auction_player_id = %s AND team_id = %s
    """, (auction_id, auction_player_id, team_id))
    existing_skip = cursor.fetchone()
    if existing_skip:
        cursor.close()
        db.close()
        return jsonify({'error': 'You skipped this player. Cannot bid.'}), 400
    
    cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
    team = cursor.fetchone()
    
    if not team:
        cursor.close()
        db.close()
        return jsonify({'error': 'Team not found'}), 404
    
    # Check if this team is the current highest bidder
    cursor.execute("""
        SELECT team_id FROM bids 
        WHERE auction_id = %s AND auction_player_id = %s 
        ORDER BY bid_amount DESC, created_at DESC LIMIT 1
    """, (auction_id, auction_player_id))
    last_bid = cursor.fetchone()
    if last_bid and last_bid['team_id'] == team_id:
        cursor.close()
        db.close()
        return jsonify({'error': 'You are already the highest bidder.'}), 400
    
    cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
    auction = cursor.fetchone()
    
    if auction['status'] != 'live':
        cursor.close()
        db.close()
        return jsonify({'error': 'Auction not live'}), 400
    
    # Get base price from auction_players
    cursor.execute("SELECT base_price FROM auction_players WHERE id = %s", (auction_player_id,))
    ap_row = cursor.fetchone()
    base_price = float(ap_row['base_price']) if ap_row else float(auction.get('base_price') or 2.0)
    
    # Check if any bids exist
    cursor.execute("""
        SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
        FROM bids 
        WHERE auction_id = %s AND auction_player_id = %s
    """, (auction_id, auction_player_id))
    bid_info = cursor.fetchone()
    has_bids = bid_info['bid_count'] > 0
    highest_bid = float(bid_info['highest_bid']) if bid_info['highest_bid'] else 0
    
    # INITIAL BID: No bids yet
    if not has_bids:
        if amount < base_price:
            cursor.close()
            db.close()
            return jsonify({'error': f'Initial bid must be at least base price ₹{base_price:.2f}Cr'}), 400
        # No increment check for initial bid
    
    # SUBSEQUENT BID: Bids exist
    else:
        current_bid = highest_bid
        
        if amount <= current_bid:
            cursor.close()
            db.close()
            return jsonify({'error': f'Bid must be higher than current bid ₹{current_bid:.2f}Cr'}), 400
        
        min_increment = get_min_bid_increment(current_bid)
        if amount < current_bid + min_increment:
            cursor.close()
            db.close()
            return jsonify({'error': f'Bid must be at least ₹{min_increment:.2f}Cr higher than current bid'}), 400
    
    # Check hidden max bids
    cursor.execute(
        "SELECT max_bid FROM hidden_max_bids WHERE auction_player_id = %s AND team_id = %s AND is_active = TRUE",
        (auction_player_id, team_id)
    )
    hidden = cursor.fetchone()
    hidden_amount = float(hidden['max_bid']) if hidden else 0
    
    available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
    
    if amount > available + hidden_amount:
        cursor.close()
        db.close()
        return jsonify({'error': f'Insufficient funds. Available: ₹{available + hidden_amount:.2f}Cr'}), 400
    
    # Place bid
    cursor.execute(
        "INSERT INTO bids (auction_id, auction_player_id, team_id, bid_amount) VALUES (%s, %s, %s, %s)",
        (auction_id, auction_player_id, team_id, amount)
    )
    
    cursor.execute(
        "UPDATE auctions SET current_bid = %s, current_bidder_id = %s, current_player_id = %s WHERE id = %s",
        (amount, team_id, auction_player_id, auction_id)
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({
        'success': True, 
        'current_bid': amount, 
        'bidder': team['team_name'],
        'check_auto': True
    })

@bp.route('/auction/hidden_bid', methods=['POST'])
def place_hidden_bid():
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    team_id = data.get('team_id')
    max_amount = float(data.get('max_amount', 0))
    
    user_team = get_user_team(session['user_id'], auction_id)
    if session.get('role') == 'team_owner' and (not user_team or user_team['id'] != team_id):
        return jsonify({'error': 'You can only set hidden bids for your own team'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
    team = cursor.fetchone()
    
    if not team:
        cursor.close()
        db.close()
        return jsonify({'error': 'Team not found'}), 404
    
    available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
    
    if max_amount > available:
        cursor.close()
        db.close()
        return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
    
    cursor.execute(
        "DELETE FROM hidden_max_bids WHERE auction_player_id = %s AND team_id = %s",
        (auction_player_id, team_id)
    )
    
    cursor.execute(
        "INSERT INTO hidden_max_bids (auction_id, auction_player_id, team_id, max_bid) VALUES (%s, %s, %s, %s)",
        (auction_id, auction_player_id, team_id, max_amount)
    )
    
    cursor.execute(
        "INSERT INTO purse_reservations (team_id, auction_player_id, reserved_amount) VALUES (%s, %s, %s)",
        (team_id, auction_player_id, max_amount)
    )
    
    cursor.execute(
        "UPDATE teams SET reserved = reserved + %s WHERE id = %s",
        (max_amount, team_id)
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})

@bp.route('/auction/auto_bid', methods=['POST'])
def auto_counter_bid():
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    current_bid = float(data.get('current_bid', 0))
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT h.*, t.team_name, t.purse_limit, t.spent, t.reserved 
        FROM hidden_max_bids h 
        JOIN teams t ON h.team_id = t.id 
        WHERE h.auction_player_id = %s AND h.is_active = TRUE AND h.max_bid > %s
        ORDER BY h.max_bid DESC
    """, (auction_player_id, current_bid))
    
    hidden_bids = cursor.fetchall()
    
    if not hidden_bids:
        cursor.close()
        db.close()
        return jsonify({'auto_bid': False})
    
    winner = hidden_bids[0]
    next_bid = current_bid + 0.5
    if next_bid > winner['max_bid']:
        next_bid = winner['max_bid']
    
    cursor.execute(
        "SELECT reserved_amount FROM purse_reservations WHERE auction_player_id = %s AND team_id = %s AND status = 'active'",
        (auction_player_id, winner['team_id'])
    )
    res = cursor.fetchone()
    reserved_amount = float(res['reserved_amount']) if res else 0
    
    available = float(winner['purse_limit']) - float(winner['spent'] or 0) - (float(winner['reserved'] or 0) - reserved_amount)
    
    if next_bid > available + reserved_amount:
        cursor.close()
        db.close()
        return jsonify({'auto_bid': False, 'reason': 'Insufficient funds'})
    
    cursor.execute(
        "INSERT INTO bids (auction_id, auction_player_id, team_id, bid_amount) VALUES (%s, %s, %s, %s)",
        (auction_id, auction_player_id, winner['team_id'], next_bid)
    )
    
    cursor.execute(
        "UPDATE auctions SET current_bid = %s, current_bidder_id = %s, current_player_id = %s WHERE id = %s",
        (next_bid, winner['team_id'], auction_player_id, auction_id)
    )
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({
        'auto_bid': True,
        'team': winner['team_name'],
        'amount': next_bid
    })