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
        
        # Willing prices (hidden_max_bids) for this team
        cursor.execute("""
            SELECT h.*, p.player_name, ap.sold_price as final_price
            FROM hidden_max_bids h
            JOIN auction_players ap ON h.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE h.team_id = %s
            ORDER BY h.created_at DESC
        """, (user_team['id'],))
        willing_prices = cursor.fetchall()
        
        # Active willing prices (where player NOT sold to this team)
        cursor.execute("""
            SELECT h.*, p.player_name
            FROM hidden_max_bids h
            JOIN auction_players ap ON h.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE h.team_id = %s AND h.is_active = TRUE AND ap.sold_team_id != %s
            ORDER BY h.max_bid DESC
        """, (user_team['id'], user_team['id']))
        active_willing = cursor.fetchall()
        
        # Category breakdown
        breakdown = {
            'batsmen': [p for p in squad if p['category'] == 'batsman'],
            'bowlers': [p for p in squad if p['category'] == 'bowler'],
            'all_rounders': [p for p in squad if p['category'] == 'all_rounder'],
            'wicket_keepers': [p for p in squad if p['category'] == 'wicket_keeper'],
            'overseas': [p for p in squad if p['overseas']]
        }
        
        # Purse calculations
        purse_limit = float(user_team['purse_limit'] or 100)
        spent = float(user_team['spent'] or 0)
        reserved = float(user_team['reserved'] or 0)
        available = purse_limit - spent - reserved
        
        # Stats
        total_players = len(squad)
        overseas_count = len(breakdown['overseas'])
        overseas_limit = 8  # Can come from auction config if needed
        
        stats = {
            'purse_limit': purse_limit,
            'spent': spent,
            'reserved': reserved,
            'available': available,
            'spent_pct': (spent / purse_limit * 100) if purse_limit > 0 else 0,
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