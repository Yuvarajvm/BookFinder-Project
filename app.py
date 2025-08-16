# app.py — BookFinder (Google Books + Open Library + Gutendx + NYT)

import os
import sqlite3
import hashlib
import requests
from datetime import datetime
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_file, jsonify
)
from werkzeug.utils import secure_filename

try:
    from aws_email import AWSEmailService
except ImportError:
    AWSEmailService = None

# ───────────────────────── INIT ──────────────────────────

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'bookfinder-secret-key-change-in-production')

# Session configuration for better reliability
app.config['SESSION_COOKIE_SECURE'] = False  # Set True in production with HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

email_service = None
if AWSEmailService:
    email_service = AWSEmailService(app)
    if hasattr(email_service, "init_reset_table"):
        email_service.init_reset_table()

print(f"Secret key loaded: {app.secret_key[:10]}..." if app.secret_key else "No secret key!")

# ─────────────────── UPLOAD CONFIG ───────────────────────

UPLOAD_FOLDER = "/tmp/uploads" if os.getenv('RENDER') else "uploads"
ALLOWED_EXTENSIONS = {"pdf", "epub"}
MAX_FILE_SIZE = 50 * 1024 * 1024

app.config.update(UPLOAD_FOLDER=UPLOAD_FOLDER, MAX_CONTENT_LENGTH=MAX_FILE_SIZE)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ───────────────────── HELPERS ───────────────────────────

