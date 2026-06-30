from flask import Blueprint, render_template, session, flash, redirect, jsonify
from database.db import get_db
import json

bp = Blueprint('team_owner_squad', __name__, url_prefix='/team-owner/squad')

def get_user_team_by_ids(cursor, user_id, auction_id=None):
    """Get team where user is owner (supports owner_ids JSON)"""
    if auction_id:
        cursor.execute("SELECT * FROM teams WHERE auction_id = %s", (auction_id,))
    else:
        cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    
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


@bp.route('/')
def view_squad():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    # FIX: Must have active_auction_id set via dashboard
    auction_id = session.get('active_auction_id')
    if not auction_id:
        flash('Please select an auction from the dashboard first')
        return redirect('/team-owner/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        user_team = get_user_team_by_ids(cursor, session['user_id'], auction_id)
        
        if not user_team:
            flash('No team assigned to this auction')
            return redirect('/team-owner/dashboard')
        
        team_id = user_team['id']
        
        # ============================================
        # GET SQUAD: All players bought across ALL sessions for THIS auction
        # ============================================
        cursor.execute("""
            SELECT 
                p.id as player_id,
                p.player_name,
                p.category,
                p.overseas,
                sp.id as session_player_id,
                sp.session_id,
                s.session_name,
                stp.purchase_price as sold_price,
                stp.willing_price,
                stp.purchased_at
            FROM session_team_players stp
            JOIN session_players sp ON stp.session_player_id = sp.id
            JOIN players p ON sp.player_id = p.id
            JOIN auction_sessions s ON sp.session_id = s.id
            WHERE stp.team_id = %s
            ORDER BY stp.purchased_at DESC
        """, (team_id,))
        squad = cursor.fetchall()
        
        # ============================================
        # GET WILLING PRICES: Players with willing price set
        # Reserve = 20% of (WILLING - PURCHASE) — DIFFERENCE ONLY
        # ============================================
        cursor.execute("""
            SELECT 
                p.player_name,
                p.category,
                stp.purchase_price as sold_price,
                stp.willing_price,
                ((stp.willing_price - stp.purchase_price) * 0.20) as reserve_amount,
                s.session_name as bought_session,
                stp.purchased_at
            FROM session_team_players stp
            JOIN session_players sp ON stp.session_player_id = sp.id
            JOIN players p ON sp.player_id = p.id
            JOIN auction_sessions s ON sp.session_id = s.id
            WHERE stp.team_id = %s
              AND stp.willing_price IS NOT NULL
              AND stp.willing_price > stp.purchase_price
            ORDER BY stp.purchased_at DESC
        """, (team_id,))
        willing_prices = cursor.fetchall()
        
        # ============================================
        # GET PENDING POPUPS
        # ============================================
        cursor.execute("""
            SELECT 
                pwp.player_name,
                pwp.purchase_price,
                pwp.created_at
            FROM pending_willing_price pwp
            WHERE pwp.team_id = %s
              AND pwp.popup_shown = 0
            ORDER BY pwp.created_at DESC
        """, (team_id,))
        pending_popups = cursor.fetchall()
        
        # ============================================
        # CATEGORY BREAKDOWN
        # ============================================
        breakdown = {
            'batsmen': [p for p in squad if p['category'] == 'batsman'],
            'bowlers': [p for p in squad if p['category'] == 'bowler'],
            'all_rounders': [p for p in squad if p['category'] == 'all_rounder'],
            'wicket_keepers': [p for p in squad if p['category'] == 'wicket_keeper'],
            'overseas': [p for p in squad if p['overseas']]
        }
        
        # ============================================
        # PURSE CALCULATIONS — RESERVE = 20% OF (WILLING - PURCHASE)
        # ============================================
        purse_limit = float(user_team['purse_limit'] or 100)
        
        # Total spent = sum of all purchase prices
        cursor.execute("""
            SELECT COALESCE(SUM(purchase_price), 0) as total_spent
            FROM session_team_players
            WHERE team_id = %s
        """, (team_id,))
        spent_result = cursor.fetchone()
        total_spent = float(spent_result['total_spent'] or 0) if spent_result else 0
        
        # Reserved = 20% of (willing_price - purchase_price) for all players with willing price set
        cursor.execute("""
            SELECT COALESCE(SUM((willing_price - purchase_price) * 0.20), 0) as total_reserved
            FROM session_team_players
            WHERE team_id = %s 
              AND willing_price IS NOT NULL
              AND willing_price > purchase_price
        """, (team_id,))
        reserved_result = cursor.fetchone()
        total_reserved = float(reserved_result['total_reserved'] or 0) if reserved_result else 0
        
        # Also add active hidden max bids from current session
        cursor.execute("""
            SELECT COALESCE(SUM(max_bid), 0) as total_hidden
            FROM session_hidden_max_bids
            WHERE team_id = %s AND is_active = TRUE
        """, (team_id,))
        hidden_result = cursor.fetchone()
        total_hidden = float(hidden_result['total_hidden'] or 0) if hidden_result else 0
        
        total_reserved = total_reserved + total_hidden
        
        # Available = Purse - Spent - Reserved
        available = purse_limit - total_spent - total_reserved
        
        total_players = len(squad)
        overseas_count = len(breakdown['overseas'])
        overseas_limit = 8
        
        stats = {
            'purse_limit': purse_limit,
            'spent': total_spent,
            'reserved': total_reserved,
            'available': available,
            'spent_pct': (total_spent / purse_limit * 100) if purse_limit > 0 else 0,
            'reserved_pct': (total_reserved / purse_limit * 100) if purse_limit > 0 else 0,
            'total_players': total_players,
            'overseas_count': overseas_count,
            'overseas_limit': overseas_limit,
            'overseas_pct': (overseas_count / overseas_limit * 100) if overseas_limit > 0 else 0
        }
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/squad.html',
        squad=squad,
        breakdown=breakdown,
        team=user_team,
        stats=stats,
        willing_prices=willing_prices,
        pending_popups=pending_popups
    )