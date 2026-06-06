from flask import Blueprint, render_template, request, redirect, session, flash, jsonify

import json

bp = Blueprint('admin_sessions', __name__, url_prefix='/admin/sessions')

from database.db import get_db


@bp.route('/')
def list_sessions():
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        flash('Unauthorized')
        return redirect('/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM auctions WHERE status IN ('live', 'paused', 'pending') ORDER BY id DESC")
    auctions = cursor.fetchall()
    
    cursor.execute("""
        SELECT s.*, a.league_name
        FROM auction_sessions s
        JOIN auctions a ON s.auction_id = a.id
        ORDER BY s.created_at DESC
    """)
    sessions = cursor.fetchall()
    
    cursor.close()
    db.close()
    return render_template('admin/sessions.html', auctions=auctions, sessions=sessions)

@bp.route('/create', methods=['POST'])
def create_session():
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    auction_id = request.form['auction_id']
    session_name = request.form['session_name']
    team_ids = request.form.getlist('team_ids')
    
    if len(team_ids) < 2:
        flash('Need at least 2 teams')
        return redirect('/admin/sessions')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO auction_sessions (auction_id, session_name, team_ids, status, start_time)
        VALUES (%s, %s, %s, 'active', NOW())
    """, (auction_id, session_name, json.dumps([int(t) for t in team_ids])))
    db.commit()
    cursor.close()
    db.close()
    
    flash('Session created!')
    return redirect('/admin/sessions')

@bp.route('/close/<int:session_id>', methods=['POST'])
def close_session(session_id):
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE auction_sessions SET status = 'completed', end_time = NOW() WHERE id = %s", (session_id,))
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True})

@bp.route('/continue/<int:session_id>', methods=['POST'])
def continue_session(session_id):
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM auction_sessions WHERE id = %s", (session_id,))
    old_session = cursor.fetchone()
    
    # Create new session with same teams
    cursor.execute("""
        INSERT INTO auction_sessions (auction_id, session_name, team_ids, status, start_time)
        VALUES (%s, %s, %s, 'active', NOW())
    """, (old_session['auction_id'], old_session['session_name'] + ' (Continued)', old_session['team_ids']))
    
    db.commit()
    cursor.close()
    db.close()
    
    flash('Session continued!')
    return redirect('/admin/sessions')

@bp.route('/history')
def session_history():
    if session.get('role') not in ['owner', 'admin', 'auctioneer']:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        SELECT s.*, a.league_name
        FROM auction_sessions s
        JOIN auctions a ON s.auction_id = a.id
        ORDER BY s.created_at DESC
    """)
    sessions = cursor.fetchall()
    cursor.close()
    db.close()
    
    return jsonify({'sessions': sessions})