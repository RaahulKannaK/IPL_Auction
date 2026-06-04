from flask import Blueprint, render_template, session, flash, redirect, jsonify
import mysql.connector

bp = Blueprint('team_owner_squad', __name__, url_prefix='/team-owner/squad')

def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='root',
        password='raahul@185',
        database='cricket_auction'
    )

def get_user_team(user_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM teams WHERE owner_id = %s", (user_id,))
    team = cursor.fetchone()
    cursor.close()
    db.close()
    return team

@bp.route('/')
def view_squad():
    if session.get('role') != 'team_owner':
        flash('Unauthorized')
        return redirect('/dashboard')
    
    user_team = get_user_team(session['user_id'])
    if not user_team:
        flash('No team assigned')
        return redirect('/dashboard')
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT p.*, tp.purchase_price as sold_price
        FROM team_players tp
        JOIN auction_players ap ON tp.auction_player_id = ap.id
        JOIN players p ON ap.player_id = p.id
        WHERE tp.team_id = %s
        ORDER BY tp.purchase_price DESC
    """, (user_team['id'],))
    squad = cursor.fetchall()
    
    # Category breakdown
    breakdown = {
        'batsmen': [p for p in squad if p['category'] == 'batsman'],
        'bowlers': [p for p in squad if p['category'] == 'bowler'],
        'all_rounders': [p for p in squad if p['category'] == 'all_rounder'],
        'wicket_keepers': [p for p in squad if p['category'] == 'wicket_keeper'],
        'overseas': [p for p in squad if p['overseas']]
    }
    
    cursor.close()
    db.close()
    
    return render_template('team_owner/squad.html', squad=squad, breakdown=breakdown, team=user_team)