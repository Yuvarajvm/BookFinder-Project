# app.py — BookFinder (COMPLETE FIXED VERSION)
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify, current_app
import os
import hashlib
from flask_login import current_user
import requests
import secrets
import time
import glob
from datetime import datetime, timedelta
import google.generativeai as genai
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# ✅ Import from centralized extensions and models
from extensions import db, mail
from flask_mail import Message
from models import User, Book, Download, Review, AdminUser, AdminLog

# ───────────────────────── INIT ──────────────────────────

load_dotenv()
app = Flask(__name__)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)

# Database config
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Configuration
if os.getenv('RENDER'):
    app.config.update(
        SECRET_KEY=os.getenv('SECRET_KEY'),
        SQLALCHEMY_DATABASE_URI=DATABASE_URL,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAIL_SERVER=os.getenv('MAIL_SERVER', 'smtp.gmail.com'),
        MAIL_PORT=int(os.getenv('MAIL_PORT', 587)),
        MAIL_USE_TLS=os.getenv('MAIL_USE_TLS', 'true').lower() == 'true',
        MAIL_USERNAME=os.getenv('MAIL_USERNAME'),
        MAIL_PASSWORD=os.getenv('MAIL_PASSWORD'),
        MAIL_DEFAULT_SENDER=os.getenv('MAIL_DEFAULT_SENDER')
    )
else:
    db_path = os.path.join(os.getcwd(), 'bookfinder.db')
    app.config.update(
        SECRET_KEY=os.getenv('SECRET_KEY', 'dev_secret_key'),
        SQLALCHEMY_DATABASE_URI=DATABASE_URL or f'sqlite:///{db_path}',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAIL_SERVER='smtp.gmail.com',
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME=os.getenv('MAIL_USERNAME'),
        MAIL_PASSWORD=os.getenv('MAIL_PASSWORD'),
        MAIL_DEFAULT_SENDER=os.getenv('MAIL_DEFAULT_SENDER')
    )

app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# ✅ CRITICAL: Initialize extensions with app
db.init_app(app)
mail.init_app(app)

# ✅ Initialize everything in app context
with app.app_context():
    # Create all tables
    db.create_all()
    print(f"✅ Tables created in {app.config['SQLALCHEMY_DATABASE_URI'][:30]}...")
    
    # Create default admin user
    try:
        existing_admin = AdminUser.query.filter_by(role='super_admin').first()
        if not existing_admin:
            admin_password = hashlib.sha256('admin123'.encode()).hexdigest()
            default_admin = AdminUser(
                admin_username='admin',
                admin_email='admin@bookfinder.com',
                admin_password=admin_password,
                role='super_admin'
            )
            db.session.add(default_admin)
            db.session.commit()
            print("✅ Default admin user created (admin/admin123)")
        print("✅ Admin database initialized")
    except Exception as e:
        print(f"❌ Admin init error: {e}")

# ✅ Register admin blueprint
try:
    from admin.admin_routes import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')
    print("✅ Admin blueprint registered successfully")
except Exception as e:
    print(f"❌ Admin blueprint error: {e}")

# Upload config
UPLOAD_FOLDER = "/tmp/uploads" if os.getenv('RENDER') else "uploads"
ALLOWED_EXTENSIONS = {"pdf", "epub"}
MAX_FILE_SIZE = 50 * 1024 * 1024
app.config.update(UPLOAD_FOLDER=UPLOAD_FOLDER, MAX_CONTENT_LENGTH=MAX_FILE_SIZE)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

reset_tokens = {}

