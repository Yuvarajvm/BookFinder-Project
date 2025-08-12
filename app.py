# app.py – COMPLETE VERSION FOR BOOKFINDER FLASK APP
import os, sqlite3, hashlib, requests
from datetime import datetime
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_file, jsonify
)
from werkzeug.utils import secure_filename

from aws_email import AWSEmailService  # Change to aws_email if using AWS SES

# ─────────────────────────  INIT  ──────────────────────────
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'bookfinder-secret-key-change-in-production')

email_service = AWSEmailService(app)  # Change to AWSEmailService if using AWS SES
email_service.init_reset_table()

# ───────────────────  UPLOAD CONFIG  ───────────────────────
UPLOAD_FOLDER = "/tmp/uploads" if os.getenv('RENDER') else "uploads"
ALLOWED_EXTENSIONS = {"pdf", "epub"}
MAX_FILE_SIZE = 50 * 1024 * 1024
app.config.update(UPLOAD_FOLDER=UPLOAD_FOLDER, MAX_CONTENT_LENGTH=MAX_FILE_SIZE)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─────────────────────  HELPERS  ───────────────────────────
def allowed_file(fn): 
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def search_google_books(q, max_results=20):
    try:
        data = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": q, "maxResults": max_results, "printType": "books"},
            timeout=10
        ).json()
        books = []
        for it in data.get("items", []):
            v = it.get("volumeInfo", {})
            desc = (v.get("description") or "No description")[:300] + "..."
            thumb = v.get("imageLinks", {}).get("thumbnail", "").replace("http:", "https:")
            books.append({
                "id": it.get("id",""), "title": v.get("title","Unknown Title"),
                "author": ", ".join(v.get("authors",["Unknown Author"])),
                "description": desc, "thumbnail": thumb,
                "published_date": v.get("publishedDate",""),
                "page_count": v.get("pageCount",0),
                "preview_link": v.get("previewLink",""),
                "info_link": v.get("infoLink",""), "source": "google_books",
            })
        return books
    except Exception as e:
        print("Google Books error:", e); return []

# ─────────────────────  DB  ────────────────────────────────
def get_db_path():
    return "/tmp/bookfinder.db" if os.getenv('RENDER') else "instance/bookfinder.db"

