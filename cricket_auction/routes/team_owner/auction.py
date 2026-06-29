from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json
# Import at top of file
from routes.admin.auction import shared_status

import time

start = time.time()

bp = Blueprint('team_owner_auction', __name__, url_prefix='/team_owner')

def get_user_team(cursor, user_id, auction_id):
    """Get team owned by user"""
    cursor.execute("SELECT * FROM teams WHERE owner_id = %s AND auction_id = %s", (user_id, auction_id))
    return cursor.fetchone()

def get_user_team_by_ids(cursor, user_id, auction_id):
    """Get all teams where user is owner (for owner_ids JSON support)"""
    cursor.execute("SELECT * FROM teams WHERE auction_id = %s", (auction_id,))
    all_teams = cursor.fetchall()
    for team in all_teams:
        if team.get('owner_id') == user_id:
            return team
        if team.get('owner_ids'):
            try:
                owner_ids = json.loads(team['owner_ids']) if isinstance(team['owner_ids'], str) else team['owner_ids']
                if user_id in owner_ids:
                    return team
            except:
                pass
    return None

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


# ==================== DASHBOARD - ENTER AUCTION ID ====================

@bp.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    """Team owner dashboard - enter auction ID to see sessions"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    if request.method == 'POST':
        auction_id = request.form.get('auction_id', type=int)
        if not auction_id:
            flash('Please enter a valid Auction ID')
            return redirect('/team_owner/dashboard')
        return redirect(f'/team_owner/sessions?auction_id={auction_id}')
    
    return render_template('team_owner/dashboard.html')


# ==================== SESSIONS LIST ====================

@bp.route('/sessions')
def sessions_list():
    """Show Your Sessions and Other Sessions"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    auction_id = request.args.get('auction_id', type=int)
    if not auction_id:
        flash('Auction ID required')
        return redirect('/team_owner/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if not auction:
            flash('Auction not found')
            return redirect('/team_owner/dashboard')
        
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        user_team_id = user_team['id'] if user_team else None
        
        cursor.execute("""
            SELECT s.*, 
                   (SELECT COUNT(*) FROM session_players sp WHERE sp.session_id = s.id) as player_count,
                   (SELECT COUNT(*) FROM session_team_players stp 
                    JOIN session_players sp ON stp.session_player_id = sp.id 
                    WHERE sp.session_id = s.id) as sold_count
            FROM auction_sessions s
            WHERE s.auction_id = %s
            ORDER BY s.created_at ASC
        """, (auction_id,))
        all_sessions = cursor.fetchall()
        
        your_sessions = []
        other_sessions = []
        
        for sess in all_sessions:
            session_team_ids = []
            if sess.get('team_ids'):
                try:
                    session_team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                except:
                    session_team_ids = []
            
            sess['team_ids_list'] = session_team_ids
            sess['team_count'] = len(session_team_ids)
            sess['is_yours'] = user_team_id in session_team_ids if user_team_id else False
            
            if sess['is_yours']:
                your_sessions.append(sess)
            else:
                other_sessions.append(sess)
        
        cursor.execute("SELECT COUNT(*) as total FROM teams WHERE auction_id = %s", (auction_id,))
        total_teams_result = cursor.fetchone()
        total_teams = total_teams_result['total'] if total_teams_result else 0
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/sessions.html',
        auction=auction,
        your_sessions=your_sessions,
        other_sessions=other_sessions,
        user_team=user_team,
        total_teams=total_teams
    )


# ==================== JOIN SESSION ====================

