# app.py — BookFinder (Google Books + Open Library + Gutendx + NYT)
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
import os
import sqlite3
import hashlib
import requests
import secrets
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# ───────────────────────── INIT ──────────────────────────

load_dotenv()
app = Flask(__name__)

# ✅ FIX: Configure DATABASE_URL before SQLAlchemy initialization
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Configuration with Render environment detection
if os.getenv('RENDER'):
    # Production settings (when deployed on Render)
    app.config.update(
        SECRET_KEY=os.getenv('SECRET_KEY'),
        SQLALCHEMY_DATABASE_URI=DATABASE_URL,  # PostgreSQL on Render
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAIL_SERVER=os.getenv('MAIL_SERVER', 'smtp.gmail.com'),
        MAIL_PORT=int(os.getenv('MAIL_PORT', 587)),
        MAIL_USE_TLS=os.getenv('MAIL_USE_TLS', 'true').lower() == 'true',
        MAIL_USERNAME=os.getenv('MAIL_USERNAME'),
        MAIL_PASSWORD=os.getenv('MAIL_PASSWORD'),
        MAIL_DEFAULT_SENDER=os.getenv('MAIL_DEFAULT_SENDER')
    )
    def get_db_path():
        return '/tmp/bookfinder.db'  # Keep for existing SQLite functions
else:
    # Local development settings
    db_path = os.path.join(os.getcwd(), 'bookfinder.db')
    app.config.update(
        SECRET_KEY=os.getenv('SECRET_KEY', 'dev_secret_key'),
        SQLALCHEMY_DATABASE_URI=DATABASE_URL or f'sqlite:///{db_path}',  # ✅ FIX: Use DATABASE_URL or fallback to SQLite
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAIL_SERVER='smtp.gmail.com',
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME=os.getenv('MAIL_USERNAME'),
        MAIL_PASSWORD=os.getenv('MAIL_PASSWORD'),
        MAIL_DEFAULT_SENDER=os.getenv('MAIL_DEFAULT_SENDER')
    )
    def get_db_path():
        return db_path

# Session configuration for better reliability
app.config['SESSION_COOKIE_SECURE'] = False  # Set True in production with HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Initialize extensions
mail = Mail(app)
db = SQLAlchemy(app)

# SQLAlchemy Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    books = db.relationship('Book', backref='owner', lazy=True)

class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(200))
    isbn = db.Column(db.String(20))
    description = db.Column(db.Text)
    filename = db.Column(db.String(255))
    filepath = db.Column(db.String(255))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)

