# admin/admin_routes.py
import os
import hashlib
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash

# ✅ Import from centralized locations
from extensions import db
from models import User, Book, Download, Review, AdminUser, AdminLog
from .admin_utils import admin_required, log_admin_action

admin_bp = Blueprint('admin_bp', __name__)

@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login using SQLAlchemy"""
    if request.method == 'POST':
        username = request.form.get('admin_username')
        password = request.form.get('admin_password')

        if not username or not password:
            flash('Please fill in all fields', 'error')
            return render_template('admin/admin_login.html')

        password_hash = hashlib.sha256(password.encode()).hexdigest()

        try:
            admin = AdminUser.query.filter_by(
                admin_username=username,
                admin_password=password_hash,
                is_active=True
            ).first()

            if admin:
                session['admin_id'] = admin.id
                session['admin_username'] = admin.admin_username
                session['admin_role'] = admin.role

                admin.last_login = datetime.utcnow()
                db.session.commit()
                
                log_admin_action('admin_login')
                print(f"✅ Admin login successful: {admin.admin_username}")
                return redirect(url_for('admin_bp.admin_dashboard'))
            else:
                flash('Invalid credentials', 'error')
                print("❌ Admin login failed: Invalid credentials")

        except Exception as e:
            print(f"❌ Admin login error: {e}")
            import traceback
            traceback.print_exc()
            flash('Login error occurred', 'error')

    return render_template('admin/admin_login.html')

@admin_bp.route('/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard"""
    try:
        total_users = User.query.count()
        total_books = Book.query.count()
        total_downloads = Download.query.count()

        # Recent downloads with proper joins
        recent_downloads = db.session.query(
            User.username, Book.title, Download.download_date
        ).join(User, Download.user_id == User.id)\
         .join(Book, Download.book_id == Book.id)\
         .order_by(Download.download_date.desc())\
         .limit(10).all()

        # Recent users
        recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()

        stats = {
            'total_users': total_users,
            'total_books': total_books,
            'total_downloads': total_downloads,
            'recent_downloads': recent_downloads,
            'recent_users': recent_users
        }

        print(f"✅ Admin dashboard loaded - Users: {total_users}, Books: {total_books}")
        return render_template('admin/admin_dashboard.html', stats=stats)

    except Exception as e:
        print(f"❌ Admin dashboard error: {e}")
        import traceback
        traceback.print_exc()
        
        stats = {
            'total_users': 0, 'total_books': 0, 'total_downloads': 0,
            'recent_downloads': [], 'recent_users': []
        }
        return render_template('admin/admin_dashboard.html', stats=stats)

@admin_bp.route('/users')
@admin_required
def admin_users():
    """Manage users"""
    try:
        search = request.args.get('search', '')
        query = User.query
        if search:
            query = query.filter(
                (User.username.contains(search)) | 
                (User.email.contains(search))
            )

        users = query.order_by(User.created_at.desc()).all()
        user_list = []
        for user in users:
            book_count = Book.query.filter_by(user_id=user.id).count()
            download_count = Download.query.filter_by(user_id=user.id).count()
            user_list.append({
                'user': user,
                'book_count': book_count,
                'download_count': download_count
            })

        return render_template('admin/admin_users.html',
                             users=user_list, search=search, total_users=len(user_list))
    except Exception as e:
        print(f"❌ Admin users error: {e}")
        return render_template('admin/admin_users.html', users=[], search='', total_users=0)

@admin_bp.route('/books')
@admin_required
def admin_books():
    """Manage books"""
    try:
        search = request.args.get('search', '')
        query = Book.query.join(User, Book.user_id == User.id)
        if search:
            query = query.filter(
                (Book.title.contains(search)) |
                (Book.author.contains(search)) |
                (User.username.contains(search))
            )

        books = query.order_by(Book.upload_date.desc()).all()
        book_list = []
        for book in books:
            download_count = Download.query.filter_by(book_id=book.id).count()
            book_list.append({
                'book': book,
                'username': book.owner.username,
                'download_count': download_count
            })

        return render_template('admin/admin_books.html',
                             books=book_list, search=search, total_books=len(book_list))
    except Exception as e:
        print(f"❌ Admin books error: {e}")
        return render_template('admin/admin_books.html', books=[], search='', total_books=0)