@bp.route('/session/<int:session_id>/join')
def join_session(session_id):
    """Join a session - either as bidder or viewer"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    mode = request.args.get('mode', 'bid')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("""
            SELECT s.*, a.id as auction_id, a.league_name, a.status as auction_status,
                   a.squad_size, a.purse_limit, a.overseas_limit
            FROM auction_sessions s
            JOIN auctions a ON s.auction_id = a.id
            WHERE s.id = %s
        """, (session_id,))
        sess = cursor.fetchone()
        
        if not sess:
            flash('Session not found')
            return redirect('/team_owner/dashboard')
        
        auction_id = sess['auction_id']
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        user_team_id = user_team['id'] if user_team else None
        
        session_team_ids = []
        if sess.get('team_ids'):
            try:
                session_team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                session_team_ids = []
        
        is_your_session = user_team_id in session_team_ids if user_team_id else False
        
        if mode == 'bid' and not is_your_session:
            mode = 'view'
        
        session['active_session_id'] = session_id
        session['active_auction_id'] = auction_id
        session['session_mode'] = mode
        
    finally:
        cursor.close()
        db.close()
    
    return redirect(f'/team_owner/auction?session={session_id}&mode={mode}')


# ==================== MAIN AUCTION ROOM ====================

@bp.route('/auction')
def auction_room():
    """Team owner auction room"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    url_session_id = request.args.get('session', type=int)
    mode = request.args.get('mode') or session.get('session_mode', 'bid')
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
            return redirect('/team_owner/dashboard')
        
        auction_id = auction['id']
        session['active_auction_id'] = auction_id
        
        if not active_session_id:
            return redirect(f'/team_owner/sessions?auction_id={auction_id}')
        
        session['active_session_id'] = active_session_id
        
        cursor.execute("""
            SELECT s.*, a.league_name, a.status as auction_status, a.squad_size, 
                   a.purse_limit, a.overseas_limit, a.last_sold_session_player_id,
                   a.last_sold_team_id, a.last_sold_price, a.last_sold_at
            FROM auction_sessions s
            JOIN auctions a ON s.auction_id = a.id
            WHERE s.id = %s
        """, (active_session_id,))
        auction_session = cursor.fetchone()
        
        if not auction_session:
            session.pop('active_session_id', None)
            flash('Session expired. Contact admin.')
            return redirect('/team_owner/dashboard')
        
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        
        session_team_ids = []
        if auction_session.get('team_ids'):
            try:
                session_team_ids = json.loads(auction_session['team_ids']) if isinstance(auction_session['team_ids'], str) else auction_session['team_ids']
            except:
                session_team_ids = []
        
        is_your_session = user_team and user_team['id'] in session_team_ids if user_team else False
        
        if not is_your_session:
            mode = 'view'
        
        # GET SESSION TEAMS
        session_teams = []
        if session_team_ids:
            format_ids = ','.join(['%s'] * len(session_team_ids))
            cursor.execute(f"""
                SELECT t.*, 
                       (t.purse_limit - COALESCE(t.spent, 0) - COALESCE(t.reserved, 0)) as available_purse,
                       (SELECT COUNT(*) FROM session_team_players stp 
                        JOIN session_players sp ON stp.session_player_id = sp.id 
                        WHERE stp.team_id = t.id AND sp.session_id = {active_session_id}) as squad_count,
                       (SELECT COUNT(*) FROM session_team_players stp 
                        JOIN session_players sp ON stp.session_player_id = sp.id 
                        JOIN players p ON sp.player_id = p.id 
                        WHERE stp.team_id = t.id AND p.overseas = TRUE AND sp.session_id = {active_session_id}) as overseas_count
                FROM teams t
                WHERE t.id IN ({format_ids})
            """, tuple(session_team_ids))
            session_teams = cursor.fetchall()
        
        # GET ALL TEAMS SQUAD DATA
        all_teams_squads = []
        cursor.execute("""
            SELECT t.id, t.team_name, t.owner_id,
                   (SELECT COUNT(*) FROM session_team_players stp 
                    JOIN session_players sp ON stp.session_player_id = sp.id 
                    WHERE stp.team_id = t.id AND sp.session_id = %s) as squad_count
            FROM teams t
            WHERE t.auction_id = %s
        """, (active_session_id, auction_id))
        all_teams = cursor.fetchall()
        
        for team in all_teams:
            cursor.execute("""
                SELECT stp.purchase_price, stp.willing_price,
                       p.player_name, p.category, p.overseas, sp.base_price,
                       sp.id as session_player_id
                FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                JOIN players p ON sp.player_id = p.id
                WHERE stp.team_id = %s AND sp.session_id = %s
            """, (team['id'], active_session_id))
            squad_players = cursor.fetchall()
            
            willing_prices = {}
            if user_team and team['id'] == user_team['id']:
                cursor.execute("""
                    SELECT session_player_id, willing_price 
                    FROM session_team_players 
                    WHERE team_id = %s
                """, (team['id'],))
                wp_rows = cursor.fetchall()
                for wp in wp_rows:
                    if wp['willing_price']:
                        willing_prices[wp['session_player_id']] = wp['willing_price']
            
            team['squad_players'] = squad_players
            team['willing_prices'] = willing_prices
            team['is_your_team'] = user_team and team['id'] == user_team['id']
            all_teams_squads.append(team)
        
        # GET SKIP COUNT
        skip_count = 0
        total_session_teams = len(session_team_ids)
        
        # GET SESSION PLAYERS
        cursor.execute("""
            SELECT sp.id as session_player_id, sp.base_price, sp.status, 
                   sp.sold_price, sp.sold_team_id,
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
        
        # CURRENT PLAYER
        current_player = None
        current_bid = 0
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
                SELECT COUNT(DISTINCT team_id) as skip_count 
                FROM session_skips 
                WHERE session_id = %s AND session_player_id = %s
            """, (active_session_id, auction_session['current_player_id']))
            skip_result = cursor.fetchone()
            skip_count = skip_result['skip_count'] if skip_result else 0
            
            cursor.execute("""
                SELECT COUNT(*) as bid_count FROM session_bids 
                WHERE session_id = %s AND session_player_id = %s
            """, (active_session_id, auction_session['current_player_id']))
            bid_result = cursor.fetchone()
            has_bids = bid_result['bid_count'] > 0 if bid_result else False
        
        # GET BID HISTORY
        cursor.execute("""
            SELECT sb.*, t.team_name
            FROM session_bids sb
            JOIN teams t ON sb.team_id = t.id
            WHERE sb.session_id = %s AND sb.session_player_id = %s
            ORDER BY sb.created_at DESC
            LIMIT 20
        """, (active_session_id, auction_session.get('current_player_id') or 0))
        bid_history = cursor.fetchall()
        
        # GET ALL SESSIONS FOR NAVIGATION
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
            sess['is_your_session'] = user_team and user_team['id'] in sess['team_ids_list'] if user_team else False
        
        # Check if current bidder is user's team
        is_current_bidder = False
        if current_player and auction_session.get('current_bidder_id') and user_team:
            is_current_bidder = auction_session['current_bidder_id'] == user_team['id']
        
        # Build auction dict
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
    
    return render_template('team_owner/auction.html', 
        auction=auction_dict,
        auction_session=auction_session,
        session_id=active_session_id,
        current_session=auction_session,
        players=players, 
        teams=session_teams,
        user_team=user_team,
        all_sessions=all_sessions,
        current_player=current_player,
        current_bid=current_bid,
        has_bids=has_bids,
        mode=mode,
        is_your_session=is_your_session,
        all_teams_squads=all_teams_squads,
        bid_history=bid_history,
        skip_count=skip_count,
        total_session_teams=total_session_teams,
        is_current_bidder=is_current_bidder
    )