def init_db():
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    with sqlite3.connect(db_path) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, username TEXT UNIQUE,
            email TEXT UNIQUE, password TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS books(
            id INTEGER PRIMARY KEY, title TEXT, author TEXT,
            isbn TEXT, description TEXT, filename TEXT,
            filepath TEXT, user_id INTEGER,
            upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS downloads(
            id INTEGER PRIMARY KEY, book_id INTEGER, user_id INTEGER,
            download_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        """); 
        c.commit()

init_db()

# ─────────────────────  ROUTES  ─────────────────────────────
@app.route("/")
def home():
    featured = search_google_books("bestseller fiction", 6)
    try:
        with sqlite3.connect(get_db_path()) as c:
            rows = c.execute(
                "SELECT b.*, u.username FROM books b JOIN users u ON b.user_id=u.id "
                "ORDER BY b.upload_date DESC LIMIT 6").fetchall()
        uploaded = [dict(
            id=r[0], title=r[1], author=r[2], isbn=r[3] or "",
            description=r[4], filename=r[5], filepath=r[6],
            user_id=r[7], upload_date=r[8], uploader=r[9], source="uploaded") for r in rows]
    except Exception as e:
        print("DB error:", e); uploaded=[]
    return render_template("home.html", google_books=featured, uploaded_books=uploaded)

# ───────── REGISTER ─────────
@app.route("/register", methods=["POST"])
def register():
    u, e = request.form.get("username","").strip(), request.form.get("email","").strip().lower()
    p, c  = request.form.get("password","").strip(), request.form.get("confirm_password","").strip()
    if not all([u,e,p,c]):                 return jsonify(success=False, message="Fill in all fields")
    if p!=c:                               return jsonify(success=False, message="Passwords do not match")
    if len(p)<6:                           return jsonify(success=False, message="Password too short")
    pw_hash = hashlib.sha256(p.encode()).hexdigest()
    try:
        with sqlite3.connect(get_db_path()) as c:
            c.execute("INSERT INTO users(username,email,password) VALUES(?,?,?)",(u,e,pw_hash)); c.commit()
        email_service.send_welcome_email(e,u)
        return jsonify(success=True, message="Account created! Please log in.")
    except sqlite3.IntegrityError:
        return jsonify(success=False, message="Username or email exists")
    except Exception as err:
        print("Register error:", err); return jsonify(success=False, message="Registration failed")

# ───────── LOGIN ─────────
@app.route("/login", methods=["POST"])
def login():
    e, p = request.form.get("email","").strip().lower(), request.form.get("password","").strip()
    if not all([e,p]): return jsonify(success=False, message="Fill in all fields")
    pw_hash = hashlib.sha256(p.encode()).hexdigest()
    with sqlite3.connect(get_db_path()) as c:
        row = c.execute("SELECT id,username FROM users WHERE email=? AND password=?", (e,pw_hash)).fetchone()
    if row: session.update(user_id=row[0], username=row[1]); return jsonify(success=True, message="Logged in")
    return jsonify(success=False, message="Invalid credentials")

# ───────── FORGOT PASSWORD ─────────
@app.route("/forgot-password", methods=["POST"], endpoint="forgot_password")
def forgot_password():
    email = request.form.get("email","").strip().lower()
    if not email: flash("Enter email","error"); return redirect(url_for("home"))
    with sqlite3.connect(get_db_path()) as c:
        row = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if row:
        token = email_service.create_reset_token(row[0])
        email_service.send_password_reset(email, token)
    flash("If the email exists, a reset link was sent.", "info")
    return redirect(url_for("home"))

# ───────── RESET PASSWORD ─────────
@app.route("/reset-password/<token>", methods=["GET","POST"])
def reset_password(token):
    user_id = email_service.verify_token(token)
    if not user_id: flash("Invalid/expired link","error"); return redirect(url_for("home"))
    if request.method=="POST":
        p1,p2 = request.form.get("new_password",""), request.form.get("confirm_password","")
        if len(p1)<6 or p1!=p2:
            flash("Passwords must match and be ≥6 chars.","error")
            return render_template("reset_password.html", token=token)
        pw_hash = hashlib.sha256(p1.encode()).hexdigest()
        with sqlite3.connect(get_db_path()) as c:
            c.execute("UPDATE users SET password=? WHERE id=?", (pw_hash,user_id)); c.commit()
        email_service.mark_token_used(token)
        flash("Password updated! Please log in.","success"); return redirect(url_for("home"))
    return render_template("reset_password.html", token=token)

# ───────── LOGOUT ─────────
@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("home"))

# ───────── CONTACT ─────────
@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        if not all(request.form.get(k, "").strip() for k in ("name", "email", "subject", "message")):
            return jsonify(success=False, message="Fill in all fields")
        return jsonify(success=True, message="Thanks! We'll respond soon.")
    return render_template("contact.html")

# ───────── UPLOAD ─────────
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
        with sqlite3.connect(get_db_path()) as c:
            c.execute("INSERT INTO books(title,author,isbn,description,filename,filepath,user_id) "
                      "VALUES (?,?,?,?,?,?,?)", (title, author, isbn, desc, unique, path, session["user_id"]))
            c.commit()
        return jsonify(success=True, message="Book uploaded successfully!", redirect=url_for("my_books"))
    except Exception as err:
        if os.path.exists(path): 
            os.remove(path)
        print("Upload error:", err)
        return jsonify(success=False, message="Upload failed")

# ───────── MY BOOKS ─────────
@app.route("/my-books")
def my_books():
    if "user_id" not in session:
        return render_template(
            "mybooks.html",
            books=[], uploaded_books_count=0,
            total_books_count=0, downloads_count=0,
            days_since_joined=0
        )

    try:
        with sqlite3.connect(get_db_path()) as c:
            books = c.execute(
                "SELECT * FROM books WHERE user_id=? ORDER BY upload_date DESC",
                (session["user_id"],)
            ).fetchall()

            book_list = [
                dict(
                    id=b[0], title=b[1], author=b[2],
                    isbn=b[3] or "", description=b[4],
                    filename=b[5], filepath=b[6],
                    user_id=b[7], upload_date=b[8]
                )
                for b in books
            ]

            uploaded_books_count = len(book_list)
            total_books_count = c.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            downloads_count = c.execute(
                "SELECT COUNT(*) FROM downloads WHERE user_id=?",
                (session["user_id"],)
            ).fetchone()[0]

            created = c.execute(
                "SELECT created_at FROM users WHERE id=?",
                (session["user_id"],)
            ).fetchone()[0]
            
            try:
                created_date = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
                days_since_joined = (datetime.now() - created_date).days
            except:
                days_since_joined = 0

        return render_template(
            "mybooks.html",
            books=book_list,
            uploaded_books_count=uploaded_books_count,
            total_books_count=total_books_count,
            downloads_count=downloads_count,
            days_since_joined=days_since_joined,
        )
    except Exception as e:
        print("my_books error:", e)
        return render_template(
            "mybooks.html",
            books=[], uploaded_books_count=0,
            total_books_count=0, downloads_count=0,
            days_since_joined=0
        )

# ───────── SEARCH ─────────
@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    source = request.args.get("source", "")
    
    if not query:
        return redirect(url_for("home"))

    google_results = search_google_books(query, 20)

    local_results = []
    try:
        with sqlite3.connect(get_db_path()) as c:
            like = f"%{query}%"
            rows = c.execute(
                "SELECT b.*, u.username "
                "FROM books b JOIN users u ON b.user_id=u.id "
                "WHERE (b.title LIKE ? OR b.author LIKE ? OR b.description LIKE ?) "
                "ORDER BY b.upload_date DESC",
                (like, like, like)
            ).fetchall()
            
            local_results = [
                dict(
                    id=r[0], title=r[1], author=r[2], isbn=r[3] or "",
                    description=r[4], filename=r[5], filepath=r[6],
                    user_id=r[7], upload_date=r[8], uploader=r[9],
                    source="uploaded"
                )
                for r in rows
            ]
    except Exception as e:
        print("Local search error:", e)

    all_results = local_results + google_results

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or source == "mybooks":
        return jsonify(results=all_results, query=query)

    return render_template(
        "results.html",
        results=all_results,
        query=query,
        google_results=google_results,
        local_results=local_results,
        total_results=len(all_results),
    )

# ───────── DOWNLOAD ─────────
@app.route("/download/<int:book_id>")
def download_book(book_id):
    try:
        with sqlite3.connect(get_db_path()) as c:
            row = c.execute(
                "SELECT filepath, filename FROM books WHERE id=?",
                (book_id,)
            ).fetchone()

        if not row:
            return "File not found", 404

        filepath, filename = row
        if not os.path.exists(filepath):
            return "File missing on server", 404

        if "user_id" in session:
            try:
                with sqlite3.connect(get_db_path()) as c:
                    c.execute(
                        "INSERT INTO downloads(book_id,user_id) VALUES(?,?)",
                        (book_id, session["user_id"])
                    )
                    c.commit()
            except Exception:
                pass

        return send_file(filepath, as_attachment=True, download_name=filename)

    except Exception as e:
        print("download_book error:", e)
        return "Error downloading file", 500

# ───────── VIEW BOOK ─────────
@app.route("/view_book/<int:book_id>")
def view_book(book_id):
    if "user_id" not in session:
        flash("Please log in to view books.", "error")
        return redirect(url_for("home"))

    try:
        with sqlite3.connect(get_db_path()) as c:
            book = c.execute(
                "SELECT * FROM books WHERE id=?", (book_id,)
            ).fetchone()

        if not book:
            return "Book not found", 404

        try:
            with sqlite3.connect(get_db_path()) as c:
                c.execute(
                    "INSERT INTO downloads(book_id,user_id) VALUES(?,?)",
                    (book_id, session["user_id"])
                )
                c.commit()
        except Exception:
            pass

        filename = book[5]
        ext = (filename or "").split(".")[-1].lower()

        if ext == "pdf":
            return render_template("pdf_viewer.html", book=book)
        else:
            return render_template("epub_reader.html", book=book)

    except Exception as e:
        print("view_book error:", e)
        return "Error loading book", 500

# ───────── DELETE BOOK ─────────
@app.route("/delete_book/<int:book_id>", methods=["DELETE"])
def delete_book(book_id):
    if "user_id" not in session:
        return jsonify(success=False, message="Please log in")

    try:
        with sqlite3.connect(get_db_path()) as c:
            row = c.execute(
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

            c.execute("DELETE FROM books WHERE id=?", (book_id,))
            c.execute("DELETE FROM downloads WHERE book_id=?", (book_id,))
            c.commit()

        return jsonify(success=True, message="Book deleted")

    except Exception as e:
        print("delete_book error:", e)
        return jsonify(success=False, message=f"Error: {e}")

# ───────── ADMIN BLUEPRINT ─────────
try:
    from admin.admin_routes import admin_bp
    from admin.admin_utils import init_admin_db
    app.register_blueprint(admin_bp)
    init_admin_db()
except ImportError:
    print("Admin module not found - continuing without admin functionality")

# ───────── RUN ─────────
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    debug_mode = not os.getenv('RENDER')  # Disable debug on Render
    app.run(debug=debug_mode, host="0.0.0.0", port=port)