def allowed_file(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# ✅ DEBUG ROUTE
@app.route('/debug-db')
def debug_db():
    """Debug route to show database configuration"""
    db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI')
    
    try:
        user_count = User.query.count()
        book_count = Book.query.count()
        admin_count = AdminUser.query.count()
        
        result = f"<h2>🔧 Database Debug Info</h2>"
        result += f"<p>URI: <code>{db_uri[:50]}...</code></p>"
        result += f"<p>✅ Users: <strong>{user_count}</strong></p>"
        result += f"<p>✅ Books: <strong>{book_count}</strong></p>"
        result += f"<p>✅ Admin Users: <strong>{admin_count}</strong></p>"
        return result
    except Exception as e:
        return f"<h2>❌ Database Error</h2><p>{e}</p>"

# Email Functions
def send_welcome_email(email, username):
    """Send welcome email to new users"""
    try:
        msg = Message('Welcome to BookFinder!', recipients=[email])
        msg.body = f"""Hi {username},

Welcome to BookFinder! Your account has been created successfully.

Start exploring and uploading your favorite books!

Best regards,
BookFinder Team"""
        
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def send_password_reset_email(email, username, token):
    """Send password reset email"""
    try:
        reset_url = request.url_root.rstrip('/') + f'/reset-password/{token}'
        msg = Message('Reset Your BookFinder Password', recipients=[email])
        msg.body = f"""Hi {username},

Someone requested a password reset for your BookFinder account.

Click the link below to reset your password:
{reset_url}

This link will expire in 1 hour.

If you didn't request this, please ignore this email.

Best regards,
BookFinder Team"""
        
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

# External API Functions
def search_google_books(q, max_results=50):
    """Google Books API with optional API key support"""
    try:
        params = {"q": q, "maxResults": max_results, "printType": "books"}
        api_key = os.getenv('GOOGLE_API_KEY')
        if api_key:
            params["key"] = api_key

        response = requests.get("https://www.googleapis.com/books/v1/volumes", params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        books = []
        for it in data.get("items", []):
            v = it.get("volumeInfo", {})
            desc_raw = v.get("description") or "No description"
            desc = (desc_raw[:300] + "...") if len(desc_raw) > 300 else desc_raw
            
            thumb = (v.get("imageLinks", {}) or {}).get("thumbnail", "")
            if thumb.startswith("http:"):
                thumb = thumb.replace("http:", "https:")

            sale = it.get("saleInfo", {}) or {}
            list_price = (sale.get("listPrice") or {})
            price_amount = list_price.get("amount")
            price_currency = list_price.get("currencyCode")
            price_str = f"{price_amount} {price_currency}" if (price_amount and price_currency) else ""

            books.append({
                "id": it.get("id", ""),
                "title": v.get("title", "Unknown Title"),
                "author": ", ".join(v.get("authors", ["Unknown Author"])),
                "description": desc,
                "thumbnail": thumb,
                "published_date": v.get("publishedDate", ""),
                "page_count": v.get("pageCount", 0),
                "preview_link": v.get("previewLink", ""),
                "info_link": v.get("infoLink", ""),
                "isbn13": "",
                "price": price_str,
                "price_value": price_amount or 0,
                "rating": v.get("averageRating", 0),
                "filename": "",
                "filepath": "",
                "source": "google_books",
            })

        return books
    except Exception as e:
        print(f"Google Books error: {e}")
        return []

def search_open_library(q, limit=10, page=1):
    """Open Library search with bulletproof error handling"""
    try:
        params = {
            "q": q, "limit": limit, "page": page,
            "fields": "key,title,author_name,first_publish_year,cover_i,isbn,ia,has_fulltext,public_scan_b"
        }
        r = requests.get("https://openlibrary.org/search.json", params=params, timeout=8)
        r.raise_for_status()
        data = r.json()

        results = []
        for d in data.get("docs", []):
            try:
                authors = d.get("author_name") or []
                author_str = ", ".join(authors) if authors else "Unknown Author"
                
                cover_id = d.get("cover_i")
                cover = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else ""
                
                isbn_list = d.get("isbn") or []
                isbn13 = ""
                if isbn_list:
                    for isbn in isbn_list:
                        if isinstance(isbn, str) and len(isbn) == 13:
                            isbn13 = isbn
                            break

                publish_year = d.get("first_publish_year")
                if isinstance(publish_year, (int, float)):
                    publish_date = str(int(publish_year))
                elif isinstance(publish_year, str) and publish_year.isdigit():
                    publish_date = str(int(publish_year))
                else:
                    publish_date = ""

                ia_list = d.get("ia") or []
                has_fulltext = d.get("has_fulltext", False)
                public_scan = d.get("public_scan_b", False)
                
                preview_link = ""
                if ia_list and isinstance(ia_list, list) and len(ia_list) > 0:
                    if has_fulltext or public_scan:
                        preview_link = f"<https://archive.org/details/{ia_list>[0]}"

                book_key = d.get("key", "")
                info_link = f"https://openlibrary.org{book_key}" if book_key else ""

                results.append({
                    "id": book_key,
                    "title": d.get("title", "Unknown Title"),
                    "author": author_str,
                    "description": "Available on Open Library",
                    "thumbnail": cover,
                    "published_date": publish_date,
                    "page_count": 0,
                    "preview_link": preview_link,
                    "info_link": info_link,
                    "isbn13": isbn13,
                    "price": "",
                    "price_value": 0,
                    "rating": 0,
                    "filename": "",
                    "filepath": "",
                    "source": "openlibrary"
                })
            except Exception:
                continue

        return results
    except Exception as e:
        print(f"Open Library error: {e}")
        return []

def search_gutendx(q, limit=10):
    """Gutendx (Project Gutenberg public-domain ebooks)"""
    try:
        r = requests.get("https://gutendex.com/books", params={"search": q}, timeout=8)
        r.raise_for_status()
        data = r.json()

        out = []
        for b in data.get("results", [])[:limit]:
            authors = [a.get("name") for a in b.get("authors", []) if a.get("name")]
            formats = b.get("formats") or {}
            cover = formats.get("image/jpeg", "")
            epub = formats.get("application/epub+zip", "")
            txt = formats.get("text/plain; charset=utf-8", "") or formats.get("text/plain", "")

            out.append({
                "id": b.get("id"),
                "title": b.get("title", ""),
                "author": ", ".join(authors) if authors else "Unknown Author",
                "description": "Public domain (Project Gutenberg)",
                "thumbnail": cover,
                "published_date": "",
                "page_count": 0,
                "preview_link": epub or txt,
                "info_link": f"https://www.gutenberg.org/ebooks/{b.get('id')}",
                "isbn13": "",
                "download_url": epub or txt,
                "price": "",
                "price_value": 0,
                "rating": 0,
                "filename": "",
                "filepath": "",
                "source": "gutendx"
            })

        print(f"✅ Gutendx: Found {len(out)} books")
        return out
    except Exception as e:
        print(f"❌ Gutendx error: {e}")
        return []

def search_nyt_books(query=None, limit=10):
    """NYT Books API with retry logic for rate limiting"""
    api_key = os.getenv('NYT_API_KEY')
    if not api_key:
        print("❌ NYT_API_KEY not found in environment variables")
        return []

    url = f"https://api.nytimes.com/svc/books/v3/lists/current/combined-print-and-e-book-fiction.json?api-key={api_key}"
    
    retries = 0
    max_retries = 3
    backoff_factor = 2

    while retries < max_retries:
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            
            nyt_data = response.json()
            books = nyt_data.get('results', {}).get('books', [])
            
            results = []
            for book in books:
                if query and (query.lower() not in book.get('title', '').lower() and 
                             query.lower() not in book.get('author', '').lower()):
                    continue
                
                results.append({
                    'title': book.get('title', 'Unknown Title'),
                    'author': book.get('author', 'Unknown Author'),
                    'year': 'Recent',
                    'source': 'NYT Best Seller',
                    'isbn': book.get('primary_isbn13', ''),
                    'description': book.get('description', ''),
                    'weeks_on_list': book.get('weeks_on_list', 0)
                })
                
                if len(results) >= limit:
                    break
            
            print(f"✅ NYT: Found {len(results)} books")
            return results

        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response.status_code == 429:
                wait_time = backoff_factor ** retries
                print(f"⏳ NYT rate limit hit. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
                retries += 1
            else:
                print(f"❌ NYT HTTP error: {e}")
                break
        except requests.exceptions.RequestException as e:
            print(f"❌ NYT request error: {e}")
            break
    
    print("❌ NYT: Max retries exceeded or request failed")
    return []

# Helper Functions
def get_book_reviews(book_id):
    """Get reviews for a specific book from database."""
    try:
        reviews = Review.query.join(User, Review.user_id == User.id)\
                             .filter(Review.book_id == str(book_id))\
                             .order_by(Review.created_at.desc())\
                             .limit(3).all()
        return [{"username": r.user.username, "rating": r.rating, "text": r.review_text} 
                for r in reviews if hasattr(r, 'user')]
    except Exception as e:
        print(f"Error fetching reviews: {e}")
        return []

def normalize_key(title, author):
    t = (title or "").strip().lower()
    
    if author:
        if isinstance(author, str):
            a = author.split(",")[0].strip().lower()
        elif isinstance(author, list) and len(author) > 0:
            a = str(author[0]).split(",")[0].strip().lower()
        else:
            a = ""
    else:
        a = ""
    
    return f"{t}|{a}"

def merge_results(*lists):
    """Deduplicate by isbn13 if present else title|author."""
    seen = set()
    merged = []
    for lst in lists:
        for b in lst:
            key = b.get("isbn13") or normalize_key(b.get("title"), b.get("author"))
            if key in seen:
                continue
            seen.add(key)
            b["reviews"] = get_book_reviews(b.get("id", ""))
            merged.append(b)
    return merged

def sort_results(results, sort_by):
    """Sort results based on sort_by parameter with type safety."""
    try:
        if sort_by == "price_low":
            return sorted(results, key=lambda x: float(x.get("price_value", 0) or 0))
        elif sort_by == "price_high":
            return sorted(results, key=lambda x: float(x.get("price_value", 0) or 0), reverse=True)
        elif sort_by == "rating":
            return sorted(results, key=lambda x: float(x.get("rating", 0) or 0), reverse=True)
        elif sort_by == "new":
            def safe_date_key(x):
                date_str = x.get("published_date", "")
                try:
                    return int(date_str) if date_str and date_str.isdigit() else 0
                except (ValueError, TypeError):
                    return 0
            return sorted(results, key=safe_date_key, reverse=True)
        elif sort_by == "discount":
            return sorted(results, key=lambda x: (float(x.get("price_value", 0) or 0) == 0, -float(x.get("price_value", 0) or 0)), reverse=True)
        else:
            return results
    except Exception as e:
        print(f"Sorting error: {e}")
        return results

# ───────────────────── ROUTES ─────────────────────────────

@app.route("/")
def home():
    featured = search_google_books("bestseller fiction", 6)
    uploaded = []
    try:
        books = Book.query.join(User, Book.user_id == User.id)\
                         .order_by(Book.upload_date.desc())\
                         .limit(6).all()
        uploaded = [
            dict(
                id=b.id, title=b.title, author=b.author, isbn=b.isbn or "", description=b.description,
                filename=b.filename, filepath=b.filepath, user_id=b.user_id, upload_date=b.upload_date,
                uploader=b.owner.username if b.owner else "", source="uploaded"
            ) for b in books
        ]
    except Exception as e:
        print("DB error:", e)

    return render_template("home.html", google_books=featured, uploaded_books=uploaded, user=current_user)

@app.route("/register", methods=["POST"])
def register():
    u = request.form.get("username", "").strip()
    e = request.form.get("email", "").strip().lower()
    p = request.form.get("password", "").strip()
    c = request.form.get("confirm_password", "").strip()

    if not all([u, e, p, c]):
        return jsonify(success=False, message="Fill in all fields")
    if p != c:
        return jsonify(success=False, message="Passwords do not match")
    if len(p) < 6:
        return jsonify(success=False, message="Password too short")

    pw_hash = hashlib.sha256(p.encode()).hexdigest()

    try:
        new_user = User(username=u, email=e, password=pw_hash)
        db.session.add(new_user)
        db.session.commit()

        send_welcome_email(e, u)
        return jsonify(success=True, message="Account created! Please log in.")

    except Exception as err:
        db.session.rollback()
        error_details = str(err)
        print(f"❌ Registration error: {error_details}")
        
        if 'UNIQUE constraint failed' in error_details or 'duplicate key value violates unique constraint' in error_details:
            return jsonify(success=False, message="Username or email already exists")
        elif 'no such table' in error_details.lower():
            return jsonify(success=False, message="Database tables not created. Contact admin.")
        else:
            return jsonify(success=False, message=f"Registration error: {error_details}")

@app.route("/login", methods=["POST"])
def login():
    e = request.form.get("email", "").strip().lower()
    p = request.form.get("password", "").strip()

    search_query = request.form.get("search_query", "")
    search_source = request.form.get("search_source", "")

    if not all([e, p]):
        return jsonify(success=False, message="Fill in all fields")

    pw_hash = hashlib.sha256(p.encode()).hexdigest()

    try:
        user = User.query.filter_by(email=e, password=pw_hash).first()

        if user:
            session["user_id"] = user.id
            session["username"] = user.username
            
            if search_query:
                redirect_url = f"/search?q={search_query}"
                if search_source:
                    redirect_url += f"&source={search_source}"
                return jsonify(success=True, message="Logged in", redirect=redirect_url)
            else:
                return jsonify(success=True, message="Logged in", redirect="/")

        return jsonify(success=False, message="Invalid credentials")
    except Exception as db_error:
        print(f"Database error during login: {db_error}")
        return jsonify(success=False, message="Database error")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        if not all(request.form.get(k, "").strip() for k in ("name", "email", "subject", "message")):
            return jsonify(success=False, message="Fill in all fields")
        return jsonify(success=True, message="Thanks! We'll respond soon.")
    return render_template("contact.html")

@app.route("/upload", methods=["GET", "POST"])
def upload_page_or_handler():
    if request.method == "GET":
        return render_template("upload.html")

    if "user_id" not in session:
        return jsonify(success=False, message="Please log in to upload books")

    f = request.files.get("file")
    title = request.form.get("title", "").strip()
    author = request.form.get("author", "").strip() or "Unknown Author"
    isbn = request.form.get("isbn", "").strip()
    description = request.form.get("description", "").strip() or "No description provided"

    if not f or f.filename == "" or not allowed_file(f.filename):
        return jsonify(success=False, message="Please select a valid PDF or EPUB file")
    
    if not title:
        return jsonify(success=False, message="Book title is required")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name, ext = os.path.splitext(secure_filename(f.filename))
    unique_filename = f"{timestamp}_{name}{ext}"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], unique_filename)

    try:
        f.save(filepath)
        print(f"✅ File saved to: {filepath}")

        new_book = Book(
            title=title,
            author=author,
            isbn=isbn,
            description=description,
            filename=unique_filename,
            filepath=filepath,
            user_id=session["user_id"]
        )
        
        db.session.add(new_book)
        db.session.commit()
        
        print(f"✅ Book '{title}' saved to database with ID: {new_book.id} for user: {session['user_id']}")

        return jsonify(
            success=True, 
            message="Book uploaded successfully!", 
            redirect=url_for("my_books")
        )

    except Exception as e:
        print(f"❌ Upload error: {e}")
        db.session.rollback()
        
        if os.path.exists(filepath):
            os.remove(filepath)
            
        return jsonify(success=False, message=f"Upload failed: {str(e)}")

@app.route("/my-books")
def my_books():
    if "user_id" not in session:
        return render_template("mybooks.html", 
                             books=[], 
                             uploaded_books_count=0, 
                             total_books_count=0, 
                             downloads_count=0, 
                             days_since_joined=0)

    user_id = session["user_id"]
    
    print(f"🔍 Fetching books for user_id: {user_id}")

    try:
        user = User.query.get(user_id)
        if not user:
            print(f"❌ User with ID {user_id} not found!")
            session.clear()
            return redirect(url_for("home"))

        user_books = Book.query.filter_by(user_id=user_id).order_by(Book.upload_date.desc()).all()
        print(f"📚 Found {len(user_books)} books for user: {user.username}")

        book_list = []
        for book in user_books:
            book_dict = {
                'id': book.id,
                'title': book.title,
                'author': book.author,
                'isbn': book.isbn or "",
                'description': book.description,
                'filename': book.filename,
                'filepath': book.filepath,
                'user_id': book.user_id,
                'upload_date': book.upload_date.strftime('%Y-%m-%d') if book.upload_date else '',
                'uploader': user.username,
                'source': "uploaded"
            }
            book_list.append(book_dict)
            print(f"  - {book.title} by {book.author}")

        uploaded_books_count = len(book_list)
        total_books_count = Book.query.count()
        downloads_count = Download.query.filter_by(user_id=user_id).count()
        days_since_joined = (datetime.now() - user.created_at).days if user.created_at else 0

        print(f"📊 Stats - Uploaded: {uploaded_books_count}, Total: {total_books_count}, Downloads: {downloads_count}")

        return render_template(
            "mybooks.html", 
            books=book_list,
            uploaded_books_count=uploaded_books_count,
            total_books_count=total_books_count,
            downloads_count=downloads_count,
            days_since_joined=days_since_joined
        )

    except Exception as e:
        print(f"❌ Error fetching user books: {e}")
        import traceback
        traceback.print_exc()
        
        return render_template("mybooks.html", 
                             books=[], 
                             uploaded_books_count=0,
                             total_books_count=0, 
                             downloads_count=0, 
                             days_since_joined=0)

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    sort_by = request.args.get("sort", "relevance")
    sources_param = request.args.get("sources", "")
    sources_selected = sources_param.split(",") if sources_param else []

    if not query:
        return redirect(url_for("home"))

    if "user_id" not in session:
        return render_template(
            "results.html",
            show_login_modal=True,
            results=[],
            query=query,
            sort_by=sort_by,
            sources_selected=sources_selected,
            total_results=0
        )

    google_results = []
    openlib_results = []
    gutendx_results = []
    nyt_results = []

    try:
        if not sources_selected or "google_books" in sources_selected:
            google_results = search_google_books(query, 20)
            print(f"✅ Google Books: {len(google_results)} results")
    except Exception as e:
        print(f"❌ Google Books error: {e}")

    try:
        if not sources_selected or "openlibrary" in sources_selected:
            openlib_results = search_open_library(query, limit=10)
            print(f"✅ Open Library: {len(openlib_results)} results")
    except Exception as e:
        print(f"❌ Open Library error: {e}")

    try:
        if not sources_selected or "gutendx" in sources_selected:
            gutendx_results = search_gutendx(query, limit=10)
            print(f"✅ Gutendx: {len(gutendx_results)} results")
    except Exception as e:
        print(f"❌ Gutendx error: {e}")

    try:
        if not sources_selected or "nyt" in sources_selected:
            nyt_results = search_nyt_books(query, limit=8)
            print(f"✅ NYT: {len(nyt_results)} results")
    except Exception as e:
        print(f"❌ NYT error: {e}")

    local_results = []
    if not sources_selected or "uploaded" in sources_selected:
        try:
            like = f"%{query}%"
            books = Book.query.join(User, Book.user_id == User.id).filter(
                (Book.title.ilike(like)) | (Book.author.ilike(like)) | (Book.description.ilike(like))
            ).order_by(Book.upload_date.desc()).all()

            local_results = [
                dict(id=b.id, title=b.title, author=b.author, isbn=b.isbn or "",
                    description=b.description, filename=b.filename, filepath=b.filepath, user_id=b.user_id,
                    upload_date=b.upload_date, uploader=b.owner.username if b.owner else "", thumbnail="", published_date="",
                    page_count=0, preview_link=url_for("download_book", book_id=b.id),
                    info_link="", isbn13="", price="", price_value=0, rating=0, source="uploaded"
                ) for b in books
            ]
            print(f"✅ Local DB: {len(local_results)} results")
        except Exception as e:
            print(f"❌ Local search error: {e}")
            local_results = []

    all_results = merge_results(local_results, google_results, openlib_results, gutendx_results, nyt_results)
    sorted_results = sort_results(all_results, sort_by)

    return render_template(
        "results.html", results=sorted_results, query=query, sort_by=sort_by,
        sources_selected=sources_selected, total_results=len(all_results), show_login_modal=False
    )

@app.route("/add_free_book", methods=["POST"])
def add_free_book():
    if "user_id" not in session:
        return jsonify(success=False, require_login=True, message="Please log in")

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    author = (data.get("author") or "Unknown Author").strip()
    description = (data.get("description") or "No description").strip()
    download_url = (data.get("download_url") or "").strip()
    source = (data.get("source") or "").strip()

    if source not in ("gutendx",) or not title or not download_url:
        return jsonify(success=False, message="Invalid request")

    try:
        new_book = Book(title=title, author=author, isbn="", description=description,
                       filename="", filepath=download_url, user_id=session["user_id"])
        db.session.add(new_book)
        db.session.commit()
        return jsonify(success=True, message="Added to My Books")
    except Exception as e:
        print("add_free_book error:", e)
        return jsonify(success=False, message="Failed to add book")

@app.route("/download/<int:book_id>")
def download_book(book_id):
    try:
        book = Book.query.get(book_id)

        if not book:
            return "File not found", 404

        if not book.filepath or not os.path.exists(book.filepath):
            return "File missing on server", 404

        if "user_id" in session:
            try:
                new_download = Download(book_id=book_id, user_id=session["user_id"])
                db.session.add(new_download)
                db.session.commit()
            except Exception:
                pass

        return send_file(book.filepath, as_attachment=True, download_name=book.filename)

    except Exception as e:
        print("download_book error:", e)
        return "Error downloading file", 500

@app.route("/delete_book/<int:book_id>", methods=["DELETE"])
def delete_book(book_id):
    if "user_id" not in session:
        return jsonify(success=False, message="Please log in")

    try:
        book = Book.query.get(book_id)

        if not book:
            return jsonify(success=False, message="Book not found")

        if book.user_id != session["user_id"]:
            return jsonify(success=False, message="You are not the owner")

        if book.filepath and os.path.exists(book.filepath):
            os.remove(book.filepath)

        Download.query.filter_by(book_id=book_id).delete()
        db.session.delete(book)
        db.session.commit()

        return jsonify(success=True, message="Book deleted")

    except Exception as e:
        print("delete_book error:", e)
        return jsonify(success=False, message=f"Error: {e}")

# Password Reset Routes
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Handle forgot password - email collection and reset link sending"""
    if request.method == 'GET':
        return render_template('forgot_password.html')

    email = request.form.get('email', '').strip().lower()
    if not email:
        flash('Please enter your email address.', 'warning')
        return render_template('forgot_password.html')

    try:
        user = User.query.filter_by(email=email).first()

        if user:
            token = secrets.token_urlsafe(32)
            reset_tokens[token] = {
                'user_id': user.id,
                'email': email,
                'expires': datetime.utcnow() + timedelta(hours=1)
            }
            send_password_reset_email(email, user.username, token)

        flash('If the email exists, a reset link was sent.', 'info')
        return redirect(url_for('home'))
    except Exception as e:
        print(f"Forgot password error: {e}")
        flash('If the email exists, a reset link was sent.', 'info')
        return redirect(url_for('home'))

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    token_data = reset_tokens.get(token)
    if not token_data or datetime.utcnow() > token_data['expires']:
        reset_tokens.pop(token, None)
        flash('Invalid or expired reset link!', 'danger')
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        new_password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not new_password or not confirm_password:
            flash('Please fill in both password fields!', 'warning')
            return render_template('reset_password.html')
            
        if new_password != confirm_password:
            flash('Passwords do not match!', 'warning')
            return render_template('reset_password.html')
        
        if len(new_password) < 6:
            flash('Password must be at least 6 characters long!', 'warning')
            return render_template('reset_password.html')
        
        pw_hash = hashlib.sha256(new_password.encode()).hexdigest()
        
        try:
            user = User.query.get(token_data['user_id'])
            if user:
                user.password = pw_hash
                db.session.commit()
            
            reset_tokens.pop(token, None)
            flash('Password reset successful! Please log in with your new password.', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            print(f"Database error: {e}")
            flash('An error occurred. Please try again.', 'danger')
            return render_template('reset_password.html')
    
    return render_template('reset_password.html')

# Reading Routes for PDF and EPUB
@app.route("/read/<int:book_id>")
def read_book(book_id):
    """Display reader for EPUB or PDF books"""
    if "user_id" not in session:
        return redirect(url_for("home"))
    
    try:
        book = Book.query.get(book_id)

        if not book:
            return "Book not found", 404

        if not book.filename:
            return "No file attached to this book", 400
            
        file_extension = book.filename.lower().split('.')[-1]

        if file_extension == 'epub':
            return render_template("epub_reader.html", book_id=book_id, title=book.title)
        elif file_extension == 'pdf':
            return render_template("pdf_viewer.html", book_id=book_id, title=book.title)
        else:
            return f"Unsupported file format: {file_extension}", 400

    except Exception as e:
        print("read_book error:", e)
        return "Error loading book reader", 500

@app.route("/serve_epub/<int:book_id>")
def serve_epub(book_id):
    """Serve EPUB files for the reader"""
    try:
        book = Book.query.get(book_id)

        if not book:
            return "EPUB file not found", 404

        if not book.filepath or not os.path.exists(book.filepath):
            return "EPUB file missing on server", 404

        if not book.filename.lower().endswith('.epub'):
            return "This is not an EPUB file", 400

        return send_file(book.filepath, as_attachment=False, mimetype='application/epub+zip')

    except Exception as e:
        print("serve_epub error:", e)
        return "Error serving EPUB file", 500

@app.route("/serve_pdf/<int:book_id>")
def serve_pdf(book_id):
    """Serve PDF files for the viewer"""
    try:
        book = Book.query.get(book_id)

        if not book:
            return "PDF file not found", 404

        if not book.filepath or not os.path.exists(book.filepath):
            return "PDF file missing on server", 404

        if not book.filename.lower().endswith('.pdf'):
            return "This is not a PDF file", 400

        return send_file(book.filepath, as_attachment=False, mimetype='application/pdf')

    except Exception as e:
        print("serve_pdf error:", e)
        return "Error serving PDF file", 500

@app.route('/test-email')
def test_email():
    try:
        msg = Message(
            "🧪 Test Email from BookFinder",
            recipients=[app.config['MAIL_USERNAME']]
        )
        msg.body = """Hi!

This is a test email from your BookFinder Flask app.

If you receive this, your Gmail SMTP is working correctly!

Best regards,
BookFinder Test System"""
        
        mail.send(msg)
        return "<h2>✅ Test email sent successfully!</h2><p>Check your inbox (or spam folder).</p>"
    except Exception as e:
        print(f"❌ Email error: {e}")
        return f"<h2>❌ Failed to send test email</h2><p>Error: {e}</p>"

@app.template_filter('fmt_date')
def fmt_date(value, out_fmt='%Y-%m-%d'):
    if not value:
        return 'N/A'
    # If it's already a datetime
    if hasattr(value, 'strftime'):
        try:
            return value.strftime(out_fmt)
        except Exception:
            return 'N/A'
    # If it's a string, try common formats
    s = str(value)
    try:
        # ISO 8601 support, including Z
        if s.endswith('Z'):
            s = s.replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        return dt.strftime(out_fmt)
    except Exception:
        pass

    for in_fmt in (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d',
        '%d-%m-%Y %H:%M:%S',
        '%d-%m-%Y',
        '%m/%d/%Y %H:%M:%S',
        '%m/%d/%Y',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
    ):
        try:
            return datetime.strptime(s, in_fmt).strftime(out_fmt)
        except Exception:
            continue
    # Last‑resort: show first 10 chars (often YYYY-MM-DD)
    return s[:10]

# Chatbot route
@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        user_message = data.get('message', '')
        
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400
        
        # Initialize Gemini model
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Create context about BookFinder
        context = """You are a helpful assistant for BookFinder, a comprehensive book discovery platform. 
        BookFinder helps users search for books across multiple sources including Google Books, Open Library, 
        and Project Gutenberg. Users can save books, get recommendations, and access free public domain books. 
        Answer questions about books, reading suggestions, and help users find what they're looking for. 
        Be friendly, concise, and helpful."""
        
        # Generate response
        chat_session = model.start_chat(history=[])
        response = chat_session.send_message(context + "\n\nUser: " + user_message)
        
        return jsonify({
            'response': response.text,
            'timestamp': datetime.now().strftime('%I:%M %p')
        })
    
    except Exception as e:
        app.logger.error(f"Chat error: {str(e)}")
        return jsonify({'error': 'Failed to process your message. Please try again.'}), 500

@app.route('/chatbot')
def chatbot():
    return render_template('chatbot.html')

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    debug_mode = not os.getenv('RENDER')
    app.run(debug=debug_mode, host="0.0.0.0", port=port)
