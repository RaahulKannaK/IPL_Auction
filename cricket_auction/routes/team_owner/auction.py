from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json

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
        # Check owner_id direct match
        if team.get('owner_id') == user_id:
            return team
        # Check owner_ids JSON
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


# ==================== SESSIONS LIST (YOURS vs OTHERS) ====================

@bp.route('/sessions')
def sessions_list():
    """Show Your Sessions (allocated to you) and Other Sessions (view only)"""
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
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify auction exists
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if not auction:
            flash('Auction not found')
            return redirect('/team_owner/dashboard')
        
        # Get user's team in this auction
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        user_team_id = user_team['id'] if user_team else None
        
        # Get all sessions for this auction
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
            # Parse team_ids
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
        
        # Get total teams in auction for willing price logic
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


# ==================== JOIN SESSION (VIEWER MODE) ====================

@bp.route('/session/<int:session_id>/join')
def join_session():
    """Join a session - either as bidder (your session) or viewer (other session)"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    session_id = request.view_args['session_id']
    mode = request.args.get('mode', 'bid')  # 'bid' or 'view'
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
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
        
        # Get user's team
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        user_team_id = user_team['id'] if user_team else None
        
        # Check if user's team is in this session
        session_team_ids = []
        if sess.get('team_ids'):
            try:
                session_team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                session_team_ids = []
        
        is_your_session = user_team_id in session_team_ids if user_team_id else False
        
        # If trying to bid in other session, force view mode
        if mode == 'bid' and not is_your_session:
            mode = 'view'
        
        session['active_session_id'] = session_id
        session['active_auction_id'] = auction_id
        session['session_mode'] = mode  # 'bid' or 'view'
        
    finally:
        cursor.close()
        db.close()
    
    return redirect(f'/team_owner/auction?session={session_id}&mode={mode}')


# ==================== MAIN AUCTION ROOM ====================

@bp.route('/auction')
def auction_room():
    """Team owner auction room — bid or view mode"""
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
    cursor = db.cursor(dictionary=True)
    
    try:
        # Find the auction
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
        
        # MUST HAVE ACTIVE SESSION
        if not active_session_id:
            return redirect(f'/team_owner/sessions?auction_id={auction_id}')
        
        session['active_session_id'] = active_session_id
        
        # LOAD SESSION DETAILS
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
        
        # Get user's team
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        
        # Check if this is user's session
        session_team_ids = []
        if auction_session.get('team_ids'):
            try:
                session_team_ids = json.loads(auction_session['team_ids']) if isinstance(auction_session['team_ids'], str) else auction_session['team_ids']
            except:
                session_team_ids = []
        
        is_your_session = user_team and user_team['id'] in session_team_ids if user_team else False
        
        # Force view mode if not user's session
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
        
        # GET ALL TEAMS SQUAD DATA (for squad view)
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
            # Get squad players for this team
            cursor.execute("""
                SELECT stp.purchase_price, stp.purchase_price as willing_price,
                       p.player_name, p.category, p.overseas, sp.base_price,
                       sp.id as session_player_id
                FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                JOIN players p ON sp.player_id = p.id
                WHERE stp.team_id = %s AND sp.session_id = %s
            """, (team['id'], active_session_id))
            squad_players = cursor.fetchall()
            
            # Get willing prices (only visible to team owner)
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
        
        # Check if willing price popup should show
        show_willing_price_popup = False
        last_sold_player = None
        
        if is_your_session and user_team and auction_session.get('last_sold_team_id') == user_team['id']:
            # Check if this team already set willing price for last sold player
            if auction_session.get('last_sold_session_player_id'):
                cursor.execute("""
                    SELECT willing_price, purchase_price 
                    FROM session_team_players 
                    WHERE team_id = %s AND session_player_id = %s
                """, (user_team['id'], auction_session['last_sold_session_player_id']))
                stp = cursor.fetchone()
                
                # Show popup if willing_price is NULL (not set yet)
                if stp and stp['willing_price'] is None:
                    # Check if total teams > filled teams
                    cursor.execute("""
                        SELECT COUNT(DISTINCT team_id) as filled_teams 
                        FROM session_team_players stp
                        JOIN session_players sp ON stp.session_player_id = sp.id
                        WHERE sp.session_id = %s
                    """, (active_session_id,))
                    filled_result = cursor.fetchone()
                    filled_teams = filled_result['filled_teams'] if filled_result else 0
                    
                    # Only show if not all teams have players
                    total_session_teams = len(session_team_ids)
                    if total_session_teams > filled_teams:
                        show_willing_price_popup = True
                        cursor.execute("""
                            SELECT p.player_name, sp.base_price, stp.purchase_price
                            FROM session_players sp
                            JOIN players p ON sp.player_id = p.id
                            JOIN session_team_players stp ON stp.session_player_id = sp.id
                            WHERE sp.id = %s AND stp.team_id = %s
                        """, (auction_session['last_sold_session_player_id'], user_team['id']))
                        last_sold_player = cursor.fetchone()
        
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
        show_willing_price_popup=show_willing_price_popup,
        last_sold_player=last_sold_player
    )


# ==================== SET WILLING PRICE ====================

@bp.route('/auction/set_willing_price', methods=['POST'])
def set_willing_price():
    """Set willing price for a purchased player"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    session_player_id = data.get('session_player_id')
    team_id = data.get('team_id')
    willing_price = float(data.get('willing_price', 0))
    
    active_session_id = session.get('active_session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team belongs to user
        user_team = get_user_team_by_ids(cursor, session['user_id'], session.get('active_auction_id'))
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'Unauthorized team'}), 403
        
        # Update willing price
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


