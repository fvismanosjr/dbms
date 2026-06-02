from collections import defaultdict
import pickle  # handle complex data types and tuples as dict keys
from typing import Dict, Set, Tuple
from pathlib import Path
from uuid import uuid4

import dbm  # Python's built-in key/value store; replaces Berkeley DB

from messages import *


class DataObject:
    def serialize(self):
        return pickle.dumps(self.__dict__)
    
    @classmethod
    def deserialize(cls, pickled_data):
        data = pickle.loads(pickled_data)
        return cls(**data)


class Table(DataObject):
    def __init__(
        self, 
        table_name: str, 
        columns: Dict[str, str], 
        not_null_keys: Set[str], 
        primary_key: Tuple[str], 
        foreign_keys: Dict[str, Tuple[str, str]],
        referenced_by: Set[str]=set()
    ):
        self.table_name = table_name
        self.columns = columns  # key: column name, value: column referencing_type
        self.not_null_keys = not_null_keys  # set of column names
        self.primary_key = primary_key  # tuple of column names (order is important in this project)
        self.foreign_keys = foreign_keys  # key: referencing column name, value: tuple of (referenced table name, referenced column name)
        self.referenced_by = referenced_by  # set of table names that reference this table
        
    def __str__(self):
        info = "\n-----------------------------------------------------------------\n"
        info += f"table_name [{self.table_name}]\n"
        info += "{:<25}{:<15}{:<10}{:<10}\n".format("column_name", "type", "null", "key")
        for column, column_type in self.columns.items():
            null_str = 'N' if column in self.not_null_keys else 'Y'
            key_str = ''
            if self.primary_key and column in self.primary_key:
                key_str = 'PRI'
            if self.foreign_keys and column in self.foreign_keys:
                key_str = 'FOR'
                if self.primary_key and column in self.primary_key:
                    key_str = 'PRI/FOR'
            info += "{:<25}{:<15}{:<10}{:<10}\n".format(column, column_type, null_str, key_str)
        info += "-----------------------------------------------------------------"
        return info
    
    def __contains__(self, key: tuple):
        return key in self.columns
    
    def check_reference_primary_key(self, referenced_key: str):
        return referenced_key in self.primary_key
    
    def check_reference_type(self, referencing_type: str, referenced_key: str):
        return self.columns[referenced_key] == referencing_type
    
    def has_reference(self):
        return self.referenced_by is not None and len(self.referenced_by) > 0
    
    def get_referencing_tables(self):
        if self.foreign_keys is None or len(self.foreign_keys) == 0:
            return None
        return [table for table, column in self.foreign_keys.values()]
    
    def add_reference(self, table_name):
        self.referenced_by.add(table_name)
        
    def remove_reference(self, table_name):
        self.referenced_by.remove(table_name)
    
'''
table = TableSchema(
    table_name="employees",
    column_names={
        "id": "INTEGER",
        "name": "VARCHAR(255)",
        "age": "INTEGER",
        "department": "VARCHAR(255)"
    },
    not_null_keys={"id", "name", "age"},
    primary_key=("id",),
    foreign_keys={
        "department": ("departments", "department")
    }
)
'''
        

class Record(DataObject):
    def __init__(
        self, 
        table_name: str, 
        data: Dict, 
        primary_value: Tuple,
        referencing: Dict[Tuple, Set],
        referenced_by=defaultdict(set)
    ):
        self.table_name = table_name
        self.data = data
        self.primary_value = primary_value
        self.referencing = referencing  # {(referenced table_name, referenced column): {referenced value...}} 
        self.referenced_by = referenced_by  # {(referencing table_name, referencing column): {referencing value...}}

    def add_to_referenced_by(self, referencing_table, referencing_column, referencing_value):
        self.referenced_by[(referencing_table, referencing_column)].add(referencing_value)
        
    def remove_referenced_by(self, referencing_table, referencing_column, referencing_value):
        self.referenced_by[(referencing_table, referencing_column)].remove(referencing_value)
        if len(self.referenced_by[(referencing_table, referencing_column)]) == 0:
            del self.referenced_by[(referencing_table, referencing_column)]
        
        
        
