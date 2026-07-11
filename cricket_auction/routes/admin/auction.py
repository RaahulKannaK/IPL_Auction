from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json
import time
import logging

import time

start = time.time()
logger = logging.getLogger(__name__)

bp = Blueprint('admin_auction', __name__, url_prefix='/admin')

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


# ==================== MAIN AUCTION ROOM ====================

@bp.route('/auction')
def auction_room():
    """Main auction room — REQUIRES active session"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') not in ['admin', 'auctioneer', 'team_owner']:
        flash('Unauthorized')
        return redirect('/')
    
    url_session_id = request.args.get('session', type=int)
    active_session_id = url_session_id or session.get('active_session_id')
    active_auction_id = session.get('active_auction_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        if active_auction_id:
            cursor.execute("SELECT * FROM auctions WHERE id = %s", (active_auction_id,))
        else:
            cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused') ORDER BY id DESC LIMIT 1")
        auction = cursor.fetchone()
        
        if not auction:
            flash('No active auction found')
            return redirect('/admin/')
        
        auction_id = auction['id']
        session['active_auction_id'] = auction_id
        
        if not active_session_id:
            cursor.execute("""
                SELECT * FROM auction_sessions 
                WHERE auction_id = %s AND status IN ('active', 'paused')
                ORDER BY created_at DESC LIMIT 1
            """, (auction_id,))
            existing_session = cursor.fetchone()
            
            if not existing_session:
                if session.get('role') in ['admin', 'auctioneer']:
                    flash('No session created yet. Create a session first.')
                    return redirect(f'/admin/sessions?auction={auction_id}')
                else:
                    flash('No active session available.')
                    return redirect('/admin/')
            
            if session.get('role') == 'team_owner':
                session['active_session_id'] = existing_session['id']
                active_session_id = existing_session['id']
            else:
                flash('Select a session to enter.')
                return redirect(f'/admin/sessions?auction={auction_id}')
        
        session['active_session_id'] = active_session_id
        
        cursor.execute("""
            SELECT s.*, a.league_name, a.status as auction_status, a.squad_size, 
                   a.purse_limit, a.overseas_limit
            FROM auction_sessions s
            JOIN auctions a ON s.auction_id = a.id
            WHERE s.id = %s
        """, (active_session_id,))
        auction_session = cursor.fetchone()
        
        # DEBUG
        if auction_session:
            print(f"[ADMIN-ROOM] session={active_session_id}, current_player_id={auction_session.get('current_player_id')}, current_bid={auction_session.get('current_bid')}, current_bidder_id={auction_session.get('current_bidder_id')}")
        else:
            print(f"[ADMIN-ROOM] session={active_session_id} NOT FOUND")
        
        if not auction_session:
            session.pop('active_session_id', None)
            flash('Session expired. Please select again.')
            return redirect(f'/admin/sessions?auction={auction_id}')
        
        # Get session teams
        session_team_ids = []
        if auction_session.get('team_ids'):
            try:
                session_team_ids = json.loads(auction_session['team_ids']) if isinstance(auction_session['team_ids'], str) else auction_session['team_ids']
            except:
                session_team_ids = []
        
        session_teams = []
        if session_team_ids:
            format_ids = ','.join(['%s'] * len(session_team_ids))
            cursor.execute(f"""
                SELECT t.*, 
                       (t.purse_limit - COALESCE(t.spent, 0) - COALESCE(t.reserved, 0)) as available_purse
                FROM teams t
                WHERE t.id IN ({format_ids})
            """, tuple(session_team_ids))
            session_teams = cursor.fetchall()
        
        # Get user team
        user_team = None
        if session.get('role') == 'team_owner':
            cursor.execute("SELECT * FROM teams WHERE owner_id = %s AND auction_id = %s", (session['user_id'], auction_id))
            user_team = cursor.fetchone()
            if user_team and user_team['id'] not in session_team_ids:
                flash('Your team is not part of this session')
                return redirect('/admin/')
        
        # Get session players (available + unsold + in_auction)
        cursor.execute("""
            SELECT sp.id as session_player_id, sp.base_price, sp.status, 
                   sp.sold_price, sp.sold_team_id, sp.skip_reason, sp.skip_notes,
                   p.id as player_id, p.player_name, p.category, p.overseas
            FROM session_players sp
            JOIN players p ON sp.player_id = p.id
            WHERE sp.session_id = %s AND sp.status IN ('available', 'unsold', 'in_auction')
            ORDER BY 
                CASE sp.status 
                    WHEN 'in_auction' THEN 1 
                    WHEN 'available' THEN 2 
                    WHEN 'unsold' THEN 3 
                END,
                p.player_name
        """, (active_session_id,))
        players = cursor.fetchall()
        
        # Current player from session state
        current_player = None
        current_bid = 0
        current_bidder = None
        skip_votes = []
        total_teams = len(session_teams)
        all_skipped = False
        has_bids = False
        
        if auction_session.get('current_player_id'):
            cursor.execute("""
                SELECT sp.*, p.player_name, p.category, p.overseas, p.id as player_id
                FROM session_players sp
                JOIN players p ON sp.player_id = p.id
                WHERE sp.id = %s AND sp.session_id = %s
            """, (auction_session['current_player_id'], active_session_id))
            current_player = cursor.fetchone()
            current_bid = float(auction_session.get('current_bid') or 0)
            
            cursor.execute("""
                SELECT COUNT(*) as bid_count FROM session_bids 
                WHERE session_id = %s AND session_player_id = %s
            """, (active_session_id, auction_session['current_player_id']))
            bid_result = cursor.fetchone()
            has_bids = bid_result['bid_count'] > 0 if bid_result else False
            
            # Current bidder name
            current_bidder = None
            if auction_session.get('current_bidder_id'):
                cursor.execute("""
                    SELECT team_name FROM teams WHERE id = %s
                """, (auction_session['current_bidder_id'],))
                bidder_row = cursor.fetchone()
                if bidder_row:
                    current_bidder = bidder_row['team_name']
            
            cursor.execute("""
                SELECT ss.*, t.team_name, u.username as skipped_by_name
                FROM session_skips ss
                JOIN teams t ON ss.team_id = t.id
                JOIN users u ON ss.skipped_by = u.id
                WHERE ss.session_id = %s AND ss.session_player_id = %s
                ORDER BY ss.skipped_at DESC
            """, (active_session_id, auction_session['current_player_id']))
            skip_votes = cursor.fetchall()
            
            unique_skip_teams = len(set(v['team_id'] for v in skip_votes))
            all_skipped = unique_skip_teams >= total_teams and total_teams > 0
        
        # Get all sessions for navigation
        cursor.execute("""
            SELECT s.*, 
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id) as player_count
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY s.created_at ASC
        """, (auction_id,))
        all_sessions = cursor.fetchall()
        
        for sess in all_sessions:
            if sess.get('team_ids'):
                try:
                    sess['team_ids_list'] = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                except:
                    sess['team_ids_list'] = []
            else:
                sess['team_ids_list'] = []
            sess['team_count'] = len(sess['team_ids_list'])
        
        auction_dict = {
            'id': auction_id,
            'league_name': auction['league_name'],
            'status': auction['status'],
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
        current_session=auction_session,
        players=players, 
        teams=session_teams,
        all_teams=session_teams,
        user_team=user_team,
        sessions_count=len(all_sessions),
        all_sessions=all_sessions,
        current_player=current_player,
        current_bid=current_bid,
        current_bidder=current_bidder,
        has_bids=has_bids,
        skip_votes=skip_votes,
        total_teams=total_teams,
        all_skipped=all_skipped
    )


# ==================== SELECT PLAYER ====================
# ==================== SELECT PLAYER (WITH PROACTIVE BOT TRIGGER) ====================

@bp.route('/auction/select_player', methods=['POST'])
def select_player():
    """Select a player for bidding - triggers proactive bot bids immediately"""
    try:
        if session.get('role') not in ['admin', 'auctioneer']:
            return jsonify({'error': 'Unauthorized'}), 403
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data received'}), 400
            
        auction_id = data.get('auction_id')
        session_player_id = data.get('session_player_id')
        
        if not auction_id or not session_player_id:
            return jsonify({'error': 'Missing auction_id or session_player_id'}), 400
        
        active_session_id = session.get('active_session_id') or data.get('session_id')
        if not active_session_id:
            return jsonify({'error': 'No active session'}), 400
        
        try:
            session_player_id = int(session_player_id)
            auction_id = int(auction_id)
            active_session_id = int(active_session_id)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid IDs'}), 400
        
        db = get_db()
        cursor = db.cursor(dictionary=True, buffered=True)
        
        try:
            # Verify session exists
            cursor.execute("SELECT id FROM auction_sessions WHERE id = %s", (active_session_id,))
            if not cursor.fetchone():
                return jsonify({'error': f'Session {active_session_id} not found'}), 404
            
            # Get player with player_id for bot lookup
            cursor.execute("""
                SELECT sp.*, p.player_name, p.category, p.overseas, p.id as master_player_id, sp.base_price as player_base_price
                FROM session_players sp
                JOIN players p ON sp.player_id = p.id
                WHERE sp.id = %s AND sp.session_id = %s
            """, (session_player_id, active_session_id))
            player = cursor.fetchone()
            
            if not player:
                return jsonify({'error': 'Player not found in this session'}), 404
            
            master_player_id = player['master_player_id']
            
            # Clear previous state
            cursor.execute("DELETE FROM session_skips WHERE session_id = %s", (active_session_id,))
            cursor.execute("DELETE FROM session_bids WHERE session_id = %s AND session_player_id = %s", (active_session_id, session_player_id))
            
            # Update player status
            # Reset any existing in_auction player in this session
            cursor.execute("""
                UPDATE session_players
                SET status = 'available'
                WHERE session_id = %s
                AND status = 'in_auction'
                AND id != %s
            """, (active_session_id, session_player_id))

            # Set selected player as in_auction
            cursor.execute("""
                UPDATE session_players
                SET skip_reason = NULL,
                    skip_notes = NULL,
                    status = 'in_auction'
                WHERE id = %s
            """, (session_player_id,))
            
            # Update auction session
            cursor.execute("""
                UPDATE auction_sessions
                SET current_player_id = %s,
                    current_bid = 0,
                    current_bidder_id = NULL
                WHERE id = %s
            """, (session_player_id, active_session_id))
            
            db.commit()
            
            # Verify it stuck
            cursor.execute("SELECT current_player_id, current_bid, current_bidder_id FROM auction_sessions WHERE id = %s", (active_session_id,))
            verify = cursor.fetchone()
            print(f"[SELECT-PLAYER] session={active_session_id}, player={session_player_id}, verify={verify}")
            
            # ============================================
            # PROACTIVE BOT TRIGGER
            # Find teams with willing price for this player and auto-bid
            # ============================================
            bot_bid_placed = False
            bot_team_name = None
            bot_amount = 0
            
            cursor.execute("""
                SELECT 
                    stp.team_id,
                    stp.purchase_price as original_price,
                    stp.willing_price as max_bid,
                    t.team_name,
                    t.purse_limit,
                    t.spent,
                    t.reserved
                FROM session_team_players stp
                JOIN session_players sp ON sp.id = stp.session_player_id
                JOIN teams t ON t.id = stp.team_id
                WHERE sp.player_id = %s
                  AND stp.willing_price IS NOT NULL
                  AND stp.willing_price > stp.purchase_price
                ORDER BY stp.willing_price DESC
            """, (master_player_id,))
            proactive_bots = cursor.fetchall()
            
            for bot in proactive_bots:
                original_price = float(bot['original_price'])
                max_willing = float(bot['max_bid'])
                team_id = bot['team_id']
                
                # Check funds: available + reserve must cover original price
                available = float(bot['purse_limit']) - float(bot['spent'] or 0) - float(bot['reserved'] or 0)
                reserve_amount = (max_willing - original_price) * 0.20
                
                if original_price > available + reserve_amount:
                    print(f"[PROACTIVE-BOT] {bot['team_name']} insufficient funds for ₹{original_price}Cr")
                    continue
                
                # Place proactive bot bid at original purchase price
                cursor.execute("""
                    INSERT INTO session_bids (session_id, session_player_id, team_id, bid_amount) 
                    VALUES (%s, %s, %s, %s)
                """, (active_session_id, session_player_id, team_id, original_price))
                
                cursor.execute("""
                    UPDATE auction_sessions 
                    SET current_bid = %s, current_bidder_id = %s
                    WHERE id = %s
                """, (original_price, team_id, active_session_id))
                
                db.commit()
                
                bot_bid_placed = True
                bot_team_name = bot['team_name']
                bot_amount = original_price
                
                print(f"[PROACTIVE-BOT] {bot['team_name']} auto-bid ₹{original_price}Cr on {player['player_name']}")
                break  # Only the highest willing bot bids first (others wait for counter)
            
        finally:
            cursor.close()
            db.close()
        
        clear_cache(f'auction:status:{auction_id}')
        clear_cache('auction:status:active')
        
        return jsonify({
            'success': True,
            'player_name': player['player_name'],
            'category': player['category'],
            'base_price': float(player['base_price']),
            'session_player_id': session_player_id,
            'overseas': player.get('overseas', False),
            'debug_session': active_session_id,
            'debug_db_player_id': verify['current_player_id'] if verify else None,
            'check_auto': True,
            'bot_bid_placed': bot_bid_placed,
            'bot_team': bot_team_name,
            'bot_amount': float(bot_amount) if bot_bid_placed else 0
        })
        
    except Exception as e:
        import traceback
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        print(f"SELECT_PLAYER ERROR: {error_msg}")
        return jsonify({'error': f'Server error: {str(e)}', 'traceback': error_msg}), 500
    

@bp.route('/auction/bid', methods=['POST'])
def place_bid():
    """Place bid — admin/auctioneer can bid on behalf of any team"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = int(data.get('team_id'))
    amount = float(data.get('amount', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    user_role = session.get('role')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # FIX: Clear any lingering transaction and set isolation level
        try:
            cursor.execute("ROLLBACK")
        except:
            pass
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        
        cursor.execute("START TRANSACTION")
        
        # Lock session
        cursor.execute("SELECT team_ids, current_bid, current_bidder_id, current_player_id FROM auction_sessions WHERE id = %s FOR UPDATE", (active_session_id,))
        sess = cursor.fetchone()
        if not sess:
            cursor.execute("ROLLBACK")
            return jsonify({'error': 'Session not found'}), 404
        
        # Verify the player being bid on is the CURRENT player
        if sess.get('current_player_id') != session_player_id:
            cursor.execute("ROLLBACK")
            return jsonify({'error': 'This player is not currently in auction'}), 400
        
        session_team_ids = []
        if sess['team_ids']:
            try:
                session_team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                pass
        
        if team_id not in session_team_ids:
            cursor.execute("ROLLBACK")
            return jsonify({'error': 'Team not in this session'}), 403
        
        # Check skip
        cursor.execute("""
            SELECT * FROM session_skips 
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        existing_skip = cursor.fetchone()
        if existing_skip:
            cursor.execute("ROLLBACK")
            return jsonify({'error': 'Team skipped this player. Cannot bid.'}), 400
        
        cursor.execute("SELECT * FROM teams WHERE id = %s FOR UPDATE", (team_id,))
        team = cursor.fetchone()
        if not team:
            cursor.execute("ROLLBACK")
            return jsonify({'error': 'Team not found'}), 404
        
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if auction['status'] != 'live':
            cursor.execute("ROLLBACK")
            return jsonify({'error': 'Auction not live'}), 400
        
        # Get base price
        cursor.execute("SELECT base_price FROM session_players WHERE id = %s", (session_player_id,))
        sp_row = cursor.fetchone()
        base_price = float(sp_row['base_price']) if sp_row else 2.0
        
        # Check highest bid with lock
        cursor.execute("""
            SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
            FROM session_bids 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        bid_info = cursor.fetchone()
        has_bids = bid_info['bid_count'] > 0
        highest_bid = float(bid_info['highest_bid']) if bid_info['highest_bid'] else 0
        
        # RACE FIX: Use higher of table max and session current_bid
        actual_current_bid = float(sess.get('current_bid') or 0)
        true_highest_bid = max(highest_bid, actual_current_bid)
        
        if not has_bids and actual_current_bid <= 0:
            if amount < base_price:
                cursor.execute("ROLLBACK")
                return jsonify({'error': f'First bid must be at least base price ₹{base_price:.2f}Cr'}), 400
        else:
            if amount <= true_highest_bid:
                cursor.execute("ROLLBACK")
                return jsonify({'error': f'Bid must be higher than current bid ₹{true_highest_bid:.2f}Cr'}), 400
            
            min_increment = get_min_bid_increment(true_highest_bid)
            if amount < true_highest_bid + min_increment:
                cursor.execute("ROLLBACK")
                return jsonify({'error': f'Bid must be at least ₹{min_increment:.2f}Cr higher than ₹{true_highest_bid:.2f}Cr'}), 400
        
        # Check if same team is already highest bidder
        if sess.get('current_bidder_id') == team_id:
            cursor.execute("ROLLBACK")
            return jsonify({'error': 'Already the highest bidder.'}), 400
        
        # Check funds
        available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
        if amount > available:
            cursor.execute("ROLLBACK")
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        # Atomic insert and update
        cursor.execute("""
            INSERT INTO session_bids (session_id, session_player_id, team_id, bid_amount) 
            VALUES (%s, %s, %s, %s)
        """, (active_session_id, session_player_id, team_id, amount))
        
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_bid = %s, current_bidder_id = %s
            WHERE id = %s
        """, (amount, team_id, active_session_id))
        
        db.commit()
        
        # VERIFY
        cursor.execute("SELECT current_bid, current_bidder_id FROM auction_sessions WHERE id = %s", (active_session_id,))
        verify = cursor.fetchone()
        print(f"[BID-VERIFY] session={active_session_id}, player={session_player_id}, team={team_id}, amount={amount}, verify_bid={verify['current_bid']}, verify_bidder={verify['current_bidder_id']}")
        
    except Exception as e:
        cursor.execute("ROLLBACK")
        raise e
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'success': True, 
        'current_bid': amount, 
        'bidder': team['team_name'],
        'check_auto': True
    })


# ==================== HELPER: Check if willing price should be asked ====================

def should_ask_willing_price(cursor, session_id):
    """
    Ask willing price ONLY if:
    1. This player has NEVER been bought before in ANY session (first time)
    2. AND not all teams are present in this session (re-auction risk exists)
    If all teams are present → no re-auction risk → DON'T ask.
    If player was sold before → DON'T ask again.
    """
    # Get the current session + auction_id
    cursor.execute("SELECT current_player_id, auction_id, team_ids FROM auction_sessions WHERE id = %s", (session_id,))
    sess = cursor.fetchone()
    if not sess or not sess.get('current_player_id'):
        return False
    
    session_player_id = sess['current_player_id']
    
    # Get player_id
    cursor.execute("SELECT player_id FROM session_players WHERE id = %s", (session_player_id,))
    sp_row = cursor.fetchone()
    if not sp_row:
        return False
    
    player_id = sp_row['player_id']
    
    # ============================================
    # CHECK 1: If ALL teams present → NO re-auction risk → NO popup
    # ============================================
    cursor.execute("SELECT COUNT(*) as total FROM teams WHERE auction_id = %s", (sess['auction_id'],))
    total_teams = cursor.fetchone()['total']
    
    session_team_ids = []
    if sess['team_ids']:
        try:
            session_team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
        except:
            session_team_ids = []
    
    # If all teams present in this session → no re-auction risk → no popup
    if len(session_team_ids) >= total_teams:
        print(f"[WILLING-CHECK] All {total_teams} teams present. No re-auction risk. Skip popup.")
        return False
    
    # ============================================
    # CHECK 2: If player was sold before → DON'T ask again
    # ============================================
    cursor.execute("""
        SELECT COUNT(*) as sale_count
        FROM session_team_players stp
        JOIN session_players sp ON sp.id = stp.session_player_id
        WHERE sp.player_id = %s
          AND stp.session_player_id != %s
    """, (player_id, session_player_id))
    
    result = cursor.fetchone()
    sale_count = result['sale_count'] if result else 0
    
    # If player was sold before → DON'T ask willing price (already asked in first session)
    # If first time ever → ASK willing price
    if sale_count > 0:
        print(f"[WILLING-CHECK] Player sold before in {sale_count} session(s). Skip popup.")
        return False
    
    # First time + not all teams present → re-auction risk → ASK willing price
    print(f"[WILLING-CHECK] First sale, {len(session_team_ids)}/{total_teams} teams present. Show popup.")
    return True


# ==================== SELL PLAYER (UPDATED WITH 10/10 LOGIC) ====================
# ==================== SELL PLAYER (WITH BOT LOGIC + NO DOUBLE WILLING PRICE) ====================

# ==================== SELL PLAYER (WITH BOT LOGIC + NO DOUBLE WILLING PRICE) ====================

@bp.route('/auction/sell', methods=['POST'])
def sell_player():
    """Sell player to current highest bidder — with bot logic and no double willing-price asks"""
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (active_session_id,))
        auction_session = cursor.fetchone()
        
        if not auction_session or not auction_session.get('current_bidder_id') or float(auction_session.get('current_bid') or 0) <= 0:
            return jsonify({'error': 'No active bid - cannot sell.'}), 400
        
        if auction_session['current_player_id'] != session_player_id:
            return jsonify({'error': 'Player mismatch'}), 400
        
        winning_team_id = auction_session['current_bidder_id']
        sold_price = float(auction_session['current_bid'])
        
        cursor.execute("""
            SELECT sp.*, p.player_name, p.category, p.overseas
            FROM session_players sp
            JOIN players p ON sp.player_id = p.id
            WHERE sp.id = %s
        """, (session_player_id,))
        player_info = cursor.fetchone()
        player_name = player_info['player_name'] if player_info else 'Unknown'
        player_id = player_info['player_id'] if player_info else None
        
        if not player_info:
            return jsonify({'error': 'Player not in session'}), 404
        
        # ============================================
        # CHECK IF WINNER ALREADY OWNS THIS PLAYER (BOT WIN/LOSS SCENARIO)
        # ============================================
        cursor.execute("""
            SELECT stp.id, stp.purchase_price, stp.willing_price, stp.team_id
            FROM session_team_players stp
            JOIN session_players sp ON sp.id = stp.session_player_id
            WHERE stp.team_id = %s AND sp.player_id = %s
            LIMIT 1
        """, (winning_team_id, player_id))
        existing_ownership = cursor.fetchone()
        
        need_willing = False
        
        if existing_ownership:
            # ============================================
            # RE-BUY SCENARIO: Team already bought this player in PREVIOUS session
            # Reserve is RELEASED — willing price protection was for first session only
            # ============================================
            old_price = float(existing_ownership['purchase_price'])
            old_willing = float(existing_ownership['willing_price']) if existing_ownership['willing_price'] else None
            
            # Calculate price difference
            price_diff = sold_price - old_price
            
            # Get old reserve (20% of difference) — to be released
            old_reserve = (old_willing - old_price) * 0.20 if old_willing else 0
            
            # Update existing record: new price, new session, CLEAR willing_price (no reserve in re-buy)
            cursor.execute("""
                UPDATE session_team_players
                SET purchase_price = %s,
                    session_player_id = %s,
                    willing_price = NULL
                WHERE id = %s
            """, (sold_price, session_player_id, existing_ownership['id']))
            
            # Update team finances: 
            # - Add price difference to spent
            # - RELEASE old reserve completely (re-buy = no reserve needed)
            cursor.execute("""
                UPDATE teams
                SET spent = spent + %s,
                    reserved = GREATEST(0, reserved - %s)
                WHERE id = %s
            """, (price_diff, old_reserve, winning_team_id))
            
            # Clear any old reservations
            cursor.execute("""
                DELETE FROM session_purse_reservations
                WHERE session_player_id = %s AND team_id = %s
            """, (session_player_id, winning_team_id))
            
            # Don't ask willing price again — already asked in first session
            need_willing = False
            
            print(f"[SELL-REBUY] Team {winning_team_id} re-bought {player_name}. Old: ₹{old_price}Cr, New: ₹{sold_price}Cr, Diff: ₹{price_diff}Cr, Reserve released: ₹{old_reserve}Cr")  

        else:
            # ============================================
            # NORMAL SCENARIO: First time buyer
            # ============================================
            
            # Update session_player status
            cursor.execute("""
                UPDATE session_players 
                SET status = 'sold', sold_team_id = %s, sold_price = %s 
                WHERE id = %s
            """, (winning_team_id, sold_price, session_player_id))
            
            # Handle reservations (from hidden bids)
            cursor.execute("""
                SELECT reserved_amount
                FROM session_purse_reservations
                WHERE session_player_id=%s
                AND team_id=%s
                AND status='active'
            """, (session_player_id, winning_team_id))
            reservation = cursor.fetchone()
            reserved_amount = float(reservation['reserved_amount']) if reservation else 0

            cursor.execute("""
                UPDATE teams
                SET spent = spent + %s,
                    reserved = GREATEST(0, reserved - %s)
                WHERE id = %s
            """, (sold_price, reserved_amount, winning_team_id))

            cursor.execute("""
                UPDATE session_purse_reservations
                SET status='used'
                WHERE session_player_id=%s
                AND team_id=%s
            """, (session_player_id, winning_team_id))
            
            # Add to session_team_players
            cursor.execute("""
                INSERT INTO session_team_players (team_id, session_player_id, purchase_price) 
                VALUES (%s, %s, %s)
            """, (winning_team_id, session_player_id, sold_price))
            
            # ============================================
            # WILLING PRICE LOGIC (NO DOUBLE ASK)
            # ============================================
            
            # Check if this team already has willing price for this player (from any session)
            cursor.execute("""
                SELECT stp.willing_price
                FROM session_team_players stp
                JOIN session_players sp ON sp.id = stp.session_player_id
                WHERE stp.team_id = %s AND sp.player_id = %s AND stp.willing_price IS NOT NULL
                LIMIT 1
            """, (winning_team_id, player_id))
            has_existing_willing = cursor.fetchone()
            
            if has_existing_willing:
                # Already has willing price — copy to new record, don't ask again
                cursor.execute("""
                    UPDATE session_team_players
                    SET willing_price = %s
                    WHERE team_id = %s AND session_player_id = %s
                """, (has_existing_willing['willing_price'], winning_team_id, session_player_id))
                need_willing = False
                print(f"[SELL] Team {winning_team_id} already has willing price for {player_name}. Skipping popup.")
            else:
                # First time buyer for this team, no existing willing price
                need_willing = should_ask_willing_price(cursor, active_session_id)
                
                if need_willing:
                    # Ask willing price via popup
                    owner_user_id = None
                    try:
                        cursor.execute("SELECT owner_id FROM teams WHERE id = %s", (winning_team_id,))
                        owner_row = cursor.fetchone()
                        owner_user_id = owner_row['owner_id'] if owner_row else None
                    except Exception as e:
                        print(f"[SELL] Error getting owner_id: {e}")
                    
                    if player_id and owner_user_id:
                        try:
                            cursor.execute("""
                                INSERT INTO pending_willing_price 
                                (user_id, team_id, player_id, session_player_id, player_name, purchase_price, popup_shown)
                                VALUES (%s, %s, %s, %s, %s, %s, 0)
                            """, (owner_user_id, winning_team_id, player_id, session_player_id, player_name, sold_price))
                            print(f"[SELL] Pending willing price popup queued for user={owner_user_id}, player={player_name}")
                        except Exception as e:
                            print(f"[SELL] Error inserting pending_willing_price: {e}")
                    else:
                        print(f"[SELL] Skipping popup: player_id={player_id}, owner_user_id={owner_user_id}")
                else:
                    # Player was sold before in another session — auto-set willing = purchase price
                    cursor.execute("""
                        UPDATE session_team_players 
                        SET willing_price = purchase_price
                        WHERE team_id = %s AND session_player_id = %s
                    """, (winning_team_id, session_player_id))
                    print(f"[SELL] Player sold before. Auto-set willing_price = purchase_price for {player_name}")
        
        # ============================================
        # HANDLE LOSING TEAMS (BOT LOSERS)
        # Find teams that had willing price but lost — refund them
        # ============================================
        cursor.execute("""
            SELECT 
                stp.team_id,
                stp.purchase_price as original_price,
                stp.willing_price,
                t.team_name
            FROM session_team_players stp
            JOIN session_players sp ON sp.id = stp.session_player_id
            JOIN teams t ON t.id = stp.team_id
            WHERE sp.player_id = %s
              AND stp.team_id != %s
              AND stp.willing_price IS NOT NULL
        """, (player_id, winning_team_id))
        losing_teams = cursor.fetchall()
        
        for loser in losing_teams:
            loser_team_id = loser['team_id']
            original_price = float(loser['original_price'])
            willing_price = float(loser['willing_price']) if loser['willing_price'] else 0
            reserve_amount = (willing_price - original_price) * 0.20 if willing_price else 0
            
            # Refund original price + reserve back to team
            cursor.execute("""
                UPDATE teams
                SET spent = GREATEST(0, spent - %s),
                    reserved = GREATEST(0, reserved - %s)
                WHERE id = %s
            """, (original_price, reserve_amount, loser_team_id))
            
            print(f"[SELL-REFUND] {loser['team_name']} lost {player_name}. Refunded ₹{original_price}Cr + ₹{reserve_amount}Cr reserve")
        
        # ============================================
        # REMOVE PLAYER FROM LOSING TEAMS' SQUADS
        # ============================================
        cursor.execute("""
            DELETE stp FROM session_team_players stp
            JOIN session_players sp ON sp.id = stp.session_player_id
            WHERE sp.player_id = %s
              AND stp.team_id != %s
        """, (player_id, winning_team_id))
        
        print(f"[SELL-REMOVE] Removed {player_name} from all losing teams' squads")
        
        # ============================================
        # CLEAR SESSION STATE
        # ============================================
        
        # Clear session's current player
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_player_id = NULL, current_bid = 0, current_bidder_id = NULL
            WHERE id = %s
        """, (active_session_id,))
        
        # Clear skip records for this session
        cursor.execute("""
            DELETE FROM session_skips 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        
        # Clear hidden bids for this player
        cursor.execute("""
            DELETE FROM session_hidden_max_bids 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        
        # Clear reservations for this player
        cursor.execute("""
            DELETE FROM session_purse_reservations
            WHERE session_player_id = %s
        """, (session_player_id,))
        
        db.commit()
        
        print(f"[SELL] session={active_session_id}, player={session_player_id}, team={winning_team_id}, price={sold_price}, need_willing={need_willing}")
        
    except Exception as e:
        db.rollback()
        import traceback
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        print(f"[SELL ERROR] {error_msg}")
        return jsonify({'error': f'Server error: {str(e)}', 'traceback': error_msg}), 500
        
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'success': True, 
        'sold_to': winning_team_id, 
        'price': float(sold_price),
        'player_name': player_name,
        'session_player_id': session_player_id,
        'sold_to_team': winning_team_id,
        'willing_price_requested': need_willing
    })
# ==================== MARK UNSOLD ====================

@bp.route('/auction/unsold', methods=['POST'])
def mark_unsold():
    """Mark player unsold"""
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    force_unsold = data.get('force', False)
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("SELECT * FROM session_players WHERE id = %s", (session_player_id,))
        sp = cursor.fetchone()
        if not sp:
            return jsonify({'error': 'Player not in session'}), 404
        
        # Get session's current bid
        cursor.execute("SELECT current_bid FROM auction_sessions WHERE id = %s", (active_session_id,))
        session_row = cursor.fetchone()
        current_bid = float(session_row['current_bid'] or 0) if session_row else 0
        
        cursor.execute("""
            SELECT COUNT(DISTINCT team_id) as skip_count
            FROM session_skips
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        skip_result = cursor.fetchone()
        skip_count = skip_result['skip_count'] if skip_result else 0
        
        cursor.execute("SELECT team_ids FROM auction_sessions WHERE id = %s", (active_session_id,))
        sess = cursor.fetchone()
        total_teams = 0
        if sess and sess['team_ids']:
            try:
                team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                total_teams = len(team_ids)
            except:
                pass
        
        all_skipped = skip_count >= total_teams and total_teams > 0
        
        if current_bid > 0 and not all_skipped and not force_unsold:
            return jsonify({
                'error': 'There are active bids! Use "Sell Player" or wait for all teams to skip.',
                'skip_count': skip_count,
                'total_teams': total_teams,
                'all_skipped': all_skipped
            }), 400
        
        cursor.execute(
            "UPDATE session_players SET status = 'unsold' WHERE id = %s",
            (session_player_id,)
        )
        
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_player_id = NULL, current_bid = 0, current_bidder_id = NULL
            WHERE id = %s
        """, (active_session_id,))
        
        cursor.execute("""
            DELETE FROM session_skips 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        
        cursor.execute("""
            DELETE FROM session_bids 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        
        cursor.execute("""
            DELETE FROM session_hidden_max_bids 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        
        cursor.execute("""
            DELETE FROM session_purse_reservations 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        
        db.commit()
        
        print(f"[UNSOlD] session={active_session_id}, player={session_player_id}")
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'success': True,
        'status': 'unsold',
        'skip_count': skip_count,
        'total_teams': total_teams
    })


# ==================== UNDO SALE ====================

@bp.route('/auction/undo', methods=['POST'])
def undo_sale():
    """Undo sale"""
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    session_player_id = data.get('session_player_id')
    auction_id = data.get('auction_id')
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("SELECT * FROM session_players WHERE id = %s", (session_player_id,))
        sp = cursor.fetchone()
        
        if not sp or sp['status'] != 'sold':
            return jsonify({'error': 'Player not sold'}), 400
        
        cursor.execute(
            "UPDATE teams SET spent = spent - %s WHERE id = %s",
            (sp['sold_price'], sp['sold_team_id'])
        )
        
        cursor.execute("""
            UPDATE session_players 
            SET status = 'available', sold_team_id = NULL, sold_price = NULL 
            WHERE id = %s
        """, (session_player_id,))
        
        cursor.execute("""
            DELETE FROM session_team_players 
            WHERE team_id = %s AND session_player_id = %s
        """, (sp['sold_team_id'], session_player_id))
        
        # Also delete any pending willing price popup for this player
        cursor.execute("""
            DELETE FROM pending_willing_price 
            WHERE session_player_id = %s AND team_id = %s
        """, (session_player_id, sp['sold_team_id']))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({'success': True})


# ==================== RE-BID ====================

@bp.route('/auction/rebid', methods=['POST'])
def rebid_player():
    """Reset bidding — clear all bids, keep player in auction at base price"""
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    if not auction_id or not session_player_id:
        return jsonify({'error': 'Missing IDs'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (active_session_id,))
        auction_session = cursor.fetchone()
        if not auction_session:
            return jsonify({'error': 'Session not found'}), 404
        
        cursor.execute("""
            SELECT sp.*, p.player_name, p.category, p.overseas
            FROM session_players sp
            JOIN players p ON sp.player_id = p.id
            WHERE sp.id = %s AND sp.session_id = %s
        """, (session_player_id, active_session_id))
        player = cursor.fetchone()
        
        if not player:
            return jsonify({'error': 'Player not found in session'}), 404
        
        if auction_session.get('current_player_id') != session_player_id:
            return jsonify({'error': 'Player is not currently active'}), 400
        
        # Delete session bids and skips
        cursor.execute("""
            DELETE FROM session_bids WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        
        cursor.execute("""
            DELETE FROM session_skips WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        
        # Reset session state — bid = 0 (no bids), bidder = NULL
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_bid = 0, current_bidder_id = NULL
            WHERE id = %s
        """, (active_session_id,))
        
        # Deactivate hidden bids
        cursor.execute("""
            UPDATE session_hidden_max_bids SET is_active = FALSE 
            WHERE session_player_id = %s
        """, (session_player_id,))
        
        # Release reservations
        cursor.execute("""
            UPDATE session_purse_reservations SET status = 'released' 
            WHERE session_player_id = %s
        """, (session_player_id,))
        
        db.commit()
        
        print(f"[REBID] session={active_session_id}, player={session_player_id}")
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'success': True,
        'player_name': player['player_name'],
        'base_price': float(player['base_price']),
        'session_player_id': session_player_id,
        'message': 'Bidding reset. All bids cleared.'
    })


# ==================== DESELECT PLAYER ====================

@bp.route('/auction/deselect_player', methods=['POST'])
def deselect_player():
    """Deselect player — clear auction display"""
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    if not auction_id:
        return jsonify({'error': 'Missing auction_id'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if not auction:
            return jsonify({'error': 'Auction not found'}), 404
        
        # Get session's current player before clearing
        cursor.execute("SELECT current_player_id FROM auction_sessions WHERE id = %s", (active_session_id,))
        sess = cursor.fetchone()
        current_player_id = sess['current_player_id'] if sess else None
        
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_player_id = NULL, current_bid = 0, current_bidder_id = NULL
            WHERE id = %s
        """, (active_session_id,))
        
        if current_player_id:
            # FIX: Reset player status back to 'available'
            cursor.execute("""
                UPDATE session_players 
                SET status = 'available' 
                WHERE id = %s AND session_id = %s
            """, (current_player_id, active_session_id))
            
            cursor.execute("""
                DELETE FROM session_skips WHERE session_id = %s AND session_player_id = %s
            """, (active_session_id, current_player_id))
            cursor.execute("""
                DELETE FROM session_bids WHERE session_id = %s AND session_player_id = %s
            """, (active_session_id, current_player_id))
        
        db.commit()
        
        print(f"[DESELECT] session={active_session_id}, previous_player={current_player_id}")
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'success': True,
        'message': 'Player deselected',
        'previous_player_id': current_player_id
    })

