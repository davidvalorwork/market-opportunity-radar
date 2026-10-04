"""One serialized local connection; no live cursor escapes its shared lock."""
from threading import RLock


class Rows:
    def __init__(self, cursor):
        self.rowcount = cursor.rowcount
        self._rows = iter(cursor.fetchall() if cursor.description else ())
        cursor.close()

    def fetchone(self):
        return next(self._rows, None)

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return self._rows


class SerializedConnection:
    def __init__(self, connection):
        self._connection = connection
        self.lock = RLock()

    def execute(self, sql, parameters=()):
        with self.lock:
            return Rows(self._connection.execute(sql, parameters))

    def executescript(self, sql):
        with self.lock:
            self._connection.executescript(sql).close()

    def close(self):
        with self.lock:
            self._connection.close()
