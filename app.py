from flask import Flask, request, jsonify, render_template
from lark import Lark

from dbms import DBMS
from sql_transformer import SQLTransformer
from messages import *

app = Flask(__name__)
dbms = DBMS()

with open('grammar.lark') as f:
    sql_parser = Lark(f.read(), start="command", lexer="basic")


def parse_select_output(output: str):
    """Parse ASCII table output into headers and rows."""
    lines = output.strip().split('\n')
    headers = None
    rows = []
    for line in lines:
        if line.startswith('|') and not line.startswith('+-'):
            cells = [cell.strip() for cell in line[1:-1].split('|')]
            if headers is None:
                headers = cells
            else:
                rows.append(cells)
    return headers, rows


def execute_sql(sql: str):
    """Execute a sequence of SQL statements and return structured results."""
    results = []
    sql = sql.strip()
    if not sql:
        return results
    if not sql.endswith(';'):
        sql += ';'
    
    # Split into individual queries
    query_list = []
    current = ""
    for part in sql.split(';'):
        part = part.strip()
        if part:
            current += part + ";"
            query_list.append(current)
            current = ""
    
    for query in query_list:
        query = query.strip()
        if not query:
            continue
        try:
            transformer = SQLTransformer()
            parsed = sql_parser.parse(query)
            statement, table, record, tables, select_columns, where, index, assignments = transformer.transform(parsed)
            
            if statement == 'exit':
                break
            elif statement == "create table":
                result = dbms.create_table(table)
                results.append({"success": True, "type": "ddl", "message": str(result)})
            elif statement == "drop table":
                result = dbms.drop_table(table["table_name"])
                results.append({"success": True, "type": "ddl", "message": str(result)})
            elif statement in ("explain", "describe", "desc"):
                result = dbms.explain_describe_desc(table["table_name"])
                results.append({"success": True, "type": "schema", "message": str(result)})
            elif statement == "show tables":
                result = dbms.show_tables()
                results.append({"success": True, "type": "tables", "message": result})
            elif statement == "insert":
                result = dbms.insert(table, record)
                results.append({"success": True, "type": "dml", "message": str(result)})
            elif statement == "delete":
                result, extra = dbms.delete(table["table_name"], where)
                messages = [str(result)]
                if extra:
                    messages.append(str(extra))
                results.append({"success": True, "type": "dml", "message": "\n".join(messages)})
            elif statement == "select":
                output = dbms.select(tables, select_columns, where)
                headers, rows = parse_select_output(output)
                results.append({
                    "success": True, 
                    "type": "select", 
                    "headers": headers, 
                    "rows": rows,
                    "raw": output
                })
            elif statement == "update":
                result = dbms.update(table["table_name"], assignments, where)
                results.append({"success": True, "type": "dml", "message": str(result)})
            elif statement == "create index":
                result = dbms.create_index(index["index_name"], index["table_name"], index["column_name"])
                results.append({"success": True, "type": "ddl", "message": str(result)})
            elif statement == "drop index":
                result = dbms.drop_index(index["table_name"], index["column_name"])
                results.append({"success": True, "type": "ddl", "message": str(result)})
            elif statement == "begin":
                result = dbms.begin_transaction()
                results.append({"success": True, "type": "transaction", "message": str(result)})
            elif statement == "commit":
                result = dbms.commit_transaction()
                results.append({"success": True, "type": "transaction", "message": str(result)})
            elif statement == "rollback":
                result = dbms.rollback_transaction()
                results.append({"success": True, "type": "transaction", "message": str(result)})
            else:
                results.append({"success": False, "type": "error", "error": f"Unknown statement: {statement}"})
        except Exception as e:
            results.append({"success": False, "type": "error", "error": str(e)})
            break
        finally:
            # Always close DB connections after each query to prevent
            # cross-thread SQLite errors on the next request
            try:
                dbms.meta_db.close_db()
            except Exception:
                pass
    
    return results


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/query', methods=['POST'])
def api_query():
    data = request.get_json()
    sql = data.get('sql', '')
    results = execute_sql(sql)
    return jsonify({"results": results})


@app.route('/api/schema', methods=['GET'])
def api_schema():
    """Return all table schemas and indexes."""
    tables = {}
    in_transaction = False
    try:
        fresh_dbms = DBMS()
        fresh_dbms.meta_db.open_db()
        for table_key in fresh_dbms.meta_db.keys():
            table = fresh_dbms.meta_db.get(table_key)
            if table:
                tables[table.table_name] = {
                    "columns": {k: v for k, v in table.columns.items()},
                    "primary_key": list(table.primary_key) if table.primary_key else [],
                    "not_null": list(table.not_null_keys),
                    "foreign_keys": {k: list(v) for k, v in table.foreign_keys.items()},
                    "indexes": list(table.indexes)
                }
        fresh_dbms.meta_db.close_db()
        in_transaction = dbms.in_transaction
    except Exception:
        pass
    return jsonify({"tables": tables, "in_transaction": in_transaction})


if __name__ == '__main__':
    # threaded=False prevents SQLite "objects created in a thread" errors
    # since the global dbms object is shared across requests
    app.run(host='127.0.0.1', port=8080, debug=True, threaded=False)