'''
row = TableRow(
    table_name="employees",
    data={
        "id": 1,
        "name": "John Doe",
        "age": 30,
        "department": "HR"
    },
    primary_value=(1,)
)
'''

class Cursor:
    """Iterates a dbm store and supports delete-at-position.

    Berkeley DB handed back a native cursor; dbm has no cursor concept, so we
    snapshot the keys at creation time and walk them. Snapshotting also makes it
    safe to delete the current record while iterating (which delete()/select()
    rely on) without disturbing the walk.
    """
    def __init__(self, store):
        self._store = store
        self._keys = list(store.keys())
        self._index = -1
        self._current_key = None

    def first(self):
        self._index = 0
        return self._read()

    def next(self):
        self._index += 1
        return self._read()

    def _read(self):
        # Skip over keys that were deleted since the snapshot was taken.
        while self._index < len(self._keys):
            key = self._keys[self._index]
            if key in self._store:
                self._current_key = key
                return key, self._store[key]
            self._index += 1
        self._current_key = None
        return None

    def delete(self):
        if self._current_key is not None and self._current_key in self._store:
            del self._store[self._current_key]

    def close(self):
        pass


class DB:
    """One database, One table"""
    def __init__(self, db_name: str):
        self.db_dir = Path("./DB")
        self.db_dir.mkdir(exist_ok=True)
        self.db_name = db_name
        self.db_file = self.db_dir / (self.db_name + ".db")
        self.DB = None

    def open_db(self):
        # gdbm (the typical Linux/Docker backend) takes an EXCLUSIVE lock on the
        # file and refuses to open a handle that is already open, raising
        # "[Errno 11] Resource temporarily unavailable". Error paths in dbms.py
        # can raise between open_db() and close_db(), leaking an open handle on a
        # long-lived DB object (notably the shared MetaDB); releasing any stale
        # handle here keeps a subsequent statement from crashing on re-open.
        # (Berkeley DB and macOS's ndbm tolerated double-opens; gdbm does not.)
        self.close_db()
        # 'c' opens the store for read/write, creating it if it does not exist.
        self.DB = dbm.open(str(self.db_file), "c")

    def close_db(self):
        if self.DB is not None:
            self.DB.close()
            self.DB = None

    def create_cursor(self):
        return Cursor(self.DB)

    def discard_cursor(self, cursor):
        cursor.close()

    def get_dbname(self):
        return self.db_name

    def create_key_from_value(self, primary_tuple: tuple):  # if has primary key
        return str(primary_tuple).encode()

    def create_random_key(self):  # if no primary key
        return uuid4().bytes

    def exists(self, key):
        return key in self.DB

    def get(self, key):
        if key not in self.DB:
            return None
        dataobj = self.DB[key]
        if not dataobj:
            return None
        return Record.deserialize(dataobj)

    def put(self, key, dataobj):
        self.DB[key] = dataobj.serialize()

    def delete(self, key):
        if key in self.DB:
            del self.DB[key]

    def remove_files(self):
        """Delete every on-disk file backing this store.

        dbm's filename scheme is backend-specific: gdbm (typical on Linux) writes
        a single file at the exact path, ndbm (macOS) appends another ".db", and
        dumb (the only backend on Windows) writes ".dir"/".dat"/".bak" siblings.
        Globbing the base name removes the store on every platform instead of
        assuming gdbm's single-file layout.
        """
        for path in self.db_dir.glob(self.db_file.name + "*"):
            path.unlink()

    def delete_by_cursor(self, cursor):
        cursor.delete()

    def keys(self):
        return list(self.DB.keys())

    def values(self):
        return [self.DB[key] for key in self.DB.keys()]

    def items(self):
        return [(key, self.DB[key]) for key in self.DB.keys()]

    def define_meta(self, meta):
        self.meta = meta
        

class MetaDB(DB):
    """Metadata DB containing table schemas"""
    def __init__(self, db_name="table"):  # identifier
        super().__init__(db_name)
    
    def get(self, key):
        if key not in self.DB:
            return None
        value = self.DB[key]
        if not value:
            return None
        return Table.deserialize(value)

    def create_key_from_value(self, table_name):
        return table_name.encode()