from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('admin_auction', __name__, url_prefix='/admin')

def get_user_team(cursor, user_id, auction_id=None):
    """Get team owned by user — uses passed cursor, no new connection"""
    if auction_id:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s AND auction_id = %s", (user_id, auction_id))
    else:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    return cursor.fetchone()

def get_min_bid_increment(current_bid):
    """Get minimum bid increment based on current bid amount"""
    if current_bid < 1.0:
        return 0.05
    elif current_bid < 2.0:
        return 0.10
    elif current_bid < 7.0:
        return 0.25
    else:
        return 0.25


# ==================== SESSION MANAGEMENT ====================

@bp.route('/auction/sessions', methods=['GET', 'POST'])
def manage_sessions():
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') not in ['admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    # Get auction_id from query param OR form OR find latest active
    auction_id = request.args.get('auction') or request.form.get('auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        if auction_id:
            cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
            auction = cursor.fetchone()
        else:
            cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
            auction = cursor.fetchone()
        
        if not auction:
            flash('No active auction found')
            return redirect('/admin/')
        
        auction_id = auction['id']
        
        # GET - Show sessions list
        if request.method == 'GET':
            cursor.execute("""
                SELECT * FROM auction_sessions 
                WHERE auction_id = %s 
                ORDER BY created_at DESC
            """, (auction_id,))
            sessions = cursor.fetchall()
            
            # Parse team_ids JSON for each session
            for sess in sessions:
                if sess.get('team_ids'):
                    try:
                        import json
                        sess['team_ids'] = json.loads(sess['team_ids'])
                    except:
                        sess['team_ids'] = []
            
            # Get all teams for this auction
            cursor.execute("SELECT * FROM teams WHERE auction_id = %s", (auction_id,))
            teams = cursor.fetchall()
            
            return render_template('admin/sessions.html',
                auction=auction,
                sessions=sessions,
                teams=teams,
                total_teams=len(teams)
            )
        
        # POST - Create new session
        if request.method == 'POST':
            session_name = request.form.get('session_name', 'Session ' + str(int(time.time())))
            team_ids = request.form.getlist('team_ids')
            
            if not team_ids:
                flash('Select at least one team')
                return redirect(f'/admin/auction/sessions?auction={auction_id}')
            
            import json
            team_ids_json = json.dumps([int(t) for t in team_ids])
            
            # Set time based on slot type (optional enhancement)
            slot_type = request.form.get('slot_type', 'morning')
            start_time = time.strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute("""
                INSERT INTO auction_sessions (auction_id, session_name, team_ids, status, start_time)
                VALUES (%s, %s, %s, 'active', %s)
            """, (auction_id, session_name, team_ids_json, start_time))
            
            db.commit()
            new_session_id = cursor.lastrowid
            
            # INSTANT REDIRECT to enter the session
            return redirect(f'/admin/auction/session/{new_session_id}/enter')
            
    finally:
        cursor.close()
        db.close()

@bp.route('/auction/session/<int:session_id>/enter')
def enter_session(session_id):
    """Enter a specific session - sets session in Flask session and redirects to auction room"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') not in ['admin', 'auctioneer', 'team_owner']:
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
        auction_session = cursor.fetchone()
        
        if not auction_session:
            flash('Session not found')
            return redirect('/admin/')
        
        # Set active session in Flask session
        session['active_session_id'] = session_id
        session['active_auction_id'] = auction_session['auction_id']
        
        # Update session start time if not set
        if not auction_session['start_time']:
            cursor.execute("UPDATE auction_sessions SET start_time = NOW() WHERE id = %s", (session_id,))
            db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return redirect('/admin/auction')


@bp.route('/auction/session/<int:session_id>/close', methods=['POST'])
def close_session(session_id):
    """Close/completed a session"""
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("""
            UPDATE auction_sessions 
            SET status = 'completed', end_time = NOW() 
            WHERE id = %s
        """, (session_id,))
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    # Clear session from Flask session if it was active
    if session.get('active_session_id') == session_id:
        session.pop('active_session_id', None)
    
    clear_cache('auction:status')
    return jsonify({'success': True, 'message': 'Session closed'})


# ==================== MAIN AUCTION ROOM ====================

@bp.route('/auction')
def auction_room():
    """Main auction room — requires active session"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') not in ['admin', 'auctioneer', 'team_owner']:
        flash('Unauthorized')
        return redirect('/')
    
    # Check if user has an active session in Flask session
    active_session_id = session.get('active_session_id')
    active_auction_id = session.get('active_auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Find the auction
        if active_auction_id:
            cursor.execute("SELECT * FROM auctions WHERE id = %s AND status IN ('live', 'paused')", (active_auction_id,))
        else:
            cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
        auction = cursor.fetchone()
        
        if not auction:
            flash('No active auction found')
            return redirect('/admin/')
        
        auction_id = auction['id']
        session['active_auction_id'] = auction_id
        
        # === CHECK: Are there any sessions for this auction? ===
        cursor.execute("""
            SELECT * FROM auction_sessions 
            WHERE auction_id = %s AND status IN ('active', 'paused')
            ORDER BY created_at DESC LIMIT 1
        """, (auction_id,))
        existing_session = cursor.fetchone()
        
        # If NO session exists at all for this auction
        if not existing_session:
            if session.get('role') in ['admin', 'auctioneer']:
                flash('⚠️ No session created yet. Create a session first to start bidding.')
                return redirect(f'/admin/auction/sessions?auction={auction_id}')
            else:
                flash('⏳ No active session available. Please wait for admin to create one.')
                return redirect('/admin/')
        
        # If session exists but user hasn't joined one
        if not active_session_id:
            # Auto-join team owners to the existing session
            if session.get('role') == 'team_owner':
                session['active_session_id'] = existing_session['id']
                active_session_id = existing_session['id']
            else:
                # Admin/auctioneer should explicitly pick or create
                flash('Select a session to enter or create a new one.')
                return redirect(f'/admin/auction/sessions?auction={auction_id}')
        
        # === LOAD AUCTION ROOM WITH SESSION ===
        cursor.execute("""
            SELECT s.*, a.league_name, a.status as auction_status, a.squad_size, 
                   a.purse_limit, a.overseas_limit, a.current_player_id, 
                   a.current_bid, a.current_bidder_id
            FROM auction_sessions s
            JOIN auctions a ON s.auction_id = a.id
            WHERE s.id = %s
        """, (active_session_id,))
        auction_session = cursor.fetchone()
        
        if not auction_session:
            session.pop('active_session_id', None)
            flash('Session expired. Please select again.')
            return redirect(f'/admin/auction/sessions?auction={auction_id}')
        
        # ... rest of your existing auction room loading code ...
        # (teams, players, current_player, etc.)
        
        # Get teams in this session
        session_team_ids = []
        if auction_session.get('team_ids'):
            try:
                import json
                session_team_ids = json.loads(auction_session['team_ids'])
            except:
                session_team_ids = []
        
        # Get all teams for this auction
        cursor.execute("SELECT * FROM teams WHERE auction_id = %s", (auction_id,))
        all_teams = cursor.fetchall()
        
        # Filter to session teams
        session_teams = [t for t in all_teams if t['id'] in session_team_ids] if session_team_ids else all_teams
        
        # Get user team
        user_team = None
        if session.get('role') == 'team_owner':
            user_team = get_user_team(cursor, session['user_id'], auction_id)
        
        # Get available players
        cursor.execute("""
            SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, 
                   ap.sold_price, ap.skip_reason, ap.skip_notes
            FROM players p
            JOIN auction_players ap ON p.id = ap.player_id
            WHERE ap.auction_id = %s AND ap.status IN ('available', 'unsold')
            ORDER BY RAND()
        """, (auction_id,))
        players = cursor.fetchall()
        
        # Current player logic
        current_player = None
        current_bid = 0
        skip_votes = []
        total_teams = len(session_teams)
        all_skipped = False
        has_bids = False
        
        if auction.get('current_player_id'):
            cursor.execute("""
                SELECT p.*, ap.id as auction_player_id, ap.base_price, ap.status, 
                       ap.skip_reason, ap.skip_notes
                FROM auction_players ap
                JOIN players p ON ap.player_id = p.id
                WHERE ap.id = %s
            """, (auction['current_player_id'],))
            current_player = cursor.fetchone()
            current_bid = float(auction.get('current_bid') or 0)
            
            # Check bids
            cursor.execute("""
                SELECT COUNT(*) as bid_count FROM bids 
                WHERE auction_id = %s AND auction_player_id = %s
            """, (auction_id, auction['current_player_id']))
            bid_result = cursor.fetchone()
            has_bids = bid_result['bid_count'] > 0 if bid_result else False
            
            # Skip votes
            cursor.execute("""
                SELECT ps.*, t.team_name, u.username as skipped_by_name
                FROM player_skips ps
                JOIN teams t ON ps.team_id = t.id
                JOIN users u ON ps.skipped_by = u.id
                WHERE ps.auction_id = %s AND ps.auction_player_id = %s
                ORDER BY ps.skipped_at DESC
            """, (auction_id, auction['current_player_id']))
            skip_votes = cursor.fetchall()
            
            all_skipped = len(skip_votes) >= total_teams and total_teams > 0
        
        # Build auction dict for template
        auction_dict = {
            'id': auction_id,
            'league_name': auction['league_name'],
            'status': auction['status'],
            'current_player_id': auction.get('current_player_id'),
            'current_bid': auction.get('current_bid'),
            'current_bidder_id': auction.get('current_bidder_id'),
            'squad_size': auction.get('squad_size', 18),
            'purse_limit': auction.get('purse_limit', 100),
            'overseas_limit': auction.get('overseas_limit', 8)
        }
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/auction.html', 
        auction=auction_dict,
        auction_session=auction_session,
        session_id=active_session_id,
        players=players, 
        teams=session_teams,
        all_teams=all_teams,
        user_team=user_team,
        sessions_count=1,
        current_player=current_player,
        current_bid=current_bid,
        has_bids=has_bids,
        skip_votes=skip_votes,
        total_teams=total_teams,
        all_skipped=all_skipped
    )

# ==================== AUCTION ACTIONS ====================

@bp.route('/auction/pause', methods=['POST'])
def pause_auction():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("UPDATE auctions SET status = 'paused' WHERE status = 'live'")
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    clear_cache('auction:status')
    return jsonify({'status': 'paused', 'success': True})


@bp.route('/auction/resume', methods=['POST'])
def resume_auction():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute("UPDATE auctions SET status = 'live' WHERE status = 'paused'")
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    clear_cache('auction:status')
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
    
    try:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction or not auction.get('current_bidder_id') or float(auction.get('current_bid') or 0) <= 0:
            return jsonify({'error': 'No active bid - cannot sell. Player goes unsold.'}), 400
        
        team_id = auction['current_bidder_id']
        sold_price = auction['current_bid']
        
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
    finally:
        cursor.close()
        db.close()
    
    # Clear caches after sell
    clear_cache(f'auction:{auction_id}')
    clear_cache('auction:status')
    
    return jsonify({
        'success': True, 
        'sold_to': team_id, 
        'price': float(sold_price),
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
    
    try:
        cursor.execute("SELECT * FROM auction_players WHERE id = %s AND auction_id = %s", (auction_player_id, auction_id))
        ap = cursor.fetchone()
        
        if not ap:
            return jsonify({'error': 'Player not in auction'}), 404
        
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
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:{auction_id}')
    clear_cache('auction:status')
    
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
    
    try:
        cursor.execute("SELECT * FROM auction_players WHERE id = %s AND auction_id = %s", (auction_player_id, auction_id))
        ap = cursor.fetchone()
        
        if not ap or ap['status'] != 'sold':
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
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:{auction_id}')
    clear_cache('auction:status')
    
    return jsonify({'success': True})


@bp.route('/auction/rebid', methods=['POST'])
def rebid_player():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    
    if not auction_id or not auction_player_id:
        return jsonify({'error': 'Missing auction_id or auction_player_id'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if not auction:
            return jsonify({'error': 'Auction not found'}), 404
        
        cursor.execute("""
            SELECT ap.*, p.player_name, p.category, p.overseas
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            WHERE ap.id = %s AND ap.auction_id = %s
        """, (auction_player_id, auction_id))
        player = cursor.fetchone()
        
        if not player:
            return jsonify({'error': 'Player not found in this auction'}), 404
        
        if auction.get('current_player_id') != auction_player_id:
            return jsonify({'error': 'Player is not currently active in auction'}), 400
        
        cursor.execute(
            "DELETE FROM bids WHERE auction_id = %s AND auction_player_id = %s",
            (auction_id, auction_player_id)
        )
        
        cursor.execute(
            "DELETE FROM player_skips WHERE auction_id = %s AND auction_player_id = %s",
            (auction_id, auction_player_id)
        )
        
        cursor.execute("""
            UPDATE auctions 
            SET current_bid = %s,
                current_bidder_id = NULL
            WHERE id = %s
        """, (player['base_price'], auction_id))
        
        cursor.execute(
            "UPDATE hidden_max_bids SET is_active = FALSE WHERE auction_player_id = %s",
            (auction_player_id,)
        )
        
        cursor.execute(
            "UPDATE purse_reservations SET status = 'released' WHERE auction_player_id = %s",
            (auction_player_id,)
        )
        
        cursor.execute("""
            UPDATE teams t
            SET reserved = GREATEST(0, t.reserved - COALESCE((((
                SELECT reserved_amount FROM purse_reservations 
                WHERE auction_player_id = %s AND team_id = t.id AND status = 'released'
            ), 0)))
            WHERE t.auction_id = %s
        """, (auction_player_id, auction_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:{auction_id}')
    clear_cache('auction:status')
    
    return jsonify({
        'success': True,
        'player_name': player['player_name'],
        'base_price': float(player['base_price']),
        'auction_player_id': auction_player_id,
        'message': 'Bidding reset to base price'
    })


@bp.route('/auction/deselect_player', methods=['POST'])
def deselect_player():
    if session.get('role') not in ['admin', 'auctioneer', 'owner']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    auction_player_id = data.get('auction_player_id')
    
    if not auction_id:
        return jsonify({'error': 'Missing auction_id'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if not auction:
            return jsonify({'error': 'Auction not found'}), 404
        
        if auction_player_id and auction.get('current_player_id') != auction_player_id:
            return jsonify({'error': 'Player mismatch'}), 400
        
        current_player_id = auction.get('current_player_id')
        
        cursor.execute("""
            UPDATE auctions 
            SET current_player_id = NULL, 
                current_bid = 0, 
                current_bidder_id = NULL
            WHERE id = %s
        """, (auction_id,))
        
        if current_player_id:
            cursor.execute(
                "DELETE FROM player_skips WHERE auction_id = %s AND auction_player_id = %s",
                (auction_id, current_player_id)
            )
            cursor.execute(
                "DELETE FROM bids WHERE auction_id = %s AND auction_player_id = %s",
                (auction_id, current_player_id)
            )
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:{auction_id}')
    clear_cache('auction:status')
    
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
        return jsonify({'error': 'Invalid IDs'}), 400
    
    if not auction_player_id:
        return jsonify({'error': 'Missing auction_player_id'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if not auction:
            return jsonify({'error': f'Auction {auction_id} not found'}), 404
        
        cursor.execute("""
            SELECT ap.*, p.player_name, p.category, p.overseas
            FROM auction_players ap
            JOIN players p ON ap.player_id = p.id
            WHERE ap.id = %s AND ap.auction_id = %s
        """, (auction_player_id, auction_id))
        player = cursor.fetchone()
        
        if not player:
            return jsonify({'error': 'Player not found in this auction'}), 404
        
        cursor.execute("DELETE FROM player_skips WHERE auction_id = %s AND auction_player_id = %s", (auction_id, auction_player_id))
        
        cursor.execute(
            "UPDATE auction_players SET skip_reason = NULL, skip_notes = NULL WHERE id = %s",
            (auction_player_id,)
        )
        
        cursor.execute("""
            UPDATE auctions 
            SET current_player_id = %s, current_bid = %s, current_bidder_id = NULL
            WHERE id = %s
        """, (auction_player_id, player['base_price'], auction_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:{auction_id}')
    clear_cache('auction:status')
    
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
    team_id = request.args.get('team_id', type=int)
    session_id = request.args.get('session_id') or session.get('active_session_id')
    
    # Cache auction status for 5 seconds
    cache_key = f'auction:status:{auction_id or "active"}:{session_id or "no_session"}'
    
    def fetch_status():
        db = get_db()
        cursor = db.cursor(dictionary=True)
        
        try:
            if auction_id:
                cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
            else:
                cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
            
            auction = cursor.fetchone()
            
            if not auction:
                return {'status': 'none'}
            
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
                'last_sold_team_id': auction.get('last_sold_team_id'),
                'last_sold_player_name': auction.get('last_sold_player_name'),
                'last_sold_price': float(auction.get('last_sold_price') or 0),
                'last_sold_auction_player_id': auction.get('last_sold_auction_player_id'),
                'last_sold_at': str(auction.get('last_sold_at')) if auction.get('last_sold_at') else None,
                'sold_to_team_id': None,
                'sold_player_name': None,
                'sold_price': 0,
                'sold_auction_player_id': None,
                'willing_price_set': False,
                'session_id': session_id
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
            
            # Check willing price for team owner notifications
            if team_id and auction.get('last_sold_team_id') == team_id:
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
            
            return result
            
        finally:
            cursor.close()
            db.close()
    
    # Use 5 second cache for status polling
    result = get_cached(cache_key, fetch_status, ttl_seconds=5)
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
    
    try:
        cursor.execute("""
            SELECT * FROM player_skips 
            WHERE auction_id = %s AND auction_player_id = %s AND team_id = %s
        """, (auction_id, auction_player_id, team_id))
        existing_skip = cursor.fetchone()
        if existing_skip:
            return jsonify({'error': 'You skipped this player. Cannot bid.'}), 400
        
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        cursor.execute("""
            SELECT team_id FROM bids 
            WHERE auction_id = %s AND auction_player_id = %s 
            ORDER BY bid_amount DESC, created_at DESC LIMIT 1
        """, (auction_id, auction_player_id))
        last_bid = cursor.fetchone()
        if last_bid and last_bid['team_id'] == team_id:
            return jsonify({'error': 'You are already the highest bidder.'}), 400
        
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        
        if auction['status'] != 'live':
            return jsonify({'error': 'Auction not live'}), 400
        
        cursor.execute("SELECT base_price FROM auction_players WHERE id = %s", (auction_player_id,))
        ap_row = cursor.fetchone()
        base_price = float(ap_row['base_price']) if ap_row else float(auction.get('base_price') or 2.0)
        
        cursor.execute("""
            SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
            FROM bids 
            WHERE auction_id = %s AND auction_player_id = %s
        """, (auction_id, auction_player_id))
        bid_info = cursor.fetchone()
        has_bids = bid_info['bid_count'] > 0
        highest_bid = float(bid_info['highest_bid']) if bid_info['highest_bid'] else 0
        
        if not has_bids:
            if amount < base_price:
                return jsonify({'error': f'Initial bid must be at least base price ₹{base_price:.2f}Cr'}), 400
        else:
            current_bid = highest_bid
            if amount <= current_bid:
                return jsonify({'error': f'Bid must be higher than current bid ₹{current_bid:.2f}Cr'}), 400
            
            min_increment = get_min_bid_increment(current_bid)
            if amount < current_bid + min_increment:
                return jsonify({'error': f'Bid must be at least ₹{min_increment:.2f}Cr higher than current bid'}), 400
        
        cursor.execute(
            "SELECT max_bid FROM hidden_max_bids WHERE auction_player_id = %s AND team_id = %s AND is_active = TRUE",
            (auction_player_id, team_id)
        )
        hidden = cursor.fetchone()
        hidden_amount = float(hidden['max_bid']) if hidden else 0
        
        available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
        
        if amount > available + hidden_amount:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available + hidden_amount:.2f}Cr'}), 400
        
        cursor.execute(
            "INSERT INTO bids (auction_id, auction_player_id, team_id, bid_amount, session_id) VALUES (%s, %s, %s, %s, %s)",
            (auction_id, auction_player_id, team_id, amount, session.get('active_session_id'))
        )
        
        cursor.execute(
            "UPDATE auctions SET current_bid = %s, current_bidder_id = %s, current_player_id = %s WHERE id = %s",
            (amount, team_id, auction_player_id, auction_id)
        )
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    # Clear cache after bid
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
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
    
    try:
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
        
        if max_amount > available:
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
    finally:
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
    
    try:
        cursor.execute("""
            SELECT h.*, t.team_name, t.purse_limit, t.spent, t.reserved 
            FROM hidden_max_bids h 
            JOIN teams t ON h.team_id = t.id 
            WHERE h.auction_player_id = %s AND h.is_active = TRUE AND h.max_bid > %s
            ORDER BY h.max_bid DESC
        """, (auction_player_id, current_bid))
        
        hidden_bids = cursor.fetchall()
        
        if not hidden_bids:
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
            return jsonify({'auto_bid': False, 'reason': 'Insufficient funds'})
        
        cursor.execute(
            "INSERT INTO bids (auction_id, auction_player_id, team_id, bid_amount, session_id) VALUES (%s, %s, %s, %s, %s)",
            (auction_id, auction_player_id, winner['team_id'], next_bid, session.get('active_session_id'))
        )
        
        cursor.execute(
            "UPDATE auctions SET current_bid = %s, current_bidder_id = %s, current_player_id = %s WHERE id = %s",
            (next_bid, winner['team_id'], auction_player_id, auction_id)
        )
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'auto_bid': True,
        'team': winner['team_name'],
        'amount': next_bid
    })