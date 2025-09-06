BookFinder
A Flask web app to search books via Google Books and browse community uploads.

Features
Search books (Google Books API ,Open Library,Project Gutendx and other Multiple Sources)

Featured books carousel on home page

Upload/download PDF or EPUB (50MB)

User accounts: register, login, logout

“My Books” dashboard with basic stats

Clean, responsive UI

Optional admin panel  (not shown in nav)

Tech Stack
Python, Flask, SQLite

HTML/CSS/JS (Jinja templates)

Slick Carousel for slider

Setup
Clone repo and install:

pip install -r requirements.txt

Run:

python app.py

Open:

http://localhost:5000 or want to see live page(https://bookfinder-app-srb5.onrender.com)

Structure
app.py (routes, logic)

templates/ (home, results, mybooks, upload, contact, viewers)

uploads/ (files)

instance/bookfinder.db (auto-created)

Notes
No API key needed for basic Google Books search.


For production: set a strong SECRET_KEY, consider PostgreSQL, and configure email for password reset.