# ==================== SET WILLING PRICE (Legacy - kept for compatibility) ====================

@bp.route('/auction/set_willing_price', methods=['POST'])
def set_willing_price():
    """Set willing price for a purchased player"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    session_player_id = data.get('session_player_id')
    team_id = int(data.get('team_id'))
    willing_price = float(data.get('willing_price', 0))
    
    active_session_id = session.get('active_session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        user_team = get_user_team_by_ids(cursor, session['user_id'], session.get('active_auction_id'))
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'Unauthorized team'}), 403
        
        cursor.execute("""
            UPDATE session_team_players 
            SET willing_price = %s 
            WHERE team_id = %s AND session_player_id = %s
        """, (willing_price, team_id, session_player_id))
        
        db.commit()
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'willing_price': willing_price})


# ==================== CHECK WILLING PRICE POPUP ====================

@bp.route('/check_willing_price')
def check_willing_price():
    """Check if there's a pending willing price popup for this user"""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'show_popup': False})

    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)

    try:
        cursor.execute("""
            SELECT *
            FROM pending_willing_price
            WHERE user_id = %s
            AND popup_shown = 0
            ORDER BY created_at DESC
            LIMIT 1
        """, (user_id,))

        row = cursor.fetchone()

        if not row:
            return jsonify({'show_popup': False})

        # Mark as shown so it doesn't show again
        cursor.execute("""
            UPDATE pending_willing_price
            SET popup_shown = 1
            WHERE id = %s
        """, (row['id'],))
        db.commit()

        return jsonify({
            'show_popup': True,
            'sale_id': row['id'],
            'player_id': row['player_id'],
            'session_player_id': row['session_player_id'],
            'player_name': row['player_name'],
            'purchase_price': float(row['purchase_price']),
            'team_id': row['team_id']
        })
    finally:
        cursor.close()
        db.close()


