from flask import Blueprint, render_template, request, redirect, session, flash, jsonify, url_for
from database.db import get_db, get_cached, clear_cache
import json

bp = Blueprint('admin_dashboard', __name__, url_prefix='/admin')

@bp.route('/')
def admin_panel():
    """Main dashboard - shows create/join auction options"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        flash('Unauthorized access')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        # All auctions for join list
        cursor.execute("""
            SELECT a.*, 
                   (SELECT COUNT(*) FROM teams WHERE auction_id = a.id) as team_count,
                   (SELECT COUNT(*) FROM auction_sessions WHERE auction_id = a.id) as session_count
            FROM auctions a
            ORDER BY a.created_at DESC
            LIMIT 10
        """)
        recent_auctions = cursor.fetchall()
        
        # Live/paused auctions
        cursor.execute("""
            SELECT a.*, 
                   (SELECT COUNT(*) FROM teams WHERE auction_id = a.id) as team_count
            FROM auctions a
            WHERE a.status IN ('live', 'paused')
            ORDER BY a.created_at DESC
        """)
        active_auctions = cursor.fetchall()
        
    finally:
        cursor.close()
        db.close()
    
    return render_template('admin/dashboard.html',
        recent_auctions=recent_auctions,
        active_auctions=active_auctions
    )

@bp.route('/dashboard')
def admin_dashboard():
    """Redirect to main panel"""
    return redirect('/admin/')

@bp.route('/enter-auction/<int:auction_id>')
def enter_auction(auction_id):
    """Enter auction room - sets session and redirects to auction room"""
    if session.get('role') not in ['team_owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("SELECT * FROM auctions WHERE id = %s", (auction_id,))
        auction = cursor.fetchone()
        
        if not auction:
            flash('Auction not found')
            return redirect('/admin/')
        
        # FIXED: Set active auction
        session['active_auction_id'] = auction_id
        session['active_league_name'] = auction['league_name']
        
        # FIXED: Check if there's an active session for this auction
        # If only one active session exists, auto-select it
        # Otherwise redirect to sessions page to choose
        cursor.execute("""
            SELECT * FROM auction_sessions 
            WHERE auction_id = %s AND status IN ('active', 'paused')
            ORDER BY created_at DESC
        """, (auction_id,))
        sessions = cursor.fetchall()
        
        if len(sessions) == 1:
            # Auto-select the only active session
            session['active_session_id'] = sessions[0]['id']
            flash(f'Entered auction: {auction["league_name"]} - {sessions[0]["session_name"]}')
            return redirect('/admin/auction')
        elif len(sessions) > 1:
            # Multiple sessions - let admin choose
            flash('Select a session to enter')
            return redirect(f'/admin/sessions?auction={auction_id}')
        else:
            # No active sessions
            flash('No active sessions. Create one first.')
            return redirect(f'/admin/sessions?auction={auction_id}')
            
    finally:
        cursor.close()
        db.close()

@bp.route('/exit-auction')
def exit_auction():
    """Exit auction room - clear session data"""
    session.pop('active_auction_id', None)
    session.pop('active_session_id', None)
    session.pop('active_team_id', None)
    session.pop('active_league_name', None)
    return redirect('/admin/')

@bp.route('/auction/start', methods=['POST'])
def start_auction():
    """Create a new auction"""
    if session.get('role') not in ['admin', 'auctioneer']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    league_name = request.form.get('league_name', 'New Auction')
    squad_size = int(request.form.get('squad_size', 18))
    purse_limit = float(request.form.get('purse_limit', 100.0))
    overseas_limit = int(request.form.get('overseas_limit', 8))
    created_by = session.get('user_id')
    
    db = get_db()
    cursor = db.cursor(dictionary=True, buffered=True)
    
    try:
        cursor.execute("""
            INSERT INTO auctions (league_name, squad_size, purse_limit, overseas_limit, created_by, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
        """, (league_name, squad_size, purse_limit, overseas_limit, created_by))
        db.commit()
        auction_id = cursor.lastrowid
        
        return jsonify({
            'success': True,
            'auction_id': auction_id,
            'message': 'Auction created successfully'
        })
        
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        db.close()