def allowed_file(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_path():
    return "/tmp/bookfinder.db" if os.getenv('RENDER') else "instance/bookfinder.db"

def init_db():
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    try:
        # Create regular user tables
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

# Initialize admin tables using your existing admin_utils
try:
    from admin.admin_utils import init_admin_db
    init_admin_db()
    print("Admin database initialized")
except ImportError:
    print("Admin utils not found - admin functionality will be limited")

# Initialize regular database
init_db()

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

NYT_API_KEY = os.getenv("NYT_API_KEY")

def search_open_library(q, limit=10, page=1):
    """Open Library works search with preview support."""
    try:
        params = {
            "q": q,
            "limit": limit,
            "page": page,
            "fields": "key,title,author_name,first_publish_year,cover_i,isbn,ia,has_fulltext"
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
            preview_link = ""
            if ia_list and d.get("has_fulltext"):
                preview_link = f"<https://archive.org/details/{ia_list>[0]}"

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
        r = requests.get("https://gutendx.com/books", params={"search": q}, timeout=8)
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

def search_nyt_books(query, limit=10):
    """Search NYT Best Sellers lists for books matching query."""
    if not NYT_API_KEY:
        return []

    try:
        lists_to_search = ["hardcover-fiction", "hardcover-nonfiction", "combined-print-and-e-book-fiction"]
        all_books = []

        for list_name in lists_to_search:
            try:
                url = f"https://api.nytimes.com/svc/books/v3/lists/current/{list_name}.json"
                r = requests.get(url, params={"api-key": NYT_API_KEY}, timeout=8)
                r.raise_for_status()
                data = r.json()

                books = data.get("results", {}).get("books", [])
                for book in books:
                    title = book.get("title", "")
                    author = book.get("author", "")
                    if (query.lower() in title.lower()) or (query.lower() in author.lower()):
                        all_books.append({
                            "id": book.get("primary_isbn13") or book.get("primary_isbn10") or title,
                            "title": title,
                            "author": author,
                            "description": book.get("description") or "NYT Best Seller",
                            "thumbnail": book.get("book_image", ""),
                            "published_date": "",
                            "page_count": 0,
                            "preview_link": "",
                            "info_link": book.get("amazon_product_url") or "",
                            "isbn13": book.get("primary_isbn13") or "",
                            "price": "",
                            "price_value": 999999,
                            "rating": 0,
                            "filename": "",
                            "filepath": "",
                            "source": "nyt"
                        })

                if len(all_books) >= limit:
                    break

            except Exception as list_error:
                print(f"Error fetching NYT list {list_name}: {list_error}")
                continue

        return all_books[:limit]

    except Exception as e:
        print("NYT Books search error:", e)
        return []

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
            a = str(author).split(",").strip().lower()  # FIXED: author not author
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
            
            # FIXED: Correct indexing for home page - CRUCIAL FIX FOR JINJA ERROR
            uploaded = [dict(
                id=r[0],           # books.id
                title=r[1],        # books.title  
                author=r[2],       # books.author
                isbn=r[3] or "",   # books.isbn
                description=r[4],  # books.description
                filename=r[5],     # books.filename ← STRING, NOT TUPLE!
                filepath=r[6],     # books.filepath
                user_id=r[7],      # books.user_id
                upload_date=r[8],  # books.upload_date
                uploader=r[9],     # users.username
                source="uploaded"
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

        if email_service and hasattr(email_service, "send_welcome_email"):
            try:
                email_service.send_welcome_email(e, u)
            except Exception as er:
                print("Welcome email error:", er)

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

    if not all([e, p]):
        return jsonify(success=False, message="Fill in all fields")

    pw_hash = hashlib.sha256(p.encode()).hexdigest()

    try:
        with sqlite3.connect(get_db_path()) as db:
            row = db.execute("SELECT id,username FROM users WHERE email=? AND password=?", (e, pw_hash)).fetchone()

        if row:
            session["user_id"] = row[0]
            session["username"] = row[1]
            return jsonify(success=True, message="Logged in")

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
        return render_template("mybooks.html", books=[], uploaded_books_count=0, total_books_count=0, downloads_count=0, days_since_joined=0)

    user_id = session["user_id"]

    try:
        with sqlite3.connect(get_db_path()) as db:
            rows = db.execute(
                "SELECT b.*, u.username FROM books b JOIN users u ON b.user_id=u.id WHERE b.user_id=? ORDER BY b.upload_date DESC",
                (user_id,)
            ).fetchall()

            # FIXED: Correct indexing for book list
            book_list = [
                dict(
                    id=row[0],           # books.id
                    title=row[1],        # books.title
                    author=row[2],       # books.author
                    isbn=row[3] or "",   # books.isbn
                    description=row[4],  # books.description
                    filename=row[5],     # books.filename
                    filepath=row[6],     # books.filepath
                    user_id=row[7],      # books.user_id
                    upload_date=row[8],  # books.upload_date
                    uploader=row[9],     # users.username (from JOIN)
                    source="uploaded"
                ) for row in rows
            ]

            uploaded_books_count = len(book_list)
            total_books_count = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            downloads_count = db.execute("SELECT COUNT(*) FROM downloads WHERE user_id=?", (user_id,)).fetchone()[0]

            created_row = db.execute("SELECT created_at FROM users WHERE id=?", (user_id,)).fetchone()
            if created_row and created_row[0]:
                try:
                    created_date = datetime.strptime(created_row, "%Y-%m-%d %H:%M:%S")
                    days_since_joined = (datetime.now() - created_date).days
                except Exception:
                    days_since_joined = 0
            else:
                days_since_joined = 0

            return render_template(
                "mybooks.html",
                books=book_list,
                uploaded_books_count=uploaded_books_count,
                total_books_count=total_books_count,
                downloads_count=downloads_count,
                days_since_joined=days_since_joined
            )

    except Exception as e:
        print("my_books error:", e)
        return render_template("mybooks.html", books=[], uploaded_books_count=0, total_books_count=0, downloads_count=0, days_since_joined=0)

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
                    dict(
                        id=r[0],           # books.id
                        title=r[1],        # books.title
                        author=r[2],       # books.author
                        isbn=r[3] or "",   # books.isbn
                        description=r[4],  # books.description
                        filename=r[5],     # books.filename
                        filepath=r[6],     # books.filepath
                        user_id=r[7],      # books.user_id
                        upload_date=r[8],  # books.upload_date
                        uploader=r[9],     # users.username (from JOIN)
                        thumbnail="",
                        published_date="",
                        page_count=0,
                        preview_link=url_for("download_book", book_id=r[0]),
                        info_link="",
                        isbn13="",
                        price="",
                        price_value=0,
                        rating=0,
                        source="uploaded"
                    ) for r in rows
                ]
        except Exception as e:
            print("Local search error:", e)
            local_results = []

    # Merge and sort
    all_results = merge_results(local_results, google_results, openlib_results, gutendx_results, nyt_results)
    sorted_results = sort_results(all_results, sort_by)

    return render_template(
        "results.html",
        results=sorted_results,
        query=query,
        sort_by=sort_by,
        sources_selected=sources_selected,
        total_results=len(all_results),
        show_login_modal=False
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
            row = db.execute(
                "SELECT filepath, filename FROM books WHERE id=?",
                (book_id,)
            ).fetchone()

        if not row:
            return "File not found", 404

        filepath, filename = row

        if not filepath or not os.path.exists(filepath):
            return "File missing on server", 404

        if "user_id" in session:
            try:
                with sqlite3.connect(get_db_path()) as db:
                    db.execute(
                        "INSERT INTO downloads(book_id,user_id) VALUES(?,?)",
                        (book_id, session["user_id"])
                    )
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
            row = db.execute(
                "SELECT filepath, user_id FROM books WHERE id=?",
                (book_id,)
            ).fetchone()

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

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")

    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("Enter email", "error")
        return redirect(url_for("home"))

    with sqlite3.connect(get_db_path()) as db:
        row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()

    if row and email_service:
        try:
            token = email_service.create_reset_token(row[0])
            email_service.send_password_reset(email, token)
        except Exception as er:
            print("Password reset email error:", er)

    flash("If the email exists, a reset link was sent.", "info")
    return redirect(url_for("home"))

# ───────── ADDED: READING ROUTES FOR PDF AND EPUB ─────────

@app.route("/read/<int:book_id>")
def read_book(book_id):
    """Display reader for EPUB or PDF books"""
    if "user_id" not in session:
        return redirect(url_for("home"))
    
    try:
        with sqlite3.connect(get_db_path()) as db:
            row = db.execute(
                "SELECT id, title, filename, filepath FROM books WHERE id=?",
                (book_id,)
            ).fetchone()

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
            row = db.execute(
                "SELECT filepath, filename FROM books WHERE id=?",
                (book_id,)
            ).fetchone()

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
            row = db.execute(
                "SELECT filepath, filename FROM books WHERE id=?",
                (book_id,)
            ).fetchone()

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

# ───────── REGISTER ADMIN BLUEPRINT ─────────

try:
    from admin.admin_routes import admin_bp
    app.register_blueprint(admin_bp)
    print("Admin blueprint registered successfully")
except ImportError:
    print("Admin blueprint not found - admin functionality will not be available")

# ───────── RUN ─────────

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    debug_mode = not os.getenv('RENDER')
    app.run(debug=debug_mode, host="0.0.0.0", port=port)