@admin_bp.route('/delete-user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    """Delete a user"""
    if session.get('admin_role') != 'super_admin':
        return jsonify({'success': False, 'message': 'Insufficient permissions'})

    try:
        user = User.query.get_or_404(user_id)
        username = user.username

        # Delete user's book files
        for book in user.books:
            if book.filepath and os.path.exists(book.filepath):
                try:
                    os.remove(book.filepath)
                except:
                    pass

        # SQLAlchemy handles cascading deletes
        db.session.delete(user)
        db.session.commit()

        log_admin_action('delete_user', 'user', user_id, f'Deleted user: {username}')
        return jsonify({'success': True, 'message': 'User deleted successfully'})

    except Exception as e:
        db.session.rollback()
        print(f"❌ Delete user error: {e}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

@admin_bp.route('/delete-book/<int:book_id>', methods=['POST'])
@admin_required
def admin_delete_book(book_id):
    """Delete a book"""
    try:
        book = Book.query.get_or_404(book_id)
        title = book.title

        # Delete file
        if book.filepath and os.path.exists(book.filepath):
            try:
                os.remove(book.filepath)
            except:
                pass

        # Delete book and related downloads
        Download.query.filter_by(book_id=book_id).delete()
        db.session.delete(book)
        db.session.commit()

        log_admin_action('delete_book', 'book', book_id, f'Deleted book: {title}')
        return jsonify({'success': True, 'message': 'Book deleted successfully'})

    except Exception as e:
        db.session.rollback()
        print(f"❌ Delete book error: {e}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

@admin_bp.route('/logout')
def admin_logout():
    """Admin logout"""
    log_admin_action('admin_logout')
    session.pop('admin_id', None)
    session.pop('admin_username', None)
    session.pop('admin_role', None)
    flash('Logged out successfully', 'info')
    return redirect(url_for('admin_bp.admin_login'))

# ───────── Activity Logs ─────────
@admin_bp.route('/logs')
@admin_required
def admin_logs():
    """Activity logs page"""
    try:
        logs = db.session.query(AdminLog, AdminUser.admin_username) \
                         .join(AdminUser, AdminLog.admin_id == AdminUser.id) \
                         .order_by(AdminLog.timestamp.desc()) \
                         .limit(200).all()
        return render_template('admin/admin_logs.html', logs=logs, total_logs=len(logs))
    except Exception as e:
        print(f"❌ Admin logs error: {e}")
        return render_template('admin/admin_logs.html', logs=[], total_logs=0)

# ───────── Change Password ─────────
@admin_bp.route('/change-password', methods=['GET', 'POST'])
@admin_required
def change_password():
    """Allow an admin to change their password"""
    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not current_password or not new_password or not confirm_password:
            flash('Please fill in all fields', 'warning')
            return render_template('admin/admin_change_password.html')

        if new_password != confirm_password:
            flash('Passwords do not match', 'warning')
            return render_template('admin/admin_change_password.html')

        if len(new_password) < 6:
            flash('Password must be at least 6 characters', 'warning')
            return render_template('admin/admin_change_password.html')

        try:
            admin = AdminUser.query.get(session['admin_id'])
            if not admin:
                flash('Admin not found', 'danger')
                return redirect(url_for('admin_bp.admin_login'))

            # Verify current password (sha256)
            curr_hash = hashlib.sha256(current_password.encode()).hexdigest()
            if admin.admin_password != curr_hash:
                flash('Current password is incorrect', 'danger')
                return render_template('admin/admin_change_password.html')

            # Update password
            admin.admin_password = hashlib.sha256(new_password.encode()).hexdigest()
            db.session.commit()
            log_admin_action('change_password')
            flash('Password changed successfully', 'success')
            return redirect(url_for('admin_bp.admin_dashboard'))
        except Exception as e:
            db.session.rollback()
            print(f"❌ Change password error: {e}")
            flash('Error changing password', 'danger')
            return render_template('admin/admin_change_password.html')

    return render_template('admin/admin_change_password.html')

