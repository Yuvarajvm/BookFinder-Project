# admin/admin_routes.py
import os
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash
import sqlite3
import hashlib
from datetime import datetime
from .admin_utils import admin_required, log_admin_action

admin_bp = Blueprint('admin_bp', __name__, url_prefix='/admin')

@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login (separate from user login)"""
    if request.method == 'POST':
        username = request.form.get('admin_username')
        password = request.form.get('admin_password')
        
        if not username or not password:
            flash('Please fill in all fields', 'error')
            return render_template('admin/admin_login.html')
        
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        conn = sqlite3.connect('instance/bookfinder.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, admin_username, role FROM admin_users 
            WHERE admin_username = ? AND admin_password = ? AND is_active = 1
        ''', (username, password_hash))
        
        admin = cursor.fetchone()
        
        if admin:
            session['admin_id'] = admin[0]
            session['admin_username'] = admin[1]
            session['admin_role'] = admin[2]
            
            # Update last login
            cursor.execute('UPDATE admin_users SET last_login = ? WHERE id = ?', 
                         (datetime.now(), admin[0]))
            conn.commit()
            
            log_admin_action('admin_login')
            conn.close()
            return redirect(url_for('admin_bp.admin_dashboard'))
        else:
            flash('Invalid credentials', 'error')
        
        conn.close()
    
    return render_template('admin/admin_login.html')

@admin_bp.route('/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard with statistics"""
    conn = sqlite3.connect('instance/bookfinder.db')
    cursor = conn.cursor()
    
    # Get statistics from your existing tables
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM books')
    total_books = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM downloads')
    total_downloads = cursor.fetchone()[0]
    
    # Recent activity
    cursor.execute('''
        SELECT u.username, b.title, d.download_date
        FROM downloads d
        JOIN users u ON d.user_id = u.id
        JOIN books b ON d.book_id = b.id
        ORDER BY d.download_date DESC
        LIMIT 10
    ''')
    recent_downloads = cursor.fetchall()
    
    # Recent user registrations
    cursor.execute('''
        SELECT username, email, created_at FROM users 
        ORDER BY created_at DESC LIMIT 10
    ''')
    recent_users = cursor.fetchall()
    
    conn.close()
    
    stats = {
        'total_users': total_users,
        'total_books': total_books,
        'total_downloads': total_downloads,
        'recent_downloads': recent_downloads,
        'recent_users': recent_users
    }
    
    return render_template('admin/admin_dashboard.html', stats=stats)

@admin_bp.route('/users')
@admin_required
def admin_users():
    """Manage users"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search = request.args.get('search', '')
    
    conn = sqlite3.connect('instance/bookfinder.db')
    cursor = conn.cursor()
    
    if search:
        cursor.execute('''
            SELECT u.*, COUNT(b.id) as book_count, COUNT(d.id) as download_count
            FROM users u
            LEFT JOIN books b ON u.id = b.user_id
            LEFT JOIN downloads d ON u.id = d.user_id
            WHERE u.username LIKE ? OR u.email LIKE ?
            GROUP BY u.id
            ORDER BY u.created_at DESC
            LIMIT ? OFFSET ?
        ''', (f'%{search}%', f'%{search}%', per_page, (page-1)*per_page))
    else:
        cursor.execute('''
            SELECT u.*, COUNT(b.id) as book_count, COUNT(d.id) as download_count
            FROM users u
            LEFT JOIN books b ON u.id = b.user_id
            LEFT JOIN downloads d ON u.id = d.user_id
            GROUP BY u.id
            ORDER BY u.created_at DESC
            LIMIT ? OFFSET ?
        ''', (per_page, (page-1)*per_page))
    
    users = cursor.fetchall()
    
    # Get total count for pagination
    if search:
        cursor.execute('SELECT COUNT(*) FROM users WHERE username LIKE ? OR email LIKE ?', 
                      (f'%{search}%', f'%{search}%'))
    else:
        cursor.execute('SELECT COUNT(*) FROM users')
    
    total_users = cursor.fetchone()[0]
    conn.close()
    
    return render_template('admin/admin_users.html', 
                         users=users, 
                         page=page, 
                         per_page=per_page,
                         total_users=total_users,
                         search=search)

@admin_bp.route('/books')
@admin_required
def admin_books():
    """Manage books"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search = request.args.get('search', '')
    
    conn = sqlite3.connect('instance/bookfinder.db')
    cursor = conn.cursor()
    
    if search:
        cursor.execute('''
            SELECT b.*, u.username, COUNT(d.id) as download_count
            FROM books b
            JOIN users u ON b.user_id = u.id
            LEFT JOIN downloads d ON b.id = d.book_id
            WHERE b.title LIKE ? OR b.author LIKE ? OR u.username LIKE ?
            GROUP BY b.id
            ORDER BY b.upload_date DESC
            LIMIT ? OFFSET ?
        ''', (f'%{search}%', f'%{search}%', f'%{search}%', per_page, (page-1)*per_page))
    else:
        cursor.execute('''
            SELECT b.*, u.username, COUNT(d.id) as download_count
            FROM books b
            JOIN users u ON b.user_id = u.id
            LEFT JOIN downloads d ON b.id = d.book_id
            GROUP BY b.id
            ORDER BY b.upload_date DESC
            LIMIT ? OFFSET ?
        ''', (per_page, (page-1)*per_page))
    
    books = cursor.fetchall()
    
    if search:
        cursor.execute('''
            SELECT COUNT(*) FROM books b 
            JOIN users u ON b.user_id = u.id
            WHERE b.title LIKE ? OR b.author LIKE ? OR u.username LIKE ?
        ''', (f'%{search}%', f'%{search}%', f'%{search}%'))
    else:
        cursor.execute('SELECT COUNT(*) FROM books')
    
    total_books = cursor.fetchone()[0]
    conn.close()
    
    return render_template('admin/admin_books.html', 
                         books=books, 
                         page=page, 
                         per_page=per_page,
                         total_books=total_books,
                         search=search)

