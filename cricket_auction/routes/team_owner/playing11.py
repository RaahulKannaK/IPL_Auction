from flask import Blueprint, render_template, request, session, flash, redirect, jsonify
import mysql.connector

from database.db import get_db

bp = Blueprint('team_owner_playing11', __name__, url_prefix='/team-owner/playing11')



def get_user_team(user_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    team = cursor.fetchone()
    cursor.close()
    db.close()
    return team

@bp.route('/')
def playing11_builder():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/dashboard')
    
    user_team = get_user_team(session['user_id'])
    if not user_team:
        flash('No team assigned')
        return redirect('/dashboard')
    
    team_id = user_team['id']
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT p.*, tp.purchase_price as sold_price
        FROM team_players tp
        JOIN auction_players ap ON tp.auction_player_id = ap.id
        JOIN players p ON ap.player_id = p.id
        WHERE tp.team_id = %s
        ORDER BY tp.purchase_price DESC
    """, (team_id,))
    squad = cursor.fetchall()
    
    cursor.execute("""
        SELECT p.*, pp.position, pp.is_captain, pp.is_vice_captain
        FROM playing11 pp
        JOIN players p ON pp.player_id = p.id
        WHERE pp.team_id = %s
    """, (team_id,))
    playing11 = cursor.fetchall()
    
    cursor.close()
    db.close()
    
    return render_template('team_owner/playing11.html', squad=squad, playing11=playing11, team_id=team_id)

@bp.route('/save', methods=['POST'])
def save_playing11():
    if session.get('role') != 'team_owner':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    team_id = data.get('team_id')
    players = data.get('players', [])
    
    user_team = get_user_team(session['user_id'])
    if not user_team or user_team['id'] != team_id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("DELETE FROM playing11 WHERE team_id = %s", (team_id,))
    
    for p in players:
        cursor.execute("""
            INSERT INTO playing11 (team_id, player_id, position, is_captain, is_vice_captain)
            VALUES (%s, %s, %s, %s, %s)
        """, (team_id, p['player_id'], p['position'], p.get('is_captain', False), p.get('is_vice_captain', False)))
    
    db.commit()
    cursor.close()
    db.close()
    
    return jsonify({'success': True})