# ==================== GET ALL TEAMS SQUAD (AJAX) ====================

@bp.route('/auction/teams_squad')
def get_teams_squad():
    """Get all teams squad data for modal"""
    active_session_id = request.args.get('session_id') or session.get('active_session_id')
    
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT t.id, t.team_name, t.owner_id,
                   (SELECT COUNT(*) FROM session_team_players stp 
                    JOIN session_players sp ON stp.session_player_id = sp.id 
                    WHERE stp.team_id = t.id AND sp.session_id = %s) as squad_count
            FROM teams t
            WHERE t.auction_id = (SELECT auction_id FROM auction_sessions WHERE id = %s)
        """, (active_session_id, active_session_id))
        all_teams = cursor.fetchall()
        
        result = []
        for team in all_teams:
            cursor.execute("""
                SELECT stp.purchase_price, stp.willing_price,
                       p.player_name, p.category, p.overseas, sp.base_price
                FROM session_team_players stp
                JOIN session_players sp ON stp.session_player_id = sp.id
                JOIN players p ON sp.player_id = p.id
                WHERE stp.team_id = %s AND sp.session_id = %s
            """, (team['id'], active_session_id))
            squad_players = cursor.fetchall()
            
            team['squad_players'] = squad_players
            result.append(team)
        
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
    
    # Block bidding in view mode
    if session.get('session_mode') == 'view':
        return jsonify({'error': 'View mode - bidding disabled'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = data.get('team_id')
    amount = float(data.get('amount', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team belongs to logged-in user
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only bid for your own team'}), 403
        
        # Verify team is in session
        cursor.execute("SELECT team_ids FROM auction_sessions WHERE id = %s", (active_session_id,))
        sess = cursor.fetchone()
        if not sess:
            return jsonify({'error': 'Session not found'}), 404
        
        session_team_ids = []
        if sess['team_ids']:
            try:
                session_team_ids = json.loads(sess['team_ids']) if isinstance(sess['team_ids'], str) else sess['team_ids']
            except:
                pass
        
        if team_id not in session_team_ids:
            return jsonify({'error': 'Team not in this session'}), 403
        
        # Check if player was skipped by this team
        cursor.execute("""
            SELECT * FROM session_skips 
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        existing_skip = cursor.fetchone()
        if existing_skip:
            return jsonify({'error': 'You skipped this player. Cannot bid.'}), 400
        
        # Check last bid in this session
        cursor.execute("""
            SELECT team_id FROM session_bids 
            WHERE session_id = %s AND session_player_id = %s 
            ORDER BY bid_amount DESC, created_at DESC LIMIT 1
        """, (active_session_id, session_player_id))
        last_bid = cursor.fetchone()
        if last_bid and last_bid['team_id'] == team_id:
            return jsonify({'error': 'You are already the highest bidder.'}), 400
        
        cursor.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
        team = cursor.fetchone()
        if not team:
            return jsonify({'error': 'Team not found'}), 404
        
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        if auction['status'] != 'live':
            return jsonify({'error': 'Auction not live'}), 400
        
        # Get base price from session_players
        cursor.execute("SELECT base_price FROM session_players WHERE id = %s", (session_player_id,))
        sp_row = cursor.fetchone()
        base_price = float(sp_row['base_price']) if sp_row else 2.0
        
        # Check highest bid in this session
        cursor.execute("""
            SELECT COUNT(*) as bid_count, MAX(bid_amount) as highest_bid
            FROM session_bids 
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
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
                return jsonify({'error': f'Bid must be at least ₹{min_increment:.2f}Cr higher'}), 400
        
        # Check funds
        available = float(team['purse_limit']) - float(team['spent'] or 0) - float(team['reserved'] or 0)
        if amount > available:
            return jsonify({'error': f'Insufficient funds. Available: ₹{available:.2f}Cr'}), 400
        
        # Insert into session_bids
        cursor.execute("""
            INSERT INTO session_bids (session_id, session_player_id, team_id, bid_amount) 
            VALUES (%s, %s, %s, %s)
        """, (active_session_id, session_player_id, team_id, amount))
        
        # Update auction_sessions with current bid
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_bid = %s, current_bidder_id = %s
            WHERE id = %s
        """, (amount, team_id, active_session_id))
        
        db.commit()
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
    """Team owner skips a player — cannot bid on them later"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Block skip in view mode
    if session.get('session_mode') == 'view':
        return jsonify({'error': 'View mode - skip disabled'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = data.get('team_id')
    reason = data.get('reason', 'manual_skip')
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team belongs to logged-in user
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        if not user_team or user_team['id'] != team_id:
            return jsonify({'error': 'You can only skip for your own team'}), 403
        
        # Check if already skipped
        cursor.execute("""
            SELECT * FROM session_skips 
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        existing = cursor.fetchone()
        if existing:
            return jsonify({'error': 'Already skipped this player'}), 400
        
        # Record skip
        cursor.execute("""
            INSERT INTO session_skips (session_id, session_player_id, team_id, skipped_by, reason)
            VALUES (%s, %s, %s, %s, %s)
        """, (active_session_id, session_player_id, team_id, session['user_id'], reason))
        
        # Count total skips for this player
        cursor.execute("""
            SELECT COUNT(DISTINCT team_id) as skip_count
            FROM session_skips
            WHERE session_id = %s AND session_player_id = %s
        """, (active_session_id, session_player_id))
        skip_result = cursor.fetchone()
        
        # Count total teams in session
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
    """Hidden max bid — team owner only for their own team"""
    if not session.get('user_id'):
        return jsonify({'error': 'Not logged in'}), 401
    
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Block hidden bid in view mode
    if session.get('session_mode') == 'view':
        return jsonify({'error': 'View mode - hidden bids disabled'}), 403
    
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    team_id = data.get('team_id')
    max_amount = float(data.get('max_amount', 0))
    
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        # Verify team belongs to logged-in user
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
        
        # Remove old hidden bid for this player+team
        cursor.execute("""
            DELETE FROM session_hidden_max_bids
            WHERE session_id = %s AND session_player_id = %s AND team_id = %s
        """, (active_session_id, session_player_id, team_id))
        
        # Insert new hidden max bid
        cursor.execute("""
            INSERT INTO session_hidden_max_bids (session_id, session_player_id, team_id, max_bid, is_active) 
            VALUES (%s, %s, %s, %s, TRUE)
        """, (active_session_id, session_player_id, team_id, max_amount))
        
        # Update purse reservation
        cursor.execute("""
            DELETE FROM session_purse_reservations
            WHERE session_player_id = %s AND team_id = %s AND status = 'active'
        """, (session_player_id, team_id))
        
        cursor.execute("""
            INSERT INTO session_purse_reservations (session_id, session_player_id, team_id, reserved_amount, status)
            VALUES (%s, %s, %s, %s, 'active')
        """, (active_session_id, session_player_id, team_id, max_amount))
        
        # Update team reserved amount
        cursor.execute("""
            UPDATE teams SET reserved = reserved + %s WHERE id = %s
        """, (max_amount, team_id))
        
        db.commit()
    finally:
        cursor.close()
        db.close()
    
    return jsonify({'success': True, 'reserved': max_amount})