class Download(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey('book.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    download_date = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.String(50), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer)
    review_text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Password Reset Tokens (in-memory for demo)
reset_tokens = {}

print(f"Secret key loaded: {app.config['SECRET_KEY'][:10]}..." if app.config['SECRET_KEY'] else "No secret key!")

# ─────────────────── UPLOAD CONFIG ───────────────────────

UPLOAD_FOLDER = "/tmp/uploads" if os.getenv('RENDER') else "uploads"
ALLOWED_EXTENSIONS = {"pdf", "epub"}
MAX_FILE_SIZE = 50 * 1024 * 1024
app.config.update(UPLOAD_FOLDER=UPLOAD_FOLDER, MAX_CONTENT_LENGTH=MAX_FILE_SIZE)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ───────────────────── HELPERS ───────────────────────────

def allowed_file(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    """Initialize SQLite database for backward compatibility"""
    db_path = get_db_path()
    if not os.path.exists(os.path.dirname(db_path)):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, username TEXT UNIQUE,
            email TEXT UNIQUE, password TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS books(
            id INTEGER PRIMARY KEY, title TEXT, author TEXT,
            isbn TEXT, description TEXT, filename TEXT,
            filepath TEXT, user_id INTEGER,
            upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS downloads(
            id INTEGER PRIMARY KEY, book_id INTEGER, user_id INTEGER,
            download_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reviews(
            id INTEGER PRIMARY KEY, book_id TEXT, user_id INTEGER,
            rating INTEGER, review_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.commit()
    finally:
        conn.close()

# Initialize admin tables
try:
    from admin.admin_utils import init_admin_db
    init_admin_db()
    print("Admin database initialized")
except ImportError:
    print("Admin utils not found - admin functionality will be limited")

# Initialize database
init_db()

# Email Functions
def send_welcome_email(email, username):
    """Send welcome email to new users"""
    try:
        msg = Message(
            'Welcome to BookFinder!',
            recipients=[email]
        )
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
        msg = Message(
            'Reset Your BookFinder Password',
            recipients=[email]
        )
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

# ───────────────────── EXTERNAL SOURCES ───────────────────────────

def search_google_books(q, max_results=50):
    """Google Books — no key needed for basic search."""
    try:
        data = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": q, "maxResults": max_results, "printType": "books"},
            timeout=10
        ).json()

        books = []
        for it in data.get("items", []):
            v = it.get("volumeInfo", {})
            desc_raw = v.get("description") or "No description"
            desc = (desc_raw[:300] + "...") if len(desc_raw) > 300 else desc_raw
            thumb = (v.get("imageLinks", {}) or {}).get("thumbnail", "")
            if thumb.startswith("http:"):
                thumb = thumb.replace("http:", "https:")

            # Price (if available)
            sale = it.get("saleInfo", {}) or {}
            list_price = (sale.get("listPrice") or {})
            price_amount = list_price.get("amount")
            price_currency = list_price.get("currencyCode")
            price_str = f"{price_amount} {price_currency}" if (price_amount is not None and price_currency) else ""
            rating = v.get("averageRating", 0)

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
                "rating": rating,
                "filename": "",
                "filepath": "",
                "source": "google_books",
            })

        return books

    except Exception as e:
        print("Google Books error:", e)
        return []

def search_open_library(q, limit=10, page=1):
    """Open Library works search with preview support."""
    try:
        params = {
            "q": q,
            "limit": limit,
            "page": page,
            "fields": "key,title,author_name,first_publish_year,cover_i,isbn,ia,has_fulltext,public_scan_b"
        }
        r = requests.get("https://openlibrary.org/search.json", params=params, timeout=8)
        r.raise_for_status()
        data = r.json()

        results = []
        for d in data.get("docs", []):
            authors = d.get("author_name") or []
            cover = f"https://covers.openlibrary.org/b/id/{d['cover_i']}-M.jpg" if d.get("cover_i") else ""
            isbn_list = d.get("isbn") or []
            isbn13 = next((i for i in isbn_list if len(i) == 13), "")

            publish_year = d.get("first_publish_year")
            if isinstance(publish_year, (int, float)):
                publish_date = str(int(publish_year))
            else:
                publish_date = str(publish_year) if publish_year else ""

            ia_list = d.get("ia") or []
            has_fulltext = d.get("has_fulltext", False)
            public_scan = d.get("public_scan_b", False)
            preview_available = bool(ia_list and (has_fulltext or public_scan))
            preview_link = f"<https://archive.org/details/{ia_list>[0]}" if preview_available else ""

            results.append({
                "id": d.get("key", ""),
                "title": d.get("title", "Unknown Title"),
                "author": ", ".join(authors) if authors else "Unknown Author",
                "description": "Available on Open Library",
                "thumbnail": cover,
                "published_date": publish_date,
                "page_count": 0,
                "preview_link": preview_link,
                "info_link": f"https://openlibrary.org{d.get('key')}" if d.get("key") else "",
                "isbn13": isbn13,
                "price": "",
                "price_value": 0,
                "rating": 0,
                "filename": "",
                "filepath": "",
                "source": "openlibrary"
            })

        return results

    except Exception as e:
        print("Open Library error:", e)
        return []

def search_gutendx(q, limit=10):
    """Gutendx (Project Gutenberg public-domain ebooks)."""
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

        return out

    except Exception as e:
        print("Gutendx error:", e)
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
                # Filter by query if provided
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

# Cache for NYT results
nyt_cache = {"data": None, "timestamp": 0}
CACHE_DURATION = 300  # 5 minutes

def get_nyt_books_cached(query=None, limit=10):
    current_time = time.time()
    
    # Use cache if fresh
    if nyt_cache["data"] and (current_time - nyt_cache["timestamp"]) < CACHE_DURATION:
        books = nyt_cache["data"]
    else:
        # Fetch fresh data
        books = search_nyt_books(query, limit)
        nyt_cache["data"] = books
        nyt_cache["timestamp"] = current_time
    
    return books

def get_book_reviews(book_id):
    """Get reviews for a specific book from database."""
    try:
        with sqlite3.connect(get_db_path()) as db:
            rows = db.execute(
                "SELECT u.username, r.rating, r.review_text FROM reviews r "
                "JOIN users u ON r.user_id = u.id "
                "WHERE r.book_id = ? ORDER BY r.created_at DESC LIMIT 3",
                (str(book_id),)
            ).fetchall()
            return [{"username": row[0], "rating": row[1], "text": row[2]} for row in rows]
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
    """Sort results based on sort_by parameter."""
    if sort_by == "price_low":
        return sorted(results, key=lambda x: x.get("price_value", 0))
    elif sort_by == "price_high":
        return sorted(results, key=lambda x: x.get("price_value", 0), reverse=True)
    elif sort_by == "rating":
        return sorted(results, key=lambda x: x.get("rating", 0), reverse=True)
    elif sort_by == "new":
        return sorted(results, key=lambda x: x.get("published_date", ""), reverse=True)
    elif sort_by == "discount":
        return sorted(results, key=lambda x: (x.get("price_value", 0) == 0, -x.get("price_value", 0)), reverse=True)
    else:
        return results

# ───────────────────── ROUTES ─────────────────────────────

@app.route("/")
def home():
    featured = search_google_books("bestseller fiction", 6)
    uploaded = []
    try:
        with sqlite3.connect(get_db_path()) as c:
            rows = c.execute(
                "SELECT b.*, u.username FROM books b JOIN users u ON b.user_id=u.id "
                "ORDER BY b.upload_date DESC LIMIT 6"
            ).fetchall()
            
            uploaded = [dict(
                id=r[0], title=r[1], author=r[2], isbn=r[3] or "", description=r[4],
                filename=r[5], filepath=r[6], user_id=r[7], upload_date=r[8],
                uploader=r[9], source="uploaded"
            ) for r in rows]
            
    except Exception as e:
        print("DB error:", e)

    return render_template("home.html", google_books=featured, uploaded_books=uploaded)

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
        with sqlite3.connect(get_db_path()) as db:
            db.execute("INSERT INTO users(username,email,password) VALUES(?,?,?)", (u, e, pw_hash))
            db.commit()

        # Send welcome email
        send_welcome_email(e, u)

        return jsonify(success=True, message="Account created! Please log in.")

    except sqlite3.IntegrityError:
        return jsonify(success=False, message="Username or email exists")
    except Exception as err:
        print("Register error:", err)
        return jsonify(success=False, message="Registration failed")

@app.route("/login", methods=["POST"])
def login():
    e = request.form.get("email", "").strip().lower()
    p = request.form.get("password", "").strip()
    
    # Get the current search query and source if available
    search_query = request.form.get("search_query", "")
    search_source = request.form.get("search_source", "")

    if not all([e, p]):
        return jsonify(success=False, message="Fill in all fields")

    pw_hash = hashlib.sha256(p.encode()).hexdigest()

    try:
        with sqlite3.connect(get_db_path()) as db:
            row = db.execute("SELECT id,username FROM users WHERE email=? AND password=?", (e, pw_hash)).fetchone()

        if row:
            session["user_id"] = row[0]
            session["username"] = row[1]
            
            # If there was a search query, redirect back to search results
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
        return jsonify(success=False, message="Please log in")

    f = request.files.get("file")
    title = request.form.get("title", "").strip()

    if not f or f.filename == "" or not allowed_file(f.filename):
        return jsonify(success=False, message="Invalid file")
    if not title:
        return jsonify(success=False, message="Book title is required")

    author = request.form.get("author", "").strip() or "Unknown Author"
    isbn = request.form.get("isbn", "").strip()
    desc = request.form.get("description", "").strip() or "No description"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name, ext = os.path.splitext(secure_filename(f.filename))
    unique = f"{timestamp}_{name}{ext}"
    path = os.path.join(app.config["UPLOAD_FOLDER"], unique)

    try:
        f.save(path)
        with sqlite3.connect(get_db_path()) as db:
            db.execute(
                "INSERT INTO books(title,author,isbn,description,filename,filepath,user_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (title, author, isbn, desc, unique, path, session["user_id"])
            )
            db.commit()

        return jsonify(success=True, message="Book uploaded successfully!", redirect=url_for("my_books"))

    except Exception as err:
        if os.path.exists(path):
            os.remove(path)
        print("Upload error:", err)
        return jsonify(success=False, message="Upload failed")

@app.route("/my-books")
def my_books():
    if "user_id" not in session:
        return render_template("mybooks.html", books=[], uploaded_books_count=0, 
                              total_books_count=0, downloads_count=0, days_since_joined=0)

    user_id = session["user_id"]

    try:
        with sqlite3.connect(get_db_path()) as db:
            rows = db.execute(
                "SELECT b.*, u.username FROM books b JOIN users u ON b.user_id=u.id WHERE b.user_id=? ORDER BY b.upload_date DESC",
                (user_id,)
            ).fetchall()

            book_list = [
                dict(id=row[0], title=row[1], author=row[2], isbn=row[3] or "",
                    description=row[4], filename=row[5], filepath=row[6],
                    user_id=row[7], upload_date=row[8], uploader=row[9], source="uploaded"
                ) for row in rows
            ]

            uploaded_books_count = len(book_list)
            total_books_count = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            downloads_count = db.execute("SELECT COUNT(*) FROM downloads WHERE user_id=?", (user_id,)).fetchone()[0]

            created_row = db.execute("SELECT created_at FROM users WHERE id=?", (user_id,)).fetchone()
            if created_row and created_row[0]:
                try:
                    created_date = datetime.strptime(created_row[0], "%Y-%m-%d %H:%M:%S")
                    days_since_joined = (datetime.now() - created_date).days
                except Exception:
                    days_since_joined = 0
            else:
                days_since_joined = 0

            return render_template(
                "mybooks.html", books=book_list, uploaded_books_count=uploaded_books_count,
                total_books_count=total_books_count, downloads_count=downloads_count,
                days_since_joined=days_since_joined
            )

    except Exception as e:
        print("my_books error:", e)
        return render_template("mybooks.html", books=[], uploaded_books_count=0,
                              total_books_count=0, downloads_count=0, days_since_joined=0)

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

    # External sources
    google_results = search_google_books(query, 20) if not sources_selected or "google_books" in sources_selected else []
    openlib_results = search_open_library(query, limit=10) if not sources_selected or "openlibrary" in sources_selected else []
    gutendx_results = search_gutendx(query, limit=10) if not sources_selected or "gutendx" in sources_selected else []
    nyt_results = search_nyt_books(query, limit=8) if not sources_selected or "nyt" in sources_selected else []

    # Local DB search
    local_results = []
    if not sources_selected or "uploaded" in sources_selected:
        try:
            with sqlite3.connect(get_db_path()) as db:
                like = f"%{query}%"
                rows = db.execute(
                    "SELECT b.*, u.username FROM books b JOIN users u ON b.user_id=u.id "
                    "WHERE (b.title LIKE ? OR b.author LIKE ? OR b.description LIKE ?) "
                    "ORDER BY b.upload_date DESC",
                    (like, like, like)
                ).fetchall()

                local_results = [
                    dict(id=r[0], title=r[1], author=r[2], isbn=r[3] or "",
                        description=r[4], filename=r[5], filepath=r[6], user_id=r[7],
                        upload_date=r[8], uploader=r[9], thumbnail="", published_date="",
                        page_count=0, preview_link=url_for("download_book", book_id=r[0]),
                        info_link="", isbn13="", price="", price_value=0, rating=0, source="uploaded"
                    ) for r in rows
                ]
        except Exception as e:
            print("Local search error:", e)
            local_results = []

    # Merge and sort
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
        with sqlite3.connect(get_db_path()) as db:
            db.execute(
                "INSERT INTO books(title,author,isbn,description,filename,filepath,user_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (title, author, "", description, "", download_url, session["user_id"])
            )
            db.commit()
        return jsonify(success=True, message="Added to My Books")
    except Exception as e:
        print("add_free_book error:", e)
        return jsonify(success=False, message="Failed to add book")

@app.route("/download/<int:book_id>")
def download_book(book_id):
    try:
        with sqlite3.connect(get_db_path()) as db:
            row = db.execute("SELECT filepath, filename FROM books WHERE id=?", (book_id,)).fetchone()

        if not row:
            return "File not found", 404

        filepath, filename = row

        if not filepath or not os.path.exists(filepath):
            return "File missing on server", 404

        if "user_id" in session:
            try:
                with sqlite3.connect(get_db_path()) as db:
                    db.execute("INSERT INTO downloads(book_id,user_id) VALUES(?,?)", (book_id, session["user_id"]))
                    db.commit()
            except Exception:
                pass

        return send_file(filepath, as_attachment=True, download_name=filename)

    except Exception as e:
        print("download_book error:", e)
        return "Error downloading file", 500

@app.route("/delete_book/<int:book_id>", methods=["DELETE"])
def delete_book(book_id):
    if "user_id" not in session:
        return jsonify(success=False, message="Please log in")

    try:
        with sqlite3.connect(get_db_path()) as db:
            row = db.execute("SELECT filepath, user_id FROM books WHERE id=?", (book_id,)).fetchone()

        if not row:
            return jsonify(success=False, message="Book not found")

        filepath, owner_id = row

        if owner_id != session["user_id"]:
            return jsonify(success=False, message="You are not the owner")

        if filepath and os.path.exists(filepath):
            os.remove(filepath)

        with sqlite3.connect(get_db_path()) as db:
            db.execute("DELETE FROM books WHERE id=?", (book_id,))
            db.execute("DELETE FROM downloads WHERE book_id=?", (book_id,))
            db.commit()

        return jsonify(success=True, message="Book deleted")

    except Exception as e:
        print("delete_book error:", e)
        return jsonify(success=False, message=f"Error: {e}")

# ═══════════ PASSWORD RESET ROUTES - FIXED FOR NO DUPLICATES ═══════════

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
        with sqlite3.connect(get_db_path()) as db:
            row = db.execute("SELECT id, username FROM users WHERE email=?", (email,)).fetchone()

        if row:
            token = secrets.token_urlsafe(32)
            reset_tokens[token] = {
                'user_id': row[0],
                'email': email,
                'expires': datetime.utcnow() + timedelta(hours=1)
            }
            send_password_reset_email(email, row[1], token)

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
            with sqlite3.connect(get_db_path()) as db:
                db.execute("UPDATE users SET password = ? WHERE id = ?", 
                          (pw_hash, token_data['user_id']))
                db.commit()
            
            reset_tokens.pop(token, None)
            flash('Password reset successful! Please log in with your new password.', 'success')
            return redirect(url_for('home'))
        except Exception as e:
            print(f"Database error: {e}")
            flash('An error occurred. Please try again.', 'danger')
            return render_template('reset_password.html')
    
    return render_template('reset_password.html')

# ───────── READING ROUTES FOR PDF AND EPUB ─────────

@app.route("/read/<int:book_id>")
def read_book(book_id):
    """Display reader for EPUB or PDF books"""
    if "user_id" not in session:
        return redirect(url_for("home"))
    
    try:
        with sqlite3.connect(get_db_path()) as db:
            row = db.execute("SELECT id, title, filename, filepath FROM books WHERE id=?", (book_id,)).fetchone()

        if not row:
            return "Book not found", 404

        book_id, title, filename, filepath = row
        
        if not filename:
            return "No file attached to this book", 400
            
        file_extension = filename.lower().split('.')[-1]

        # Route to appropriate reader based on file type
        if file_extension == 'epub':
            return render_template("epub_reader.html", book_id=book_id, title=title)
        elif file_extension == 'pdf':
            return render_template("pdf_viewer.html", book_id=book_id, title=title)
        else:
            return f"Unsupported file format: {file_extension}", 400

    except Exception as e:
        print("read_book error:", e)
        return "Error loading book reader", 500

@app.route("/serve_epub/<int:book_id>")
def serve_epub(book_id):
    """Serve EPUB files for the reader"""
    try:
        with sqlite3.connect(get_db_path()) as db:
            row = db.execute("SELECT filepath, filename FROM books WHERE id=?", (book_id,)).fetchone()

        if not row:
            return "EPUB file not found", 404

        filepath, filename = row

        if not filepath or not os.path.exists(filepath):
            return "EPUB file missing on server", 404

        if not filename.lower().endswith('.epub'):
            return "This is not an EPUB file", 400

        return send_file(filepath, as_attachment=False, mimetype='application/epub+zip')

    except Exception as e:
        print("serve_epub error:", e)
        return "Error serving EPUB file", 500

@app.route("/serve_pdf/<int:book_id>")
def serve_pdf(book_id):
    """Serve PDF files for the viewer"""
    try:
        with sqlite3.connect(get_db_path()) as db:
            row = db.execute("SELECT filepath, filename FROM books WHERE id=?", (book_id,)).fetchone()

        if not row:
            return "PDF file not found", 404

        filepath, filename = row

        if not filepath or not os.path.exists(filepath):
            return "PDF file missing on server", 404

        if not filename.lower().endswith('.pdf'):
            return "This is not a PDF file", 400

        return send_file(filepath, as_attachment=False, mimetype='application/pdf')

    except Exception as e:
        print("serve_pdf error:", e)
        return "Error serving PDF file", 500

@app.route('/test-email')
def test_email():
    try:
        msg = Message(
            "🧪 Test Email from BookFinder",
            recipients=[app.config['MAIL_USERNAME']]  # Send to yourself
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

# ───────── REGISTER ADMIN BLUEPRINT ─────────

try:
    from admin.admin_routes import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')
    print("Admin blueprint registered successfully")
except ImportError:
    print("Admin blueprint not found - admin functionality will not be available")

# ✅ REMOVED THE PROBLEMATIC db.create_all() CALL TO PREVENT DATA LOSS
# Initialize SQLAlchemy database - COMMENTED OUT FOR PRODUCTION
# with app.app_context():
#     try:
#         db.create_all()
#         print("✅ SQLAlchemy database created successfully!")
#     except Exception as e:
#         print(f"❌ Database creation failed: {e}")

# ───────── RUN ─────────

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    debug_mode = not os.getenv('RENDER')
    app.run(debug=debug_mode, host="0.0.0.0", port=port)
