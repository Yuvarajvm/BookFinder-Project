# extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail

# Create extension instances WITHOUT binding to app
db = SQLAlchemy()
mail = Mail()