# ==================== AUTO BID ====================

@bp.route('/auction/auto_bid', methods=['POST'])
def auto_counter_bid():
    """Auto bid — session-scoped"""
    data = request.get_json()
    auction_id = data.get('auction_id')
    session_player_id = data.get('session_player_id')
    current_bid = float(data.get('current_bid', 0))
    active_session_id = session.get('active_session_id') or data.get('session_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT h.*, t.team_name, t.purse_limit, t.spent, t.reserved 
            FROM session_hidden_max_bids h 
            JOIN teams t ON h.team_id = t.id 
            WHERE h.session_player_id = %s AND h.is_active = TRUE AND h.max_bid > %s
            ORDER BY h.max_bid DESC
        """, (session_player_id, current_bid))
        
        hidden_bids = cursor.fetchall()
        
        if not hidden_bids:
            return jsonify({'auto_bid': False})
        
        winner = hidden_bids[0]
        increment = get_min_bid_increment(current_bid)
        next_bid = current_bid + increment
        if next_bid > winner['max_bid']:
            next_bid = winner['max_bid']
        
        cursor.execute("""
            SELECT reserved_amount FROM session_purse_reservations 
            WHERE session_player_id = %s AND team_id = %s AND status = 'active'
        """, (session_player_id, winner['team_id']))
        res = cursor.fetchone()
        reserved_amount = float(res['reserved_amount']) if res else 0
        
        available = float(winner['purse_limit']) - float(winner['spent'] or 0) - (float(winner['reserved'] or 0) - reserved_amount)
        
        if next_bid > available + reserved_amount:
            return jsonify({'auto_bid': False, 'reason': 'Insufficient funds'})
        
        cursor.execute("""
            INSERT INTO session_bids (session_id, session_player_id, team_id, bid_amount) 
            VALUES (%s, %s, %s, %s)
        """, (active_session_id, session_player_id, winner['team_id'], next_bid))
        
        cursor.execute("""
            UPDATE auction_sessions 
            SET current_bid = %s, current_bidder_id = %s
            WHERE id = %s
        """, (next_bid, winner['team_id'], active_session_id))
        
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


# ==================== STATUS POLLING ====================

@bp.route('/auction/status')
def get_status():
    """Get auction status — session-scoped"""
    auction_id = request.args.get('auction_id')
    team_id = request.args.get('team_id', type=int)
    active_session_id = request.args.get('session_id') or session.get('active_session_id')
    
    cache_key = f'auction:status:{auction_id or "active"}:{active_session_id or "no_session"}'
    
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
                'session_id': active_session_id
            }
            
            # Get session-specific state
            if active_session_id:
                cursor.execute("""
                    SELECT current_player_id, current_bid, current_bidder_id
                    FROM auction_sessions WHERE id = %s
                """, (active_session_id,))
                sess = cursor.fetchone()
                
                if sess:
                    result['current_bid'] = float(sess.get('current_bid') or 0)
                    result['current_bidder_id'] = sess.get('current_bidder_id')
                    result['current_bidder'] = None
                    result['current_player'] = None
                    result['player_category'] = None
                    result['base_price'] = 0
                    result['session_player_id'] = None
                    result['overseas'] = False
                    result['has_bids'] = False
                    result['skip_count'] = 0
                    result['total_teams'] = 0
                    result['all_skipped'] = False
                    
                    # Check for willing price popup trigger
                    result['show_willing_price'] = False
                    
                    if sess.get('current_bidder_id'):
                        cursor.execute("SELECT team_name FROM teams WHERE id = %s", (sess['current_bidder_id'],))
                        bidder = cursor.fetchone()
                        if bidder:
                            result['current_bidder'] = bidder['team_name']
                    
                    session_player_id = sess.get('current_player_id')
                    if session_player_id:
                        cursor.execute("""
                            SELECT p.player_name, p.category, p.overseas, sp.base_price, sp.id as session_player_id
                            FROM session_players sp
                            JOIN players p ON sp.player_id = p.id
                            WHERE sp.id = %s
                        """, (session_player_id,))
                        player = cursor.fetchone()
                        if player:
                            result['current_player'] = player['player_name']
                            result['player_category'] = player['category']
                            result['base_price'] = float(player['base_price'])
                            result['session_player_id'] = player['session_player_id']
                            result['overseas'] = player.get('overseas', False)
                        
                        cursor.execute("""
                            SELECT COUNT(*) as bid_count FROM session_bids 
                            WHERE session_id = %s AND session_player_id = %s
                        """, (active_session_id, session_player_id))
                        bid_result = cursor.fetchone()
                        result['has_bids'] = bid_result['bid_count'] > 0 if bid_result else False
                        
                        cursor.execute("""
                            SELECT COUNT(DISTINCT team_id) as skip_count
                            FROM session_skips
                            WHERE session_id = %s AND session_player_id = %s
                        """, (active_session_id, session_player_id))
                        skip_result = cursor.fetchone()
                        result['skip_count'] = skip_result['skip_count'] if skip_result else 0
                        
                        # Count teams in session
                        cursor.execute("SELECT team_ids FROM auction_sessions WHERE id = %s", (active_session_id,))
                        team_data = cursor.fetchone()
                        if team_data and team_data['team_ids']:
                            try:
                                team_ids = json.loads(team_data['team_ids']) if isinstance(team_data['team_ids'], str) else team_data['team_ids']
                                result['total_teams'] = len(team_ids)
                            except:
                                result['total_teams'] = 0
                        
                        result['all_skipped'] = result['skip_count'] >= result['total_teams'] and result['total_teams'] > 0
            
            return result
            
        finally:
            cursor.close()
            db.close()
    
    result = get_cached(cache_key, fetch_status, ttl_seconds=0)
    return jsonify(result)


# ==================== PLAYERS LIST (for refresh) ====================

@bp.route('/auction/players')
def get_players():
    """Get available players for current session"""
    auction_id = request.args.get('auction_id')
    active_session_id = request.args.get('session_id') or session.get('active_session_id')
    
    if not active_session_id:
        return jsonify({'error': 'No active session'}), 400
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
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