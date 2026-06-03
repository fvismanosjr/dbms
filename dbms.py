from pathlib import Path
from typing import Dict, List, Set, Tuple
import itertools
from collections import Counter
from copy import deepcopy

from db_model import Table, Record, DB, MetaDB
from utils import *
from messages import *


class HashIndex:
    """Simple hash index for equality lookups."""
    def __init__(self, table_name: str, column_name: str):
        self.table_name = table_name
        self.column_name = column_name
        self.data = {}  # {value: set(record_keys)}

    def add(self, value, record_key: bytes):
        if value not in self.data:
            self.data[value] = set()
        self.data[value].add(record_key)

    def remove(self, value, record_key: bytes):
        if value in self.data:
            self.data[value].discard(record_key)
            if not self.data[value]:
                del self.data[value]

    def lookup(self, value) -> Set[bytes]:
        return self.data.get(value, set())

    def rebuild(self, table_name: str):
        self.data = {}
        table_db = DB(table_name)
        table_db.open_db()
        cursor = table_db.create_cursor()
        kv = cursor.first()
        while kv:
            key, value = kv
            record = Record.deserialize(value)
            self.add(record.data.get(self.column_name), key)
            kv = cursor.next()
        table_db.discard_cursor(cursor)
        table_db.close_db()


class DBMS:
    def __init__(self):
        self.db_dir = Path("./DB")
        self.db_dir.mkdir(exist_ok=True)
        self.meta_db = MetaDB()
        self.indexes = {}  # {(table_name, column_name): HashIndex}
        self.in_transaction = False
        self.undo_log = []  # List of undo entries
        self._rebuild_indexes()

    def _rebuild_indexes(self):
        """Rebuild all in-memory indexes from persisted table schemas."""
        self.indexes = {}
        self.meta_db.open_db()
        all_tables = self.meta_db.keys()
        for table_key in all_tables:
            table = self.meta_db.get(table_key)
            if table and table.indexes:
                for column_name in table.indexes:
                    idx = HashIndex(table.table_name, column_name)
                    idx.rebuild(table.table_name)
                    self.indexes[(table.table_name, column_name)] = idx
        self.meta_db.close_db()

    def _add_to_indexes(self, table_name: str, record_data: dict, record_key: bytes):
        for (tn, col), idx in self.indexes.items():
            if tn == table_name:
                idx.add(record_data.get(col), record_key)

    def _remove_from_indexes(self, table_name: str, record_data: dict, record_key: bytes):
        for (tn, col), idx in self.indexes.items():
            if tn == table_name:
                idx.remove(record_data.get(col), record_key)

    def _update_indexes(self, table_name: str, old_data: dict, new_data: dict, record_key: bytes):
        for (tn, col), idx in self.indexes.items():
            if tn == table_name:
                old_value = old_data.get(col)
                new_value = new_data.get(col)
                if old_value != new_value:
                    idx.remove(old_value, record_key)
                    idx.add(new_value, record_key)

    def _get_indexed_column_for_lookup(self, where_clause: dict, table_name: str, table: Table):
        """Extract (column_name, value) from a simple equality WHERE for index lookup.
        Handles both flat comparison dicts and nested boolean_test structures."""
        if not where_clause:
            return None, None
        
        # Drill down through nested boolean_terms/factors to find the innermost boolean_test
        condition = where_clause
        while isinstance(condition, dict):
            if "boolean_test" in condition:
                condition = condition["boolean_test"]
            elif "boolean_factors" in condition:
                condition = condition["boolean_factors"]
            elif "boolean_terms" in condition:
                condition = condition["boolean_terms"]
            else:
                break
        
        op = condition.get("op")
        if op != "=":
            return None, None
        left = condition.get("left_operand")
        right = condition.get("right_operand")
        if not left or not right:
            return None, None
        # Try column = value
        if len(left) == 2 and len(right) == 1:
            col_table, col_name = left
            if (not col_table or col_table == table_name) and col_name in table.indexes:
                return col_name, right[0]
        # Try value = column
        if len(left) == 1 and len(right) == 2:
            col_table, col_name = right
            if (not col_table or col_table == table_name) and col_name in table.indexes:
                return col_name, left[0]
        return None, None

    def _log_undo(self, entry: tuple):
        if self.in_transaction:
            self.undo_log.append(entry)

    # ========================================================================
    # CREATE TABLE
    # ========================================================================
    def create_table(self, table_dict: dict):
        table_name = table_dict["table_name"]
        column_list = table_dict["column_list"]
        not_null_key_set = table_dict["not_null_key_set"]
        primary_key_list = table_dict["primary_key_list"]
        foreign_key_dict = table_dict["foreign_key_dict"]

        if len(set([column_name for column_name, _ in column_list])) < len(column_list):
            raise DuplicateColumnDefError()
        columns = {column_name: column_type for column_name, column_type in column_list}
        
        for data_type in columns.values():
            if data_type.startswith("char"):
                if eval_char_max_len(data_type) < 1:
                    raise CharLengthError()
        
        if len(primary_key_list) > 1:
            raise DuplicatePrimaryKeyDefError()
        elif len(primary_key_list) == 0:
            primary_key = None
        else:
            primary_key = primary_key_list[0]
            for key in primary_key:
                if key not in columns:
                    raise NonExistingColumnDefError(key)
            not_null_key_set.update(primary_key)
        
        if foreign_key_dict:
            for foreign_key in foreign_key_dict:
                if foreign_key not in columns:
                    raise NonExistingColumnDefError(foreign_key)   

        self.meta_db.open_db()
        
        table_key = self.meta_db.create_key_from_value(table_name)
        if self.meta_db.exists(table_key):
            raise TableExistenceError()
        
        if foreign_key_dict:
            for foreign_key, (referenced_table_name, referenced_key) in foreign_key_dict.items():
                referenced_table_key = self.meta_db.create_key_from_value(referenced_table_name)
                referenced_table = self.meta_db.get(referenced_table_key)
                if not referenced_table:
                    raise ReferenceTableExistenceError()
                if referenced_key not in referenced_table:
                    raise ReferenceColumnExistenceError()
                if not referenced_table.check_reference_primary_key(referenced_key):
                    raise ReferenceNonPrimaryKeyError()
                foreign_key_type = columns[foreign_key]
                if not referenced_table.check_reference_type(foreign_key_type, referenced_key):
                    raise ReferenceTypeError()
                referenced_table.add_reference(table_name)
                self.meta_db.put(referenced_table_key, referenced_table)
        
        table = Table(
            table_name=table_name,
            columns=columns,
            not_null_keys=not_null_key_set,
            primary_key=primary_key,
            foreign_keys=foreign_key_dict
        )
        self.meta_db.put(table_key, table)
        self.meta_db.close_db()
        
        table_db = DB(table_name)
        table_db.open_db()
        table_db.close_db()
        
        return CreateTableSuccess(table_name)
    
    # ========================================================================
    # DROP TABLE
    # ========================================================================
    def drop_table(self, table_name: str):
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        if table.has_reference():
            raise DropReferencedTableError(table_name)
        referencing_tables = table.get_referencing_tables()
        if referencing_tables:
            for referencing_table in referencing_tables:
                referencing_table_key = self.meta_db.create_key_from_value(referencing_table)
                referencing_table_db = self.meta_db.get(referencing_table_key)
                referencing_table_db.remove_reference(table_name)
                self.meta_db.put(referencing_table_key, referencing_table_db)
        self.meta_db.delete(table_key)
        
        # Remove any indexes for this table
        for (tn, col) in list(self.indexes.keys()):
            if tn == table_name:
                del self.indexes[(tn, col)]
        
        DB(table_name).remove_files()
        self.meta_db.close_db()
        
        return DropSuccess(table_name)
    
    # ========================================================================
    # EXPLAIN / DESCRIBE / DESC
    # ========================================================================
    def explain_describe_desc(self, table_name: str):
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        self.meta_db.close_db()
        # Add index info
        info = str(table)
        if table.indexes:
            info += "\nIndexes:\n"
            for col in table.indexes:
                info += f"  {col}\n"
        return info
    
    # ========================================================================
    # SHOW TABLES
    # ========================================================================
    def show_tables(self):
        self.meta_db.open_db()
        output = "\n------------------------\n"
        all_tables = self.meta_db.keys()
        for table_key in all_tables:
            output += table_key.decode() + "\n"
        output += "------------------------"
        self.meta_db.close_db()
        return output
    
    # ========================================================================
    # INSERT
    # ========================================================================
    def insert(self, table_dict: dict, value_list: list):
        table_name = table_dict["table_name"]
        column_name_list = table_dict["column_name_list"]
        
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        self.meta_db.close_db()
        
        if column_name_list:
            if len(column_name_list) != len(value_list):
                raise InsertTypeMismatchError()
            for column_name in column_name_list:
                if column_name not in table:
                    raise InsertColumnExistenceError(column_name)
            
        if len(table.columns.keys()) != len(value_list):
            raise InsertTypeMismatchError()
        
        for column_name, value in zip(table.columns.keys(), value_list):
            if value is None and column_name in table.not_null_keys:
                raise InsertColumnNonNullableError(column_name)
        
        if not all([is_valid_type(data_type, value) for data_type, value in zip(table.columns.values(), value_list)]):
            raise InsertTypeMismatchError()
        
        data = {}
        primary_value = []
        referencing = dict()
        for (column_name, data_type), value in zip(table.columns.items(), value_list):
            if data_type.startswith("char") and value is not None:
                max_len = eval_char_max_len(data_type)
                value = value[:max_len]
            if table.primary_key and column_name in table.primary_key:
                primary_value.append(value)
            if table.foreign_keys and column_name in table.foreign_keys:
                referenced_table_name, referenced_column_name = table.foreign_keys[column_name]
                self.meta_db.open_db()
                referenced_table_key = self.meta_db.create_key_from_value(referenced_table_name)
                referenced_table = self.meta_db.get(referenced_table_key)
                self.meta_db.close_db()
                referenced_table_db = DB(referenced_table_name)
                referenced_table_db.open_db()
                referenced_key = referenced_table_db.create_key_from_value((value,))
                referenced_record = None
                if len(referenced_table.primary_key) == 1:
                    referenced_record = referenced_table_db.get(referenced_key)
                else:
                    all_primary_values = referenced_table_db.keys()
                    for primary_value_item in all_primary_values:
                        if referenced_key.decode() in primary_value_item.decode():
                            referenced_record = referenced_table_db.get(primary_value_item)
                            break
                if referenced_record is None:
                    raise InsertReferentialIntegrityError()
                referencing[(referenced_table_name, referenced_column_name)] = {referenced_record.data[referenced_column_name]}
                assert referenced_record.data[referenced_column_name] == value
                referenced_record.add_to_referenced_by(table_name, column_name, value)
                referenced_table_db.put(referenced_key, referenced_record)
                referenced_table_db.close_db()
            data[column_name] = value
        primary_value = tuple(primary_value) if primary_value else None
        record = Record(table_name, data, primary_value, referencing)
        
        table_db = DB(table_name)
        table_db.open_db()
        record_key = table_db.create_key_from_value(primary_value) if primary_value else table_db.create_random_key()
        if table_db.exists(record_key):
            raise InsertDuplicatePrimaryKeyError()
        table_db.put(record_key, record)
        self._add_to_indexes(table_name, data, record_key)
        self._log_undo(('insert', table_name, record_key))
        table_db.close_db()
        
        return InsertResult()

    # ========================================================================
    # DELETE
    # ========================================================================
    def delete(self, table_name: str, where_clause: str):
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        self.meta_db.close_db()
        
        table_db = DB(table_name)
        table_db.open_db()
        
        # Check if we can use an index
        indexed_col, lookup_value = self._get_indexed_column_for_lookup(where_clause, table_name, table)
        use_index = indexed_col is not None
        
        if use_index:
            idx = self.indexes[(table_name, indexed_col)]
            candidate_keys = list(idx.lookup(lookup_value))
            records_to_process = []
            for key in candidate_keys:
                value = table_db.DB.get(key)
                if value:
                    records_to_process.append((key, Record.deserialize(value)))
        else:
            outer_cursor = table_db.create_cursor()
            records_to_process = []
            key_value_pair = outer_cursor.first()
            while key_value_pair:
                key, value = key_value_pair
                records_to_process.append((key, Record.deserialize(value)))
                key_value_pair = outer_cursor.next()
            table_db.discard_cursor(outer_cursor)
        
        success_cnt = 0
        fail_cnt = 0
        
        for key, record in records_to_process:
            satisfies = self._evaluate_condition(deepcopy(where_clause), [table], record.data) if where_clause else True
            if satisfies == True:
                if list(record.referenced_by.values()):
                    fail_cnt += 1
                else:
                    if record.referencing:
                        for (referenced_table_name, referenced_column_name), referenced_value_set in record.referencing.items():
                            for referenced_value in referenced_value_set:
                                referenced_table_db = DB(referenced_table_name)
                                referenced_table_db.open_db()
                                inner_cursor = referenced_table_db.create_cursor()
                                key_value_pair = inner_cursor.first()
                                while key_value_pair:
                                    key_ref, value_ref = key_value_pair
                                    referenced_record = Record.deserialize(value_ref)
                                    for column in table.columns:
                                        if ((table_name, column) in referenced_record.referenced_by and 
                                            referenced_value in referenced_record.referenced_by[(table_name, column)]):
                                            referenced_record.remove_referenced_by(table_name, column, referenced_value)
                                            referenced_table_db.put(key_ref, referenced_record)
                                    key_value_pair = inner_cursor.next()
                                referenced_table_db.discard_cursor(inner_cursor)
                                referenced_table_db.close_db()
                    self._remove_from_indexes(table_name, record.data, key)
                    self._log_undo(('delete', table_name, key, record))
                    table_db.delete(key)
                    success_cnt += 1
        
        table_db.close_db()
        
        index_note = ""
        if use_index:
            index_note = f"[Index scan: {len(candidate_keys)} row(s) via {table_name}.{indexed_col}]"
        result = DeleteResult(success_cnt)
        if index_note:
            result = f"{index_note}\n{result}"
        return result, DeleteReferentialIntegrityPassed(fail_cnt) if fail_cnt else None
        
    # ========================================================================
    # UPDATE
    # ========================================================================
    def update(self, table_name: str, assignments: dict, where_clause: dict):
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        self.meta_db.close_db()
        
        # Validate all columns exist
        for column_name in assignments:
            if column_name not in table:
                raise UpdateColumnExistenceError(column_name)
        
        # Validate type compatibility and NOT NULL for all assignments
        for column_name, new_value in assignments.items():
            if not is_valid_type(table.columns[column_name], new_value):
                raise UpdateTypeMismatchError()
            if new_value is None and column_name in table.not_null_keys:
                raise UpdateColumnNonNullableError(column_name)
        
        # Truncate char values
        for column_name, new_value in assignments.items():
            data_type = table.columns[column_name]
            if data_type.startswith("char") and new_value is not None:
                max_len = eval_char_max_len(data_type)
                assignments[column_name] = new_value[:max_len]
        
        table_db = DB(table_name)
        table_db.open_db()
        
        # Check if we can use an index
        indexed_col, lookup_value = self._get_indexed_column_for_lookup(where_clause, table_name, table)
        use_index = indexed_col is not None
        
        if use_index:
            idx = self.indexes[(table_name, indexed_col)]
            candidate_keys = list(idx.lookup(lookup_value))
            records_to_process = []
            for key in candidate_keys:
                value = table_db.DB.get(key)
                if value:
                    records_to_process.append((key, Record.deserialize(value)))
        else:
            cursor = table_db.create_cursor()
            records_to_process = []
            key_value_pair = cursor.first()
            while key_value_pair:
                key, value = key_value_pair
                records_to_process.append((key, Record.deserialize(value)))
                key_value_pair = cursor.next()
            table_db.discard_cursor(cursor)
        
        success_cnt = 0
        
        for key, record in records_to_process:
            satisfies = self._evaluate_condition(deepcopy(where_clause), [table], record.data) if where_clause else True
            if satisfies == True:
                # Check referential integrity if updating a column that is referenced
                if table.primary_key:
                    for pk_col in table.primary_key:
                        if pk_col in assignments and record.referenced_by:
                            raise UpdateReferentialIntegrityError()
                
                # Check FK referential integrity if updating FK
                if table.foreign_keys:
                    for fk_col, (ref_table_name, ref_col_name) in table.foreign_keys.items():
                        if fk_col in assignments:
                            new_value = assignments[fk_col]
                            self.meta_db.open_db()
                            ref_table_key = self.meta_db.create_key_from_value(ref_table_name)
                            ref_table = self.meta_db.get(ref_table_key)
                            self.meta_db.close_db()
                            
                            ref_table_db = DB(ref_table_name)
                            ref_table_db.open_db()
                            ref_key = ref_table_db.create_key_from_value((new_value,))
                            ref_record = None
                            if len(ref_table.primary_key) == 1:
                                ref_record = ref_table_db.get(ref_key)
                            else:
                                for pk_val in ref_table_db.keys():
                                    if ref_key.decode() in pk_val.decode():
                                        ref_record = ref_table_db.get(pk_val)
                                        break
                            ref_table_db.close_db()
                            if ref_record is None:
                                raise UpdateReferentialIntegrityError()
                
                # Build new data
                new_data = dict(record.data)
                for col, val in assignments.items():
                    new_data[col] = val
                
                # Check PK uniqueness if updating PK
                new_primary_value = list(record.primary_value) if record.primary_value else None
                if table.primary_key and new_primary_value is not None:
                    for i, pk_col in enumerate(table.primary_key):
                        if pk_col in assignments:
                            new_primary_value[i] = assignments[pk_col]
                    new_primary_value = tuple(new_primary_value)
                    new_key = table_db.create_key_from_value(new_primary_value)
                    if new_key != key and table_db.exists(new_key):
                        raise UpdateDuplicatePrimaryKeyError()
                
                # Update referencing info
                new_referencing = dict(record.referencing)
                if table.foreign_keys:
                    for fk_col, (ref_table_name, ref_col_name) in table.foreign_keys.items():
                        if fk_col in assignments:
                            new_value = assignments[fk_col]
                            # Remove old reference
                            old_value = record.data[fk_col]
                            ref_table_db = DB(ref_table_name)
                            ref_table_db.open_db()
                            ref_key = ref_table_db.create_key_from_value((old_value,))
                            ref_record = ref_table_db.get(ref_key)
                            if ref_record:
                                ref_record.remove_referenced_by(table_name, fk_col, old_value)
                                ref_table_db.put(ref_key, ref_record)
                            # Add new reference
                            ref_key = ref_table_db.create_key_from_value((new_value,))
                            ref_record = ref_table_db.get(ref_key)
                            if ref_record:
                                ref_record.add_to_referenced_by(table_name, fk_col, new_value)
                                ref_table_db.put(ref_key, ref_record)
                                new_referencing[(ref_table_name, ref_col_name)] = {new_value}
                            ref_table_db.close_db()
                
                old_record = Record(table_name, dict(record.data), record.primary_value, dict(record.referencing))
                
                # Apply update
                record.data = new_data
                if table.primary_key and new_primary_value is not None:
                    record.primary_value = new_primary_value
                    if new_key != key:
                        table_db.delete(key)
                        table_db.put(new_key, record)
                        self._update_indexes(table_name, old_record.data, new_data, new_key)
                        self._log_undo(('update_pk', table_name, key, new_key, old_record))
                    else:
                        table_db.put(key, record)
                        self._update_indexes(table_name, old_record.data, new_data, key)
                        self._log_undo(('update', table_name, key, old_record))
                else:
                    table_db.put(key, record)
                    self._update_indexes(table_name, old_record.data, new_data, key)
                    self._log_undo(('update', table_name, key, old_record))
                
                success_cnt += 1
        
        table_db.close_db()
        index_note = ""
        if use_index:
            index_note = f"[Index scan: {len(candidate_keys)} row(s) via {table_name}.{indexed_col}]"
        result = UpdateResult(success_cnt)
        if index_note:
            result = f"{index_note}\n{result}"
        return result

    # ========================================================================
    # SELECT
    # ========================================================================
    def select(self, tables: list, select_columns: list, where_clause: dict):
        table_list = []
        self.meta_db.open_db()
        for table_name in tables:
            table_key = self.meta_db.create_key_from_value(table_name)
            table = self.meta_db.get(table_key)
            if not table:
                raise SelectTableExistenceError(table_name)
            table_list.append(table)
        self.meta_db.close_db()
        
        final_columns = []
        if select_columns:
            for table_name, column_name in select_columns:
                found_tables = [table for table in table_list if column_name in table]
                if len(found_tables) < 1:
                    raise SelectColumnResolveError(column_name)
                elif len(found_tables) > 1:
                    if not table_name:
                        raise SelectColumnResolveError(column_name)
                    found_table = next(table for table in found_tables if table_name == table.table_name)
                else:
                    found_table = found_tables[0]       
                if table_name and table_name != found_table.table_name:
                    raise SelectColumnResolveError(column_name)
                final_column = f"{found_table.table_name}.{column_name}" if table_name else column_name
                final_columns.append(final_column)
        
        all_columns = []
        for table_schema in table_list:
            all_columns.extend(list(table_schema.columns.keys()))
        counter = Counter(all_columns)
        common_columns = set([column for column, count in counter.items() if count > 1])
        
        # Check if we can use an index for single-table queries
        use_index = False
        indexed_col = None
        lookup_value = None
        index_table_name = None
        if len(tables) == 1:
            indexed_col, lookup_value = self._get_indexed_column_for_lookup(where_clause, tables[0], table_list[0])
            if indexed_col is not None:
                use_index = True
                index_table_name = tables[0]
                    
        all_records_with_table = {}
        for table_name in tables:
            all_records_with_table[table_name] = []
            
            if use_index and table_name == index_table_name:
                idx = self.indexes[(table_name, indexed_col)]
                candidate_keys = list(idx.lookup(lookup_value))
                table_db = DB(table_name)
                table_db.open_db()
                for key in candidate_keys:
                    value = table_db.DB.get(key)
                    if value:
                        record = Record.deserialize(value)
                        record_data = {}
                        for col_name, val in record.data.items():
                            if col_name in common_columns:
                                prefixed_column_name = f"{table_name}.{col_name}"
                                record_data[prefixed_column_name] = val
                            else:
                                record_data[col_name] = val
                        all_records_with_table[table_name].append(record_data)
                table_db.close_db()
            else:
                table_db = DB(table_name)
                table_db.open_db()
                cursor = table_db.create_cursor()
                key_value_pair = cursor.first()
                while key_value_pair:
                    key, value = key_value_pair
                    record = Record.deserialize(value)
                    record_data = {}
                    for col_name, val in record.data.items():
                        if col_name in common_columns:
                            prefixed_column_name = f"{table_name}.{col_name}"
                            record_data[prefixed_column_name] = val
                        else:
                            record_data[col_name] = val
                    all_records_with_table[table_name].append(record_data)
                    key_value_pair = cursor.next()
                table_db.discard_cursor(cursor)
                table_db.close_db()
        
        cartesian_product = itertools.product(*all_records_with_table.values())
        records_product = [{k: v for record in combination_tuple for k, v in record.items()} for combination_tuple in cartesian_product]
        
        if where_clause:
            filtered_records = []
            for record in records_product:
                satisfies = self._evaluate_condition(deepcopy(where_clause), table_list, record)
                if satisfies == True:
                    filtered_records.append(record)
        else:
            filtered_records = records_product
            
        if select_columns:
            final_records = []
            for record in filtered_records:
                final_record = {}
                for table_name, column_name in select_columns:
                    value = None
                    if table_name:
                        prefixed_column_name = f"{table_name}.{column_name}"
                        try:
                            final_record[prefixed_column_name] = record[prefixed_column_name]
                        except KeyError:
                            final_record[prefixed_column_name] = record[column_name]
                    else:
                        final_record[column_name] = record[column_name]
                final_records.append(final_record)
        else:
            final_records = filtered_records
            final_columns = []
            for table_schema in table_list:
                for column in table_schema.columns:
                    if column in common_columns:
                        final_columns.append(f"{table_schema.table_name}.{column}")
                    else:
                        final_columns.append(column)
            
        headers = final_records[0].keys() if final_records else final_columns
        
        result = self._format_select_output(final_records, headers)
        if use_index:
            result = f"[Index scan: {len(candidate_keys)} row(s) via {index_table_name}.{indexed_col}]\n{result}"
        return result
        
    
    def _format_select_output(self, records: List[Dict], headers: List[str]):
        def create_separator(column_widths):
            return '+-' + '-+-'.join('-' * width for width in column_widths) + '-+'
        
        for record in records:
            for k, v in record.items():
                if v is None:
                    record[k] = "null"
        
        column_widths = [len(header) for header in headers]
        for record in records:
            for i, value in enumerate(record.values()):
                column_widths[i] = max(column_widths[i], len(str(value)))
        
        output = '\n'
        output += create_separator(column_widths) + '\n'
        output += '| ' + ' | '.join(header.upper().ljust(width) for header, width in zip(headers, column_widths)) + ' |\n'
        output += create_separator(column_widths) + '\n'
        
        for record in records:
            output += '| ' + ' | '.join(str(value).ljust(width) for value, width in zip(record.values(), column_widths)) + ' |\n'
        output += create_separator(column_widths)
        
        return output
    
    # ========================================================================
    # INDEXING
    # ========================================================================
    def create_index(self, index_name: str, table_name: str, column_name: str):
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table:
            raise NoSuchTable()
        if column_name not in table.columns:
            raise NonExistingColumnDefError(column_name)
        if column_name in table.indexes:
            raise IndexExistenceError(f"{table_name}.{column_name}")
        
        table.indexes.add(column_name)
        self.meta_db.put(table_key, table)
        self.meta_db.close_db()
        
        idx = HashIndex(table_name, column_name)
        idx.rebuild(table_name)
        self.indexes[(table_name, column_name)] = idx
        
        return CreateIndexSuccess(index_name)
    
    def drop_index(self, table_name: str, column_name: str):
        index_name = f"{table_name}.{column_name}"
        
        self.meta_db.open_db()
        table_key = self.meta_db.create_key_from_value(table_name)
        table = self.meta_db.get(table_key)
        if not table or column_name not in table.indexes:
            raise NoSuchIndex(index_name)
        
        table.indexes.discard(column_name)
        self.meta_db.put(table_key, table)
        self.meta_db.close_db()
        
        if (table_name, column_name) in self.indexes:
            del self.indexes[(table_name, column_name)]
        
        return DropIndexSuccess(index_name)
    
    # ========================================================================
    # TRANSACTIONS
    # ========================================================================
    def begin_transaction(self):
        if self.in_transaction:
            raise TransactionAlreadyActive()
        self.in_transaction = True
        self.undo_log = []
        return BeginTransactionResult()
    
    def commit_transaction(self):
        if not self.in_transaction:
            raise NoActiveTransaction()
        self.in_transaction = False
        self.undo_log = []
        return CommitTransactionResult()
    
    def rollback_transaction(self):
        if not self.in_transaction:
            raise NoActiveTransaction()
        
        # Replay undo log in reverse
        for entry in reversed(self.undo_log):
            op = entry[0]
            if op == 'insert':
                _, table_name, record_key = entry
                table_db = DB(table_name)
                table_db.open_db()
                if table_db.exists(record_key):
                    record = table_db.get(record_key)
                    if record:
                        self._remove_from_indexes(table_name, record.data, record_key)
                    table_db.delete(record_key)
                table_db.close_db()
            elif op == 'delete':
                _, table_name, record_key, old_record = entry
                table_db = DB(table_name)
                table_db.open_db()
                table_db.put(record_key, old_record)
                self._add_to_indexes(table_name, old_record.data, record_key)
                table_db.close_db()
            elif op == 'update':
                _, table_name, record_key, old_record = entry
                table_db = DB(table_name)
                table_db.open_db()
                table_db.put(record_key, old_record)
                self._update_indexes(table_name, {}, old_record.data, record_key)
                table_db.close_db()
            elif op == 'update_pk':
                _, table_name, old_key, new_key, old_record = entry
                table_db = DB(table_name)
                table_db.open_db()
                if table_db.exists(new_key):
                    new_record = table_db.get(new_key)
                    if new_record:
                        self._remove_from_indexes(table_name, new_record.data, new_key)
                    table_db.delete(new_key)
                table_db.put(old_key, old_record)
                self._add_to_indexes(table_name, old_record.data, old_key)
                table_db.close_db()
        
        self.in_transaction = False
        self.undo_log = []
        return RollbackTransactionResult()
    
    # ========================================================================
    # WHERE EVALUATION
    # ========================================================================
    def _evaluate_condition(self, condition, table_list: List[Table], record: dict):
        def get_record_value(operand):
            table_name, column_name = operand
            if table_name and not any([table_name == table.table_name for table in table_list]):
                raise WhereTableNotSpecified()
            found_tables = [table for table in table_list if column_name in table]
            if len(found_tables) < 1:
                raise WhereColumnNotExist()
            elif len(found_tables) > 1:
                if not table_name:
                    raise WhereAmbiguousReference()
                table = next(table for table in found_tables if table_name == table.table_name)
            else:
                table = found_tables[0]
            if table_name and table_name != table.table_name:
                raise WhereColumnNotExist()
            if table_name:
                prefixed_column_name = f"{table_name}.{column_name}"
                if prefixed_column_name in record:
                    return record[prefixed_column_name]
            return record[column_name]
        
        def determine_operand_value(operand):
            if operand is None:
                value = operand
            elif len(operand) == 1:
                value = operand[0]
            else:
                value = get_record_value(operand)
            return value
            
        op = condition["op"]
        if op in comparison_op_map | null_op_map:
            op, left_operand, right_operand = map(condition.get, ["op", "left_operand", "right_operand"])
            left_value = determine_operand_value(left_operand)
            right_value = determine_operand_value(right_operand)
            
            if op in comparison_op_map and is_comparable(left_value, right_value) == False:
                raise WhereIncomparableError()
            
            if op in comparison_op_map:
                if left_value is None or right_value is None:
                    output = UNKNOWN
                else:
                    output = comparison_op_map[op](left_value, right_value)
            else:
                output = null_op_map[op](left_value, right_value)
            return output
            
        elif op == "not":
            boolean_test = condition["boolean_test"]
            return not_(self._evaluate_condition(boolean_test, table_list, record))
        
        elif op == "and":
            boolean_factors = condition["boolean_factors"]
            return and_(*[self._evaluate_condition(boolean_factor, table_list, record) for boolean_factor in boolean_factors])
        
        elif op == "or":
            boolean_terms = condition["boolean_terms"]
            return or_(*[self._evaluate_condition(boolean_term, table_list, record) for boolean_term in boolean_terms])
        
        else:
            _, remaining_condition = condition.popitem()
            if remaining_condition is not None:
                return self._evaluate_condition(remaining_condition, table_list, record)
