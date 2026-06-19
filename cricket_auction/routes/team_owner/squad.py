from flask import Blueprint, render_template, session, flash, redirect, jsonify
from database.db import get_db

bp = Blueprint('team_owner_squad', __name__, url_prefix='/team-owner/squad')

def get_user_team(cursor, user_id):
    cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    return cursor.fetchone()

@bp.route('/')
def view_squad():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        user_team = get_user_team(cursor, session['user_id'])
        if not user_team:
            flash('No team assigned')
            return redirect('/dashboard')
        
        # Squad with purchase details
        cursor.execute("""
            SELECT p.*, tp.purchase_price as sold_price, tp.purchased_at, ap.id as auction_player_id
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE tp.team_id = %s
            ORDER BY tp.purchase_price DESC
        """, (user_team['id'],))
        squad = cursor.fetchall()
        
        # Willing prices (hidden_max_bids) for this team - ALL history
        cursor.execute("""
            SELECT h.*, p.player_name, ap.sold_price as final_price, ap.sold_team_id
            FROM hidden_max_bids h
            JOIN auction_players ap ON h.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE h.team_id = %s
            ORDER BY h.created_at DESC
        """, (user_team['id'],))
        willing_prices = cursor.fetchall()
        
        # Active willing prices (player not yet sold OR sold to another team - still active protection)
        cursor.execute("""
            SELECT h.*, p.player_name, ap.sold_price as final_price, ap.sold_team_id
            FROM hidden_max_bids h
            JOIN auction_players ap ON h.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE h.team_id = %s AND h.is_active = TRUE
            ORDER BY h.max_bid DESC
        """, (user_team['id'],))
        active_willing = cursor.fetchall()
        
        # Category breakdown
        breakdown = {
            'batsmen': [p for p in squad if p['category'] == 'batsman'],
            'bowlers': [p for p in squad if p['category'] == 'bowler'],
            'all_rounders': [p for p in squad if p['category'] == 'all_rounder'],
            'wicket_keepers': [p for p in squad if p['category'] == 'wicket_keeper'],
            'overseas': [p for p in squad if p['overseas']]
        }
        
        # FIXED PURSE CALCULATIONS
        # Logic:
        # - spent = actual money spent on won players (informational only)
        # - reserved = sum of all active willing prices (max auto-bid limits)
        #   This is the ONLY deduction from purse. Spent is INCLUDED in reserved.
        # - available = purse_limit - reserved
        #
        # Example: Russell won at 6Cr, willing price 9Cr
        #   reserved = 9Cr (this 9Cr includes the 6Cr already spent)
        #   spent = 6Cr (just for display - shows what you actually paid)
        #   available = 100 - 9 = 91Cr ✓
        
        purse_limit = float(user_team['purse_limit'] or 100)
        spent = float(user_team['spent'] or 0)
        
        # Calculate reserved from active willing prices
        cursor.execute("""
            SELECT COALESCE(SUM(max_bid), 0) as total_reserved
            FROM hidden_max_bids
            WHERE team_id = %s AND is_active = TRUE
        """, (user_team['id'],))
        reserved_result = cursor.fetchone()
        reserved = float(reserved_result['total_reserved'] or 0) if reserved_result else 0
        
        # FIXED: Available = Purse - Reserved (NOT purse - spent - reserved)
        # Spent is already inside reserved, don't double count
        available = purse_limit - reserved
        
        # Stats
        total_players = len(squad)
        overseas_count = len(breakdown['overseas'])
        overseas_limit = 8
        
        stats = {
            'purse_limit': purse_limit,
            'spent': spent,
            'reserved': reserved,
            'available': available,
            'spent_pct': (spent / purse_limit * 100) if purse_limit > 0 else 0,
            'reserved_pct': (reserved / purse_limit * 100) if purse_limit > 0 else 0,
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
        active_willing=active_willing
    )