from flask import Blueprint, render_template, request, redirect, session, flash, url_for, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from database.db import get_db, get_cached, clear_cache

bp = Blueprint('auth', __name__, url_prefix='/')

def get_role_dashboard(role):
    dashboards = {
        'owner': '/admin/dashboard',
        'admin': '/admin/dashboard',
        'auctioneer': '/admin/dashboard',
        'team_owner': '/team-owner/dashboard',
        'viewer': '/viewer/dashboard'
    }
    return dashboards.get(role, '/')

@bp.route('/')
def login():
    if session.get('user_id'):
        return redirect(get_role_dashboard(session.get('role')))
    return render_template('login.html')

@bp.route('/login', methods=['POST'])
def do_login():
    username = request.form['username']
    password = request.form['password']
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
    finally:
        cursor.close()
        db.close()
    
    if not user:
        flash('Invalid credentials')
        return redirect('/')
    
    if password == user['password_hash'] or check_password_hash(user['password_hash'], password):
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        return redirect(get_role_dashboard(user['role']))
    
    flash('Invalid credentials')
    return redirect('/')

@bp.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@bp.route('/dashboard/activity')
def dashboard_activity():
    if not session.get('user_id') or not session.get('active_auction_id'):
        return jsonify({'activities': []})
    
    team_id = session.get('active_team_id')
    auction_id = session.get('active_auction_id')
    
    if not team_id or not auction_id:
        return jsonify({'activities': []})
    
    # Cache activity for 5 seconds — reduces DB hits on polling
    cache_key = f'activity:{team_id}:{auction_id}'
    
    def fetch_activities():
        db = get_db()
        cursor = db.cursor(dictionary=True)
        
        try:
            cursor.execute("""
                SELECT b.*, p.player_name, t.team_name
                FROM bids b
                JOIN auction_players ap ON b.auction_player_id = ap.id
                JOIN players p ON ap.player_id = p.id
                JOIN teams t ON b.team_id = t.id
                WHERE b.team_id = %s AND b.auction_id = %s
                ORDER BY b.created_at DESC
                LIMIT 5
            """, (team_id, auction_id))
            bids = cursor.fetchall()
            
            activities = []
            for bid in bids:
                activities.append({
                    'type': 'bid',
                    'icon': '💰',
                    'text': f'Bid <strong>₹{float(bid["bid_amount"]):.2f}Cr</strong> on <strong>{bid["player_name"]}</strong>',
                    'time': bid['created_at'].strftime('%H:%M') if hasattr(bid['created_at'], 'strftime') else 'Recently'
                })
            
            return activities
            
        finally:
            cursor.close()
            db.close()
    
    activities = get_cached(cache_key, fetch_activities, ttl_seconds=5)
    return jsonify({'activities': activities})