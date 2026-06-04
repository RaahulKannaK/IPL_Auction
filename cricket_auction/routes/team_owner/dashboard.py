from flask import Blueprint, render_template, session, flash, redirect, jsonify
import mysql.connector

bp = Blueprint('team_owner_dashboard', __name__, url_prefix='/team-owner')

def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='raahul@185',
        database='cricket_auction'
    )

@bp.route('/dashboard')
def dashboard():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Get ALL teams this user owns, with auction info
    cursor.execute("""
        SELECT t.*, a.league_name, a.status as auction_status, a.id as auction_id
        FROM teams t
        JOIN auctions a ON t.auction_id = a.id
        WHERE t.owner_id = %s
        ORDER BY a.created_at DESC
    """, (session['user_id'],))
    teams = cursor.fetchall()
    
    # For each team, get quick stats
    for team in teams:
        # Squad count - fetch and consume all results
        cursor.execute("""
            SELECT COUNT(*) as cnt 
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            WHERE tp.team_id = %s AND ap.auction_id = %s
        """, (team['id'], team['auction_id']))
        result = cursor.fetchone()
        team['squad_count'] = result['cnt'] if result else 0
        
        # Available purse
        spent = float(team['spent'] or 0)
        reserved = float(team['reserved'] or 0)
        team['available'] = float(team['purse_limit']) - spent - reserved
        
        # Category breakdown - fetch and consume all results
        cursor.execute("""
            SELECT p.category, COUNT(*) as cnt
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE tp.team_id = %s AND ap.auction_id = %s
            GROUP BY p.category
        """, (team['id'], team['auction_id']))
        categories = {row['category']: row['cnt'] for row in cursor.fetchall()}
        team['batsmen'] = categories.get('batsman', 0)
        team['bowlers'] = categories.get('bowler', 0)
        team['all_rounders'] = categories.get('all_rounder', 0)
        team['wicket_keepers'] = categories.get('wicket_keeper', 0)
        
        # Overseas count - separate query, fetch all results
        cursor.execute("""
            SELECT COUNT(*) as cnt
            FROM team_players tp
            JOIN auction_players ap ON tp.auction_player_id = ap.id
            JOIN players p ON ap.player_id = p.id
            WHERE tp.team_id = %s AND ap.auction_id = %s AND p.overseas = TRUE
        """, (team['id'], team['auction_id']))
        result = cursor.fetchone()
        team['overseas'] = result['cnt'] if result else 0
    
    cursor.close()
    db.close()
    
    return render_template('team_owner/dashboard.html', teams=teams)

@bp.route('/enter-auction/<int:auction_id>')
def enter_auction(auction_id):
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    # Verify this user owns a team in this auction
    cursor.execute("""
        SELECT t.*, a.league_name 
        FROM teams t
        JOIN auctions a ON t.auction_id = a.id
        WHERE t.auction_id = %s AND t.owner_id = %s
    """, (auction_id, session['user_id']))
    team = cursor.fetchone()
    cursor.close()
    db.close()
    
    if not team:
        flash('You do not own a team in this auction')
        return redirect('/team-owner/dashboard')
    
    # Set auction context in session
    session['active_auction_id'] = auction_id
    session['active_team_id'] = team['id']
    session['active_league_name'] = team['league_name']
    
    return redirect('/team-owner/auction')

@bp.route('/exit-auction')
def exit_auction():
    session.pop('active_auction_id', None)
    session.pop('active_team_id', None)
    session.pop('active_league_name', None)
    return redirect('/team-owner/dashboard')