# admin/admin_utils.py
import sqlite3
import hashlib
from datetime import datetime
from functools import wraps
from flask import session, redirect, url_for, request

def init_admin_db():
    """Initialize admin-specific tables"""
    conn = sqlite3.connect('instance/bookfinder.db')
    cursor = conn.cursor()
    
    # Admin users table (separate from regular users)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_username TEXT UNIQUE NOT NULL,
            admin_email TEXT UNIQUE NOT NULL,
            admin_password TEXT NOT NULL,
            role TEXT DEFAULT 'moderator',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # Admin activity logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            details TEXT,
            ip_address TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES admin_users (id)
        )
    ''')
    
    # Create default super admin (run only once)
    cursor.execute('SELECT COUNT(*) FROM admin_users WHERE role = "super_admin"')
    if cursor.fetchone()[0] == 0:
        admin_password = hashlib.sha256('admin123'.encode()).hexdigest()
        cursor.execute('''
            INSERT INTO admin_users (admin_username, admin_email, admin_password, role)
            VALUES (?, ?, ?, ?)
        ''', ('admin', 'admin@bookfinder.com', admin_password, 'super_admin'))
    
    conn.commit()
    conn.close()

def admin_required(f):
    """Decorator to require admin login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_bp.admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def log_admin_action(action, target_type=None, target_id=None, details=None):
    """Log admin actions"""
    if 'admin_id' not in session:
        return
    
    conn = sqlite3.connect('instance/bookfinder.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO admin_logs (admin_id, action, target_type, target_id, details, ip_address)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (session['admin_id'], action, target_type, target_id, details, request.remote_addr))
    
    conn.commit()
    conn.close()
