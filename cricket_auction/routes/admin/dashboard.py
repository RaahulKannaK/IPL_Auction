from flask import Blueprint, render_template, request, redirect, session, flash, jsonify
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('admin_auction', __name__, url_prefix='/admin')

def get_user_team(cursor, user_id, auction_id=None):
    """Get team owned by user"""
    if auction_id:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s AND auction_id = %s", (user_id, auction_id))
    else:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    return cursor.fetchone()

def get_min_bid_increment(current_bid):
    if current_bid < 1.0:
        return 0.05
    elif current_bid < 2.0:
        return 0.10
    elif current_bid < 7.0:
        return 0.25
    else:
        return 0.25


# ==================== MAIN AUCTION ROOM (SESSION-SCOPED) ====================

@bp.route('/auction')
def auction_room():
    """Main auction room — REQUIRES active session"""
    if not session.get('user_id'):
        return redirect('/')
    
    if session.get('role') not in ['admin', 'auctioneer', 'team_owner']:
        flash('Unauthorized')
        return redirect('/')
    
    # FIXED: Accept session from URL param OR flask session
    url_session_id = request.args.get('session', type=int)
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
            return redirect('/admin/')
        
        auction_id = auction['id']
        session['active_auction_id'] = auction_id
        
        # FIXED: MUST HAVE ACTIVE SESSION
        if not active_session_id:
            # Check if any active session exists for this auction
            cursor.execute("""
                SELECT * FROM auction_sessions 
                WHERE auction_id = %s AND status IN ('active', 'paused')
                ORDER BY created_at DESC LIMIT 1
            """, (auction_id,))
            existing_session = cursor.fetchone()
            
            if not existing_session:
                if session.get('role') in ['admin', 'auctioneer']:
                    flash('⚠️ No session created yet. Create a session first.')
                    return redirect(f'/admin/sessions?auction={auction_id}')
                else:
                    flash('⏳ No active session available.')
                    return redirect('/admin/')
            
            # Auto-join team owners; admin must explicitly pick
            if session.get('role') == 'team_owner':
                session['active_session_id'] = existing_session['id']
                active_session_id = existing_session['id']
            else:
                flash('Select a session to enter.')
                return redirect(f'/admin/sessions?auction={auction_id}')
        
        # Store the active session (from URL or auto-selected)
        session['active_session_id'] = active_session_id
        
        # === LOAD SESSION DETAILS ===
        cursor.execute("""
            SELECT s.*, a.league_name, a.status as auction_status, a.squad_size, 
                   a.purse_limit, a.overseas_limit
            FROM auction_sessions s
            JOIN auctions a ON s.auction_id = a.id
            WHERE s.id = %s
        """, (active_session_id,))
        auction_session = cursor.fetchone()
        
        if not auction_session:
            session.pop('active_session_id', None)
            flash('Session expired. Please select again.')
            return redirect(f'/admin/sessions?auction={auction_id}')
        
        # === GET SESSION TEAMS ===
        session_team_ids = []
        if auction_session.get('team_ids'):
            try:
                session_team_ids = json.loads(auction_session['team_ids']) if isinstance(auction_session['team_ids'], str) else auction_session['team_ids']
                session_team_ids = [int(tid) for tid in session_team_ids]  # Ensure integers
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
        
        # Get user team (for team owners)
        user_team = None
        if session.get('role') == 'team_owner':
            user_team = get_user_team(cursor, session['user_id'], auction_id)
            if user_team and int(user_team['id']) not in session_team_ids:
                flash('Your team is not part of this session')
                return redirect('/admin/')
        
        # === GET SESSION PLAYERS ===
        cursor.execute("""
            SELECT sp.id as session_player_id, sp.base_price, sp.status, 
                   sp.sold_price, sp.sold_team_id, sp.skip_reason, sp.skip_notes,
                   p.id as player_id, p.player_name, p.category, p.overseas
            FROM session_players sp
            JOIN players p ON sp.player_id = p.id
            WHERE sp.session_id = %s AND sp.status IN ('available', 'unsold')
            ORDER BY RAND()
        """, (active_session_id,))
        players = cursor.fetchall()
        
        # === CURRENT PLAYER (from session state) ===
        current_player = None
        current_bid = 0
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
            
            # Check bids from session_bids
            cursor.execute("""
                SELECT COUNT(*) as bid_count FROM session_bids 
                WHERE session_id = %s AND session_player_id = %s
            """, (active_session_id, auction_session['current_player_id']))
            bid_result = cursor.fetchone()
            has_bids = bid_result['bid_count'] > 0 if bid_result else False
            
            # Skip votes from session_skips
            cursor.execute("""
                SELECT ss.*, t.team_name, u.username as skipped_by_name
                FROM session_skips ss
                JOIN teams t ON ss.team_id = t.id
                JOIN users u ON ss.skipped_by = u.id
                WHERE ss.session_id = %s AND ss.session_player_id = %s
                ORDER BY ss.skipped_at DESC
            """, (active_session_id, auction_session['current_player_id']))
            skip_votes = cursor.fetchall()
            
            all_skipped = len(skip_votes) >= total_teams and total_teams > 0
        
        # === GET ALL SESSIONS FOR NAVIGATION ===
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
                    sess['team_ids_list'] = [int(tid) for tid in sess['team_ids_list']]
                except:
                    sess['team_ids_list'] = []
            else:
                sess['team_ids_list'] = []
            sess['team_count'] = len(sess['team_ids_list'])
        
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
        has_bids=has_bids,
        skip_votes=skip_votes,
        total_teams=total_teams,
        all_skipped=all_skipped
    )