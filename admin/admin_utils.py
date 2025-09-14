# admin/admin_utils.py
import hashlib
from datetime import datetime
from functools import wraps
from flask import session, redirect, url_for, request

# ✅ Import from centralized locations
from extensions import db
from models import AdminLog

def admin_required(f):
    """Decorator to require admin login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_bp.admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def log_admin_action(action, target_type=None, target_id=None, details=None):
    """Log admin actions using SQLAlchemy"""
    if 'admin_id' not in session:
        return
    
    try:
        log_entry = AdminLog(
            admin_id=session['admin_id'],
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip_address=request.remote_addr
        )
        db.session.add(log_entry)
        db.session.commit()
        
    except Exception as e:
        print(f"❌ Log admin action error: {e}")