# ==================== SAVE WILLING PRICE FROM POPUP ====================

@bp.route('/save_willing_price', methods=['POST'])
def save_willing_price():
    """Save willing price from popup - uses user's team automatically"""
    data = request.get_json()
    session_player_id = data.get('session_player_id')
    willing_price = float(data.get('willing_price', 0))
    auction_id = data.get('auction_id')
    
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # Get user's team for this auction
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        if not user_team:
            return jsonify({'error': 'No team found'}), 404
        
        team_id = user_team['id']
        
        cursor.execute("""
            UPDATE session_team_players
            SET willing_price = %s
            WHERE team_id = %s
            AND session_player_id = %s
        """, (willing_price, team_id, session_player_id))
        
        db.commit()
        
        print(f"[WILLING-PRICE] team={team_id}, session_player={session_player_id}, price={willing_price}")
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'willing_price': willing_price})


# ==================== GET ALL TEAMS SQUAD ====================

@bp.route('/auction/teams_squad')
def get_teams_squad():
    """Get all teams squad data for modal"""
    active_session_id = request.args.get('session_id') or session.get('active_session_id')
    
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # Get auction config for squad_size, overseas_limit, purse_limit
        cursor.execute(
            "SELECT squad_size, purse_limit, overseas_limit FROM auctions WHERE id = (SELECT auction_id FROM auction_sessions WHERE id = %s)",
            (active_session_id,)
        )
        auction_config = cursor.fetchone() or {'squad_size': 18, 'purse_limit': 100, 'overseas_limit': 8}
        
        cursor.execute("""
            SELECT t.id, t.team_name, t.owner_id, t.purse_limit, t.spent,
                   (SELECT COUNT(*) FROM session_team_players stp 
                    JOIN session_players sp ON stp.session_player_id = sp.id 
                    WHERE stp.team_id = t.id AND sp.session_id = %s) as squad_count,
                   (SELECT COUNT(*) FROM session_team_players stp 
                    JOIN session_players sp ON stp.session_player_id = sp.id 
                    JOIN players p ON sp.player_id = p.id 
                    WHERE stp.team_id = t.id AND p.overseas = TRUE AND sp.session_id = %s) as overseas_count
            FROM teams t
            WHERE t.auction_id = (SELECT auction_id FROM auction_sessions WHERE id = %s)
        """, (active_session_id, active_session_id, active_session_id))
        all_teams = cursor.fetchall()
        
        result = []
        for team in all_teams:
            cursor.execute("""
                SELECT stp.purchase_price,
                       p.player_name, p.category, p.overseas
                FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                JOIN players p ON sp.player_id = p.id
                WHERE stp.team_id = %s AND sp.session_id = %s
            """, (team['id'], active_session_id))
            squad_players = cursor.fetchall()
            
            team['squad_players'] = squad_players
            team['squad_size'] = auction_config['squad_size']
            team['overseas_limit'] = auction_config['overseas_limit']
            team['purse_limit'] = float(team['purse_limit'] or auction_config['purse_limit'])
            team['spent'] = float(team['spent'] or 0)
            team['is_your_team'] = False
            result.append(team)
        
        # Mark user's own team
        user_id = session.get('user_id')
        if user_id:
            for team in result:
                if team.get('owner_id') == user_id:
                    team['is_your_team'] = True
        
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'teams': result})

# ==================== BIDDING ====================

