import psycopg
from psycopg.rows import dict_row
from flask import current_app, g


def get_conn():
    if "db_conn" not in g:
        g.db_conn = psycopg.connect(current_app.config["DATABASE_URL"], row_factory=dict_row)
    return g.db_conn


def close_conn(_exc=None):
    conn = g.pop("db_conn", None)
    if conn is not None:
        conn.close()