@admin_bp.route('/delete-user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    """Delete a user (admin only)"""
    if session.get('admin_role') != 'super_admin':
        return jsonify({'success': False, 'message': 'Insufficient permissions'})
    
    conn = sqlite3.connect('instance/bookfinder.db')
    cursor = conn.cursor()
    
    # Get user info for logging
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return jsonify({'success': False, 'message': 'User not found'})
    
    # Delete user's books files first
    cursor.execute('SELECT filepath FROM books WHERE user_id = ?', (user_id,))
    book_files = cursor.fetchall()
    
    for file_path in book_files:
        try:
            if os.path.exists(file_path[0]):
                os.remove(file_path[0])
        except:
            pass
    
    # Delete from database (cascading)
    cursor.execute('DELETE FROM downloads WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM books WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    
    conn.commit()
    conn.close()
    
    log_admin_action('delete_user', 'user', user_id, f'Deleted user: {user[0]}')
    
    return jsonify({'success': True, 'message': 'User deleted successfully'})

@admin_bp.route('/delete-book/<int:book_id>', methods=['POST'])
@admin_required
def admin_delete_book(book_id):
    """Delete a book (admin)"""
    conn = sqlite3.connect('instance/bookfinder.db')
    cursor = conn.cursor()
    
    # Get book info
    cursor.execute('SELECT title, filepath FROM books WHERE id = ?', (book_id,))
    book = cursor.fetchone()
    
    if not book:
        conn.close()
        return jsonify({'success': False, 'message': 'Book not found'})
    
    # Delete file
    try:
        if os.path.exists(book[1]):
            os.remove(book[1])
    except:
        pass
    
    # Delete from database
    cursor.execute('DELETE FROM downloads WHERE book_id = ?', (book_id,))
    cursor.execute('DELETE FROM books WHERE id = ?', (book_id,))
    
    conn.commit()
    conn.close()
    
    log_admin_action('delete_book', 'book', book_id, f'Deleted book: {book[0]}')
    
    return jsonify({'success': True, 'message': 'Book deleted successfully'})

@admin_bp.route('/logs')
@admin_required
def admin_logs():
    """View admin activity logs"""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    conn = sqlite3.connect('instance/bookfinder.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT al.*, au.admin_username
        FROM admin_logs al
        JOIN admin_users au ON al.admin_id = au.id
        ORDER BY al.timestamp DESC
        LIMIT ? OFFSET ?
    ''', (per_page, (page-1)*per_page))
    
    logs = cursor.fetchall()
    
    cursor.execute('SELECT COUNT(*) FROM admin_logs')
    total_logs = cursor.fetchone()[0]
    
    conn.close()
    
    return render_template('admin/admin_logs.html', 
                         logs=logs, 
                         page=page, 
                         per_page=per_page,
                         total_logs=total_logs)

@admin_bp.route('/logout')
def admin_logout():
    """Admin logout"""
    log_admin_action('admin_logout')
    session.pop('admin_id', None)
    session.pop('admin_username', None)
    session.pop('admin_role', None)
    return redirect(url_for('admin_bp.admin_login'))
@admin_bp.route('/change-password', methods=['GET', 'POST'])
@admin_required
def change_password():
    """Admin change password page"""
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if not current_password or not new_password or not confirm_password:
            flash("All fields are required.", "error")
            return render_template('admin/admin_change_password.html')
        if new_password != confirm_password:
            flash("New passwords do not match.", "error")
            return render_template('admin/admin_change_password.html')

        conn = sqlite3.connect('instance/bookfinder.db')
        cursor = conn.cursor()
        cursor.execute("SELECT admin_password FROM admin_users WHERE id = ?", (session['admin_id'],))
        row = cursor.fetchone()
        if not row:
            conn.close()
            flash("Admin user not found.", "error")
            return redirect(url_for('admin_bp.admin_logout'))

        import hashlib
        if hashlib.sha256(current_password.encode()).hexdigest() != row[0]:
            conn.close()
            flash("Current password is incorrect.", "error")
            return render_template('admin/admin_change_password.html')

        new_hashed = hashlib.sha256(new_password.encode()).hexdigest()
        cursor.execute("UPDATE admin_users SET admin_password=? WHERE id=?",
                       (new_hashed, session['admin_id']))
        conn.commit()
        conn.close()
        flash("Password changed successfully!", "success")
        return redirect(url_for('admin_bp.admin_dashboard'))

    return render_template('admin/admin_change_password.html')
