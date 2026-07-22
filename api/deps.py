from advisor import get_db

def get_db_cursor():
    db = get_db()
    cursor = db.cursor()
    try:
        yield cursor
        db.commit()
    finally:
        cursor.close()
        db.close()
