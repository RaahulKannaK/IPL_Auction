from flask import Blueprint, render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector

bp = Blueprint('auth', __name__, url_prefix='/')

def get_db():
    return mysql.connector.connect(
        host='localhost',
        user='your_username',
        password='your_password',
        database='cricket_auction'
    )

@bp.route('/')
def login():
    return render_template('login.html')

@bp.route('/login', methods=['POST'])
def do_login():
    username = request.form['username']
    password = request.form['password']
    
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()
    
    if user and check_password_hash(user['password'], password):
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        return redirect('/dashboard')
    
    flash('Invalid credentials')
    return redirect('/')

@bp.route('/logout')
def logout():
    session.clear()
    return redirect('/')