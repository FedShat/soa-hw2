import psycopg
from psycopg.rows import dict_row

from app.config import settings


def get_db():
    # Function-scoped dependencies commit before sending a response.
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        yield connection
