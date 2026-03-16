# Project/models.py
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from utils import get_db_connection

class User(UserMixin):
    def __init__(self, id, username, password_hash, gender=None, year_of_study=None):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.gender = gender
        self.year_of_study = year_of_study

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        # Flask-Login needs this to be a string
        return str(self.id)

    @staticmethod
    def get(user_id):
        if user_id is None:
            return None
        conn = get_db_connection()
        try:
            user_row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            if user_row:
                # Use standard dictionary access to avoid sqlite3.Row errors
                return User(
                    id=user_row['id'], 
                    username=user_row['username'], 
                    password_hash=user_row['password_hash'],
                    gender=user_row['gender'] if 'gender' in user_row.keys() else None,
                    year_of_study=user_row['year_of_study'] if 'year_of_study' in user_row.keys() else None
                )
        except Exception as e:
            print(f"Database Error in User.get: {e}")
        finally:
            conn.close()
        return None

    @staticmethod
    def get_by_username(username):
        conn = get_db_connection()
        try:
            user_row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
            if user_row:
                return User(
                    id=user_row['id'], 
                    username=user_row['username'], 
                    password_hash=user_row['password_hash'],
                    gender=user_row['gender'] if 'gender' in user_row.keys() else None,
                    year_of_study=user_row['year_of_study'] if 'year_of_study' in user_row.keys() else None
                )
        except Exception as e:
            print(f"Database Error in User.get_by_username: {e}")
        finally:
            conn.close()
        return None