# ==================== STATUS POLLING ====================

def shared_status():
    """Single optimized status endpoint for ALL clients — NO CACHING"""
    start = time.time()
    auction_id = request.args.get('auction_id', type=int)
    session_id = request.args.get('session_id', type=int) or session.get('active_session_id')
    team_id = request.args.get('team_id', type=int)
    
    if not auction_id or not session_id:
        return jsonify({"error": "Missing auction_id or session_id"}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # FIX: Clear any lingering transaction and use READ COMMITTED
        try:
            cursor.execute("ROLLBACK")
        except:
            pass
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        
        # ONE QUERY: Session + Auction + Current Player (NO current_bid from here)
        cursor.execute("""
            SELECT 
                s.id as session_id, s.status as session_status, s.current_player_id,
                s.team_ids,
                a.id as auction_id, a.league_name, a.status as auction_status,
                a.squad_size, a.purse_limit, a.overseas_limit,
                p.player_name, p.category, p.overseas, sp.base_price
            FROM auction_sessions s
            JOIN auctions a ON s.auction_id = a.id
            LEFT JOIN session_players sp ON sp.id = s.current_player_id AND sp.session_id = s.id
            LEFT JOIN players p ON p.id = sp.player_id
            WHERE s.id = %s AND a.id = %s
        """, (session_id, auction_id))
        
        row = cursor.fetchone()
        
        if not row:
            return jsonify({"status": "none", "error": "Session not found"}), 404
        
        # === CRITICAL: Always compute current_bid from session_bids table ===
        # This is the SOURCE OF TRUTH - never trust auction_sessions.current_bid
        computed_current_bid = 0
        computed_current_bidder_id = None
        computed_current_bidder_name = None
        bid_history = []
        
        if row['current_player_id']:
            # Get ALL bids for this player, ordered by amount DESC, then time DESC
            cursor.execute("""
                SELECT sb.bid_amount, sb.created_at, sb.team_id, t.team_name
                FROM session_bids sb
                JOIN teams t ON t.id = sb.team_id
                WHERE sb.session_id = %s AND sb.session_player_id = %s
                ORDER BY sb.bid_amount DESC, sb.created_at DESC
                LIMIT 10
            """, (session_id, row['current_player_id']))
            bids = cursor.fetchall()
            
            bid_history = [{
                "bidder": b['team_name'],
                "amount": float(b['bid_amount']),
                "time": b['created_at'].isoformat() if b['created_at'] else None
            } for b in bids]
            
            # The highest bid is the first one (already ordered by bid_amount DESC)
            if bids:
                highest = bids[0]
                computed_current_bid = float(highest['bid_amount'])
                computed_current_bidder_id = highest['team_id']
                computed_current_bidder_name = highest['team_name']
        
        # DEBUG LOG - show what we computed vs what auction_sessions says
        cursor.execute("SELECT current_bid, current_bidder_id FROM auction_sessions WHERE id = %s", (session_id,))
        db_row = cursor.fetchone()
        print(f"[STATUS-DB] session_id={session_id}, DB_current_bid={db_row.get('current_bid') if db_row else 'NONE'}, COMPUTED_current_bid={computed_current_bid}, bid_history_count={len(bid_history)}")
        
        # ONE QUERY: All teams with counts
        teams = []
        if row['team_ids']:
            try:
                team_ids = json.loads(row['team_ids']) if isinstance(row['team_ids'], str) else row['team_ids']
            except:
                team_ids = []
            
            if team_ids:
                format_ids = ','.join(['%s'] * len(team_ids))
                cursor.execute(f"""
                    SELECT 
                        t.id, t.team_name, t.purse_limit, t.spent, t.reserved,
                        COUNT(stp.id) as squad_count,
                        SUM(CASE WHEN p.overseas = TRUE THEN 1 ELSE 0 END) as overseas_count
                    FROM teams t
                    LEFT JOIN session_team_players stp ON stp.team_id = t.id
                    LEFT JOIN session_players sp ON sp.id = stp.session_player_id AND sp.session_id = %s
                    LEFT JOIN players p ON p.id = sp.player_id
                    WHERE t.id IN ({format_ids})
                    GROUP BY t.id
                """, (session_id,) + tuple(team_ids))
                teams = [{
                    "id": t['id'],
                    "name": t['team_name'],
                    "purse": float(t['purse_limit']),
                    "spent": float(t['spent'] or 0),
                    "reserved": float(t['reserved'] or 0),
                    "remaining": float(t['purse_limit'] - (t['spent'] or 0) - (t['reserved'] or 0)),
                    "squad_count": t['squad_count'],
                    "overseas_count": t['overseas_count'] or 0
                } for t in cursor.fetchall()]
        
        # ONE QUERY: Skip count
        skip_count = 0
        total_teams = len(teams)
        all_skipped = False
        if row['current_player_id']:
            cursor.execute("""
                SELECT COUNT(DISTINCT team_id) as skip_count
                FROM session_skips
                WHERE session_id = %s AND session_player_id = %s
            """, (session_id, row['current_player_id']))
            skip_result = cursor.fetchone()
            skip_count = skip_result['skip_count'] if skip_result else 0
            all_skipped = skip_count >= total_teams and total_teams > 0
        
        # Team-specific data
        my_team = None
        is_current_bidder = False
        remaining_purse = 0
        if team_id:
            cursor.execute("""
                SELECT (purse_limit - COALESCE(spent, 0) - COALESCE(reserved, 0)) as remaining
                FROM teams WHERE id = %s
            """, (team_id,))
            team_row = cursor.fetchone()
            if team_row:
                remaining_purse = float(team_row['remaining'] or 0)
                is_current_bidder = (computed_current_bidder_id == team_id)
                my_team = {
                    "id": team_id,
                    "remaining_purse": remaining_purse,
                    "is_current_bidder": is_current_bidder,
                    "has_skipped": False
                }
                if row['current_player_id']:
                    cursor.execute("""
                        SELECT 1 FROM session_skips
                        WHERE session_id = %s AND session_player_id = %s AND team_id = %s
                        LIMIT 1
                    """, (session_id, row['current_player_id'], team_id))
                    my_team['has_skipped'] = cursor.fetchone() is not None
        
        # Build response using COMPUTED values (source of truth)
        result = {
            "status": row['auction_status'] or 'live',
            "session_status": row['session_status'] or 'active',
            "league_name": row['league_name'],
            "auction_id": row['auction_id'],
            "session_id": row['session_id'],
            "current_player": row['player_name'] if row['current_player_id'] else None,
            "player_category": row['category'],
            "session_player_id": row['current_player_id'],
            "base_price": float(row['base_price']) if row['base_price'] else 2.0,
            "current_bid": computed_current_bid,
            "current_bidder": computed_current_bidder_name,
            "current_bidder_id": computed_current_bidder_id,
            "overseas": bool(row['overseas']) if row['overseas'] is not None else False,
            "has_bids": len(bid_history) > 0,
            "skip_count": skip_count,
            "all_skipped": all_skipped,
            "total_teams": total_teams,
            "remaining_purse": remaining_purse if team_id else None,
            "is_current_bidder": is_current_bidder if team_id else False,
            "bid_history": bid_history,
            "teams": teams,
        }
        
        # DEBUG LOG
        print(f"[STATUS-OUT] session_id={session_id}, player={result.get('current_player')}, bid={result.get('current_bid')}, bidder={result.get('current_bidder')}, has_bids={result.get('has_bids')}, bid_history_count={len(bid_history)}")
        
        return jsonify(result)
        
    finally:
        cursor.close()
        db.close()

@bp.route('/auction/status')
def get_status():
    """Get auction status — delegates to shared endpoint for consistency"""
    return shared_status()


# ==================== PAUSE / RESUME ====================

@bp.route('/auction/pause', methods=['POST'])
def pause_auction():
    if session.get('role') not in ['admin', 'auctioneer']:
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
    if session.get('role') not in ['admin', 'auctioneer']:
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


# ==================== HIDDEN BID ====================

@bp.route('/auction/hidden_bid', methods=['POST'])
def place_hidden_bid():
    """Hidden max bid"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = int(data.get('team_id'))
    max_amount = float(data.get('max_amount', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')

    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    try:
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
        if max_amount > available:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        # Get old reservation to subtract from team reserved
        cursor.execute("""
            SELECT reserved_amount
            FROM session_purse_reservations
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s AND status = 'active'
        """, (active_session_id, session_player_id, team_id))
        old_res = cursor.fetchone()
        
        # Delete old hidden bid
        cursor.execute("""
            DELETE FROM session_hidden_max_bids
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        
        # Insert new hidden bid
        cursor.execute("""
            INSERT INTO session_hidden_max_bids (session_id, session_player_id, team_id, max_bid) 
            VALUES (%s, %s, %s, %s)
        """, (active_session_id, session_player_id, team_id, max_amount))

        # Delete old reservation
        cursor.execute("""
            DELETE FROM session_purse_reservations
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s AND status = 'active'
        """, (active_session_id, session_player_id, team_id))
        
        # Insert new reservation
        cursor.execute("""
            INSERT INTO session_purse_reservations (session_id, session_player_id, team_id, reserved_amount, status)
            VALUES (%s, %s, %s, %s, 'active')
        """, (active_session_id, session_player_id, team_id, max_amount))
        
        # Adjust team reserved: subtract old, add new
        old_amount = float(old_res['reserved_amount']) if old_res else 0
        cursor.execute("""
            UPDATE teams SET reserved = reserved - %s + %s WHERE id = %s
        """, (old_amount, max_amount, team_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})


# ==================== AUTO BID ====================

# ==================== AUTO BID (BOT LOGIC) ====================

# ==================== AUTO BID (PROACTIVE BOT) ====================

@bp.route('/auction/auto_bid', methods=['POST'])
def auto_counter_bid():
    """Auto bid - PROACTIVE bot: bids original price immediately, then reactive counter"""
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    current_bid = float(data.get('current_bid', 0))
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # Get player_id and base_price for current session_player
        cursor.execute("SELECT player_id, base_price FROM session_players WHERE id = %s", (session_player_id,))
        player_row = cursor.fetchone()
        if not player_row:
            return jsonify({'auto_bid': False})
        player_id = player_row['player_id']
        base_price = float(player_row['base_price'] or 2.0)
        
        # Get current highest bidder to avoid self-bidding
        cursor.execute("""
            SELECT team_id, bid_amount 
            FROM session_bids 
            WHERE session_id = %s AND session_player_id = %s
            ORDER BY bid_amount DESC, created_at DESC
            LIMIT 1
        """, (active_session_id, session_player_id))
        current_winner = cursor.fetchone()
        current_winner_id = current_winner['team_id'] if current_winner else None
        
        # ============================================
        # CHECK 1: Session-specific hidden max bids
        # ============================================
        cursor.execute("""
            SELECT h.*, t.team_name, t.purse_limit, t.spent, t.reserved 
            FROM session_hidden_max_bids h 
            JOIN teams t ON h.team_id = t.id 
            WHERE h.session_player_id = %s AND h.is_active = TRUE
            ORDER BY h.max_bid DESC
        """, (session_player_id,))
        hidden_bids = cursor.fetchall()
        
        # ============================================
        # CHECK 2: Historical willing prices (PROACTIVE BOT MODE)
        # Teams that bought this player before become bots
        # ============================================
        cursor.execute("""
            SELECT 
                stp.team_id,
                stp.purchase_price as original_price,
                stp.willing_price as max_bid,
                t.team_name,
                t.purse_limit,
                t.spent,
                t.reserved
            FROM session_team_players stp
            JOIN session_players sp ON sp.id = stp.session_player_id
            JOIN teams t ON t.id = stp.team_id
            WHERE sp.player_id = %s
              AND stp.willing_price IS NOT NULL
              AND stp.willing_price > stp.purchase_price
            ORDER BY stp.willing_price DESC
        """, (player_id,))
        willing_bids = cursor.fetchall()
        
        # ============================================
        # COMBINE ALL BIDS
        # ============================================
        all_bids = []
        
        # Add hidden bids
        for hb in hidden_bids:
            # Skip if this team is already winning
            if current_winner_id == hb['team_id']:
                continue
            all_bids.append({
                'team_id': hb['team_id'],
                'max_bid': float(hb['max_bid']),
                'original_price': 0,
                'team_name': hb['team_name'],
                'purse_limit': hb['purse_limit'],
                'spent': hb['spent'],
                'reserved': hb['reserved'],
                'source': 'hidden'
            })
        
        # Add willing price bids (bots)
        for wb in willing_bids:
            # Skip if this team is already winning
            if current_winner_id == wb['team_id']:
                continue
            # Skip if already added via hidden bid
            if any(b['team_id'] == wb['team_id'] for b in all_bids):
                continue
            all_bids.append({
                'team_id': wb['team_id'],
                'max_bid': float(wb['max_bid']),
                'original_price': float(wb['original_price']),
                'team_name': wb['team_name'],
                'purse_limit': wb['purse_limit'],
                'spent': wb['spent'],
                'reserved': wb['reserved'],
                'source': 'willing'
            })
        
        if not all_bids:
            return jsonify({'auto_bid': False})
        
        # ============================================
        # FIND BEST BOT BID (PROACTIVE LOGIC)
        # ============================================
        best_bid = None
        
        for bid in all_bids:
            if bid['source'] == 'willing':
                # ============================================
                # PROACTIVE BOT: Start from original price immediately
                # ============================================
                
                if current_bid <= 0:
                    # NO BIDS YET: Bot immediately bids original price
                    # "I already paid ₹3Cr, not letting him go for ₹2Cr base"
                    proposed_bid = bid['original_price']
                    
                elif current_bid < bid['original_price']:
                    # Someone bid below our original price
                    # Bot jumps to original price to reclaim
                    proposed_bid = bid['original_price']
                    
                else:
                    # Someone exceeded our original price
                    # Counter with standard increment
                    increment = get_min_bid_increment(current_bid)
                    proposed_bid = current_bid + increment
                
                # Cap at willing price (max bot will go)
                if proposed_bid > bid['max_bid']:
                    proposed_bid = bid['max_bid']
                
                # Only bid if within max and higher than current
                if proposed_bid <= bid['max_bid'] and proposed_bid > current_bid:
                    if not best_bid or proposed_bid > best_bid['amount']:
                        best_bid = {
                            'team_id': bid['team_id'],
                            'team_name': bid['team_name'],
                            'amount': proposed_bid,
                            'max_bid': bid['max_bid'],
                            'source': 'willing',
                            'purse_limit': bid['purse_limit'],
                            'spent': bid['spent'],
                            'reserved': bid['reserved']
                        }
            
            else:
                # HIDDEN BID LOGIC (existing behavior - only reactive)
                increment = get_min_bid_increment(current_bid)
                proposed_bid = current_bid + increment
                if proposed_bid > bid['max_bid']:
                    proposed_bid = bid['max_bid']
                
                if proposed_bid <= bid['max_bid'] and proposed_bid > current_bid:
                    if not best_bid or proposed_bid > best_bid['amount']:
                        best_bid = {
                            'team_id': bid['team_id'],
                            'team_name': bid['team_name'],
                            'amount': proposed_bid,
                            'max_bid': bid['max_bid'],
                            'source': 'hidden',
                            'purse_limit': bid['purse_limit'],
                            'spent': bid['spent'],
                            'reserved': bid['reserved']
                        }
        
        if not best_bid:
            return jsonify({'auto_bid': False, 'reason': 'No valid bot bid'})
        
        winner = best_bid
        
        # ============================================
        # CHECK FUNDS
        # ============================================
        available = float(winner['purse_limit']) - float(winner['spent'] or 0) - float(winner['reserved'] or 0)
        
        if winner['source'] == 'willing':
            # Bot has reserve locked (20% of willing price)
            cursor.execute("""
                SELECT (willing_price * 0.20) as reserve_amount
                FROM session_team_players stp
                JOIN session_players sp ON sp.id = stp.session_player_id
                WHERE sp.player_id = %s AND stp.team_id = %s
                LIMIT 1
            """, (player_id, winner['team_id']))
            res = cursor.fetchone()
            reserve_amount = float(res['reserve_amount']) if res else 0
            
            # Available + reserve must cover bid
            if winner['amount'] > available + reserve_amount:
                return jsonify({'auto_bid': False, 'reason': 'Bot insufficient funds'})
        else:
            if winner['amount'] > available:
                return jsonify({'auto_bid': False, 'reason': 'Insufficient funds'})
        
        # ============================================
        # PLACE THE AUTO-BID
        # ============================================
        cursor.execute("""
            INSERT INTO session_bids (session_id, session_player_id, team_id, bid_amount) 
            VALUES (%s, %s, %s, %s)
        """, (active_session_id, session_player_id, winner['team_id'], winner['amount']))
        
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_bid = %s, current_bidder_id = %s
            WHERE id = %s
        """, (winner['amount'], winner['team_id'], active_session_id))
        
        db.commit()
        
        print(f"[AUTO-BID] team={winner['team_name']}, amount={winner['amount']}, source={winner['source']}, max={winner['max_bid']}")
        
    finally:
        cursor.close()
        db.close()
    
    clear_cache(f'auction:status:{auction_id}')
    clear_cache('auction:status:active')
    
    return jsonify({
        'auto_bid': True,
        'team': winner['team_name'],
        'amount': winner['amount'],
        'source': winner['source']
    })
@bp.route('/auction/players')
def get_players():
    """Get available players for current session"""
    auction_id = request.args.get('auction_id')
    active_session_id = request.args.get('session_id') or session.get('active_session_id')
    
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("""
            SELECT sp.id as session_player_id, sp.base_price, sp.status,
                   p.id as player_id, p.player_name, p.category, p.overseas
            FROM session_players sp
            JOIN players p ON sp.player_id = p.id
            WHERE sp.session_id = %s AND sp.status IN ('available', 'unsold', 'in_auction')
            ORDER BY 
                CASE sp.status 
                    WHEN 'in_auction' THEN 1 
                    WHEN 'available' THEN 2 
                    WHEN 'unsold' THEN 3 
                END,
                p.player_name
        """, (active_session_id,))
        players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'players': players})

# ==================== CHAT SYSTEM ====================

@bp.route('/auction/chat/send', methods=['POST'])
def admin_chat_send():
    """Admin broadcasts a message to all teams in the session"""
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_id = data.get('session_id')
    message = data.get('message', '').strip()
    msg_type = data.get('msg_type', 'admin_chat')
    
    if not message or len(message) > 120:
        return jsonify({'error': 'Invalid message'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("""
            INSERT INTO auction_chat 
            (auction_id, session_id, sender_id, sender_name, sender_type, message, msg_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            auction_id, 
            session_id,
            session.get('user_id'),
            'Admin',
            'admin',
            message,
            msg_type
        ))
        db.commit()
        
        msg_id = cursor.lastrowid
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'message_id': msg_id})


@bp.route('/auction/chat/messages')
def admin_chat_messages():
    """Get chat messages for admin (sees ALL messages including team chats)"""
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = request.args.get('auction_id', type=int)
    session_id = request.args.get('session_id', type=int)
    after_id = request.args.get('after_id', type=int, default=0)
    
    if not auction_id or not session_id:
        return jsonify({'error': 'Missing IDs'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("""
            SELECT id, sender_id, sender_name, sender_type, message, msg_type, created_at
            FROM auction_chat
            WHERE auction_id = %s AND session_id = %s AND id > %s
            ORDER BY created_at ASC
            LIMIT 50
        """, (auction_id, session_id, after_id))
        
        messages = cursor.fetchall()
        
        result = []
        for msg in messages:
            result.append({
                'id': msg['id'],
                'sender': msg['sender_name'],
                'sender_type': msg['sender_type'],
                'text': msg['message'],
                'msg_type': msg['msg_type'],
                'time': msg['created_at'].isoformat() if msg['created_at'] else None
            })
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'messages': result})