@bp.route('/auction/bid', methods=['POST'])
def place_bid():
    """Place bid — team owner only for their own team"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    if session.get('session_mode') == 'view':
        return jsonify({'error': 'View mode - bidding disabled'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    session_id = data.get('session_id')
    team_id = int(data.get('team_id'))
    amount = float(data.get('amount', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
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
        
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            cursor.execute("ROLLBACK")
            return jsonify({'error': 'You can only bid for your own team'}), 403
        
        # Lock the session row to prevent race conditions
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
            return jsonify({'error': 'You skipped this player. Cannot bid.'}), 400        
        
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
        
        # Get base price from session_players
        cursor.execute("SELECT base_price FROM session_players WHERE id = %s FOR UPDATE", (session_player_id,))
        sp_row = cursor.fetchone()
        base_price = float(sp_row['base_price']) if sp_row else 2.0
        
        # === CRITICAL: Re-check highest bid WITH LOCK ===
        cursor.execute("""
            SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
            FROM session_bids 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        bid_info = cursor.fetchone()
        has_bids = bid_info['bid_count'] > 0
        highest_bid = float(bid_info['highest_bid']) if bid_info['highest_bid'] else 0
        
        # === RACE CONDITION FIX: Re-check with actual current session state ===
        actual_current_bid = float(sess.get('current_bid') or 0)
        actual_current_bidder_id = sess.get('current_bidder_id')
        
        # Use the HIGHER of session_bids table and auction_sessions.current_bid
        true_highest_bid = max(highest_bid, actual_current_bid)
        
        if not has_bids and actual_current_bid <= 0:
            # FIRST BID: Must be at least base price
            if amount < base_price:
                cursor.execute("ROLLBACK")
                return jsonify({'error': f'First bid must be at least base price ₹{base_price:.2f}Cr'}), 400
        else:
            # SUBSEQUENT BIDS: Must be higher than true highest bid
            if amount <= true_highest_bid:
                cursor.execute("ROLLBACK")
                return jsonify({
                    'error': f'Bid must be higher than current bid ₹{true_highest_bid:.2f}Cr'
                }), 400
        
        # Check if already highest bidder
        if actual_current_bidder_id == team_id:
            cursor.execute("ROLLBACK")
            return jsonify({'error': 'You are already the highest bidder.'}), 400
        
        available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
        if amount > available:
            cursor.execute("ROLLBACK")
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        # === ATOMIC INSERT AND UPDATE ===
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
        print(f"[BID-VERIFY-TO] session={active_session_id}, player={session_player_id}, team={team_id}, amount={amount}, verify_bid={verify['current_bid']}, verify_bidder={verify['current_bidder_id']}")
        
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


# ==================== SKIP PLAYER ====================

