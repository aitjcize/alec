import sqlite3

class DatabaseManager(object):
    def __init__(self, db):
        self.conn = sqlite3.connect(db)
        self.cur = self.conn.cursor()

    def execute(self, *arg):
        self.cur.execute(*arg)
        self.conn.commit()
        return self.cur

    def __del__(self):
        self.conn.close()
