from flask import Blueprint, render_template, request, redirect, session, flash, jsonify, url_for, g
from functools import wraps
from database.db import get_db          # <-- ADD THIS LINE
from functools import wraps

bp = Blueprint('team_owner', __name__, url_prefix='/team-owner')

# ============================================================
# AUCTION REQUIRED DECORATOR
# ============================================================

def auction_required(f):
    """Decorator: redirect to dashboard if no active auction is selected."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('active_auction_id'):
            flash('⚡ No active auction selected. Please enter an Auction ID first.')
            return redirect('/team-owner/dashboard')
        return f(*args, **kwargs)
    return decorated_function

# ============================================================
# DASHBOARD - NO AUCTION REQUIRED (entry point)
# ============================================================

@bp.route('/dashboard')
def dashboard():
    """Team owner dashboard - shows their teams across all auctions."""
    if session.get('role') not in ['team_owner', 'owner']:
        flash('Unauthorized access')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # Get all teams owned by this user with auction info
        cursor.execute("""
            SELECT 
                t.*,
                a.id as auction_id,
                a.league_name,
                a.status as auction_status,
                a.purse_limit,
                (SELECT COUNT(*) FROM players WHERE team_id = t.id) as squad_count,
                (SELECT COUNT(*) FROM auction_sessions 
                 WHERE auction_id = a.id AND status IN ('active','paused')) as your_sessions_count
            FROM teams t
            JOIN auctions a ON t.auction_id = a.id
            WHERE t.owner_id = %s
            ORDER BY a.created_at DESC
        """, (session['user_id'],))
        teams = cursor.fetchall()
        
        # Calculate derived stats per team
        for team in teams:
            cursor.execute("""
                SELECT 
                    SUM(CASE WHEN role = 'Batsman' THEN 1 ELSE 0 END) as batsmen,
                    SUM(CASE WHEN role = 'Bowler' THEN 1 ELSE 0 END) as bowlers,
                    SUM(CASE WHEN role = 'All-Rounder' THEN 1 ELSE 0 END) as all_rounders,
                    SUM(CASE WHEN role = 'Wicket-Keeper' THEN 1 ELSE 0 END) as wicket_keepers,
                    SUM(CASE WHEN is_overseas = 1 THEN 1 ELSE 0 END) as overseas,
                    COALESCE(SUM(bought_for), 0) as spent
                FROM players WHERE team_id = %s
            """, (team['id'],))
            stats = cursor.fetchone()
            team.update(stats)
            team['available'] = float(team['purse_limit'] or 0) - float(stats['spent'] or 0)
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/dashboard.html', teams=teams)


# ============================================================
# ENTER AUCTION - SETS SESSION, THEN REDIRECTS TO SESSIONS
# ============================================================

@bp.route('/enter-auction/<int:auction_id>')
def enter_auction(auction_id):
    """Enter auction room - sets session and redirects to sessions page."""
    if session.get('role') not in ['team_owner', 'owner']:
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            flash('Auction not found')
            return redirect('/team-owner/dashboard')
        
        # Set active auction in session
        session['active_auction_id'] = auction_id
        session['active_league_name'] = auction['league_name']
        
        # Clear any stale session/team data
        session.pop('active_session_id', None)
        session.pop('active_team_id', None)
        
        flash(f'Entered auction: {auction["league_name"]}')
        # Redirect to sessions page to choose session
        return redirect('/team_owner/sessions?auction_id=' + str(auction_id))
        
    finally:
        cursor.close()
        db.close()


@bp.route('/exit-auction')
def exit_auction():
    """Exit auction - clear all active auction/session data."""
    session.pop('active_auction_id', None)
    session.pop('active_session_id', None)
    session.pop('active_team_id', None)
    session.pop('active_league_name', None)
    flash('Exited auction')
    return redirect('/team-owner/dashboard')


# ============================================================
# PROTECTED ROUTES - REQUIRE ACTIVE AUCTION
# ============================================================

@bp.route('/squad')
@auction_required
def squad():
    """My Squad - only accessible after entering auction ID."""
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # Get the team for this user in the active auction
        cursor.execute("""
            SELECT t.* FROM teams t
            WHERE t.owner_id = %s AND t.auction_id = %s
        """, (session['user_id'], session['active_auction_id']))
        team = cursor.fetchone()
        
        if not team:
            flash('You do not have a team in this auction')
            return redirect('/team-owner/dashboard')
        
        session['active_team_id'] = team['id']
        
        # Get squad players
        cursor.execute("""
            SELECT * FROM players 
            WHERE team_id = %s 
            ORDER BY bought_at DESC
        """, (team['id'],))
        players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/squad.html', team=team, players=players)


@bp.route('/playing11')
@auction_required
def playing11():
    """Playing XI - only accessible after entering auction ID."""
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("""
            SELECT t.* FROM teams t
            WHERE t.owner_id = %s AND t.auction_id = %s
        """, (session['user_id'], session['active_auction_id']))
        team = cursor.fetchone()
        
        if not team:
            flash('You do not have a team in this auction')
            return redirect('/team-owner/dashboard')
        
        session['active_team_id'] = team['id']
        
        cursor.execute("""
            SELECT * FROM players 
            WHERE team_id = %s 
            ORDER BY role, name
        """, (team['id'],))
        players = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/playing11.html', team=team, players=players)


@bp.route('/auction')
@auction_required
def auction_room():
    """Auction Room - only accessible after entering auction ID."""
    # Also require active session
    if not session.get('active_session_id'):
        flash('Please select a session first')
        return redirect('/team_owner/sessions?auction_id=' + str(session['active_auction_id']))
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("""
            SELECT t.* FROM teams t
            WHERE t.owner_id = %s AND t.auction_id = %s
        """, (session['user_id'], session['active_auction_id']))
        team = cursor.fetchone()
        
        if not team:
            flash('You do not have a team in this auction')
            return redirect('/team-owner/dashboard')
        
        session['active_team_id'] = team['id']
        
        # Get auction state for this session
        cursor.execute("""
            SELECT * FROM auction_sessions 
            WHERE id = %s AND auction_id = %s
        """, (session['active_session_id'], session['active_auction_id']))
        auction_session = cursor.fetchone()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('team_owner/auction.html', team=team, session=auction_session)