@bp.route('/auction/skip', methods=['POST'])
def skip_player():
    """Team owner skips a player"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    if session.get('session_mode') == 'view':
        return jsonify({'error': 'View mode - skip disabled'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = int(data.get('team_id'))
    reason = data.get('reason', 'manual_skip')
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only skip for your own team'}), 403
        
        cursor.execute("""
            SELECT * FROM session_skips 
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        existing = cursor.fetchone()
        if existing:
            return jsonify({'error': 'Already skipped this player'}), 400
        
        # Check if current bidder - can't skip if you're winning
        cursor.execute("""
            SELECT current_bidder_id FROM auction_sessions WHERE id = %s
        """, (active_session_id,))
        sess_state = cursor.fetchone()
        if sess_state and sess_state['current_bidder_id'] == team_id:
            return jsonify({'error': 'Current bidder cannot skip'}), 400
        
        cursor.execute("""
            INSERT INTO session_skips (session_id, session_player_id, team_id, skipped_by, reason)
            VALUES (%s, %s, %s, %s, %s)
        """, (active_session_id, session_player_id, team_id, session['user_id'], reason))
        
        cursor.execute("""
            SELECT COUNT(DISTINCT team_id) as skip_count
            FROM session_skips
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        skip_result = cursor.fetchone()
        
        cursor.execute("SELECT team_ids FROM auction_sessions WHERE id = %s", (active_session_id,))
        sess = cursor.fetchone()
        total_teams = 0
        if sess and sess['team_ids']:
            try:
                team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
                total_teams = len(team_ids)
            except:
                pass
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    return jsonify({
        'success': True,
        'skip_count': skip_result['skip_count'],
        'total_teams': total_teams,
        'all_skipped': skip_result['skip_count'] >= total_teams and total_teams > 0
    })


# ==================== HIDDEN BID ====================

@bp.route('/auction/hidden_bid', methods=['POST'])
def place_hidden_bid():
    """Hidden max bid"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    if session.get('session_mode') == 'view':
        return jsonify({'error': 'View mode - hidden bids disabled'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = int(data.get('team_id'))
    max_amount = float(data.get('max_amount', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only set hidden bids for your own team'}), 403
        
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
        if max_amount > available:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        # Get old reservation
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
            INSERT INTO session_hidden_max_bids (session_id, session_player_id, team_id, max_bid, is_active) 
            VALUES (%s, %s, %s, %s, TRUE)
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
        
        # Adjust team reserved
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
    requested_bid = float(data.get('current_bid', 0))
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # ============================================
        # FIX 1: Get ACTUAL highest bid from database (not stale frontend value)
        # ============================================
        cursor.execute("""
            SELECT MAX(bid_amount) as highest_bid
            FROM session_bids 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        highest_result = cursor.fetchone()
        db_highest_bid = float(highest_result['highest_bid']) if highest_result and highest_result['highest_bid'] else 0.0
        
        # Use the HIGHER of database max and frontend request (safety)
        current_bid = max(db_highest_bid, requested_bid)
        
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
            if current_winner_id == wb['team_id']:
                continue
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
                # PROACTIVE: Bid original price immediately when no bids or low bids
                if current_bid <= 0:
                    # NO BIDS YET: Bot immediately bids original price
                    proposed_bid = bid['original_price']
                    
                elif current_bid < bid['original_price']:
                    # Someone bid below our original price
                    proposed_bid = bid['original_price']
                    
                else:
                    # Someone exceeded our original price
                    increment = get_min_bid_increment(current_bid)
                    proposed_bid = current_bid + increment
                
                # Cap at willing price
                if proposed_bid > bid['max_bid']:
                    proposed_bid = bid['max_bid']
                
                # Round to avoid floating point issues
                proposed_bid = round(proposed_bid, 2)
                
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
                # HIDDEN BID - reactive only
                increment = get_min_bid_increment(current_bid)
                proposed_bid = current_bid + increment
                if proposed_bid > bid['max_bid']:
                    proposed_bid = bid['max_bid']
                
                proposed_bid = round(proposed_bid, 2)
                
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
        # FIX 2: Validate bot bid is actually higher than current highest
        # ============================================
        if winner['amount'] <= current_bid:
            return jsonify({
                'auto_bid': False, 
                'reason': f'Bot bid ₹{winner["amount"]}Cr not higher than current ₹{current_bid}Cr'
            })
        
        # ============================================
        # FIX 3: Reserve = 20% of (Willing - Purchase), NOT 20% of full willing
        # ============================================
        available = float(winner['purse_limit']) - float(winner['spent'] or 0) - float(winner['reserved'] or 0)
        
        if winner['source'] == 'willing':
            cursor.execute("""
                SELECT 
                    stp.purchase_price,
                    stp.willing_price,
                    ((stp.willing_price - stp.purchase_price) * 0.20) as reserve_amount
                FROM session_team_players stp
                JOIN session_players sp ON sp.id = stp.session_player_id
                WHERE sp.player_id = %s AND stp.team_id = %s
                  AND stp.willing_price IS NOT NULL
                ORDER BY stp.purchased_at DESC
                LIMIT 1
            """, (player_id, winner['team_id']))
            res = cursor.fetchone()
            reserve_amount = float(res['reserve_amount']) if res else 0
            
            if winner['amount'] > available + reserve_amount:
                return jsonify({'auto_bid': False, 'reason': 'Bot insufficient funds'})
        else:
            if winner['amount'] > available:
                return jsonify({'auto_bid': False, 'reason': 'Insufficient funds'})
        
        # Place the auto-bid
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
# ==================== AUCTION STATUS ====================

@bp.route('/auction/status')
def get_status():
    """Get auction status — delegates to shared endpoint for consistency"""
    return shared_status()


# ==================== PLAYERS LIST ====================

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