# Database Management System: A Simple SQL Implementation Using Python

This repository contains a simple SQL Database Management System (DBMS) implemented in Python. The primary objective of the project is to simulate basic functionalities of a SQL database like parsing SQL queries, managing schema metadata, and performing CRUD (Create, Read, Update, Delete) operations.

## What's Included

- **SQL Query Parsing** — `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE`, `DROP TABLE`, `CREATE INDEX`, `DROP INDEX`, `EXPLAIN`, `SHOW TABLES`
- **Transactions** — `BEGIN`, `COMMIT`, `ROLLBACK` with undo log
- **Indexing** — Hash-based indexes with automatic index scan for point lookups
- **Schema Management** — Table/column metadata with PK, FK, NOT NULL, CHECK constraints
- **Web GUI** — Flask-based interface to run queries visually
- **Test Suite** — Automated tests for all features

---

## Prerequisites

- **Python 3.9** (required — newer versions may work but 3.9 is tested)
- **pip** (Python package manager)
- **Git** (to clone the repository)

> **Windows users:** Make sure Python is installed from [python.org](https://www.python.org/downloads/release/python-3913/) and added to your PATH. The Microsoft Store stub will not work.

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/mhsniranmanesh/SQL-DBMS.git
cd SQL-DBMS
```

### 2. Create a virtual environment

This isolates the project dependencies from your system Python.

**Windows (Command Prompt):**
```cmd
python -m venv venv
venv\Scripts\activate.bat
```

**Windows (PowerShell):**
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

**Windows (Git Bash):**
```bash
python -m venv venv
source venv/Scripts/activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

> You should see `(venv)` at the start of your prompt when activated.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

This installs `lark==1.1.5` and `flask==3.0.3`.

---

## Run the CLI (Interactive SQL REPL)

The command-line interface lets you type SQL queries interactively.

```bash
python run.py
```

You will see a prompt:

```
DB_2023-12345>
```

Type SQL commands (end each with a semicolon `;`):

```sql
create table students (id int not null, name char(20), primary key(id));
insert into students values(1, 'alice');
insert into students values(2, 'bob');
select * from students;
exit;
```

Press **Enter** after each statement. The REPL reads one query at a time.

---

## Run the Web GUI

The web interface provides a browser-based SQL editor with a schema browser, results table, and transaction status indicator.

```bash
python app.py
```

Then open your browser to: **http://127.0.0.1:8080**

You will see:
- **Left sidebar** — Schema browser showing all tables, columns, and indexes
- **Center** — SQL editor with Run, Clear, and Load Example buttons
- **Bottom** — Results displayed as formatted tables or messages

### GUI Features
- Run multiple statements at once (separate with `;`)
- `Ctrl + Enter` to run the current query
- Transaction status badge in the header
- Error messages shown in red without crashing the UI

---

## Run the Test Suite

Verify that all features work correctly:

```bash
python run_tests.py --verbose
```

Expected output:
```
[OK] PASS: test_update (1032ms)
[OK] PASS: test_index (1210ms)
[OK] PASS: test_transaction (1711ms)
[OK] PASS: test_integration (1517ms)

==================================================
Results: 4/4 passed
==================================================
```

To regenerate expected outputs (after changing behavior):

```bash
python run_tests.py --generate
```

---

## SQL Syntax Reference

### DDL (Data Definition)

```sql
create table account (
    account_number int not null,
    branch_name char(15),
    balance int,
    primary key(account_number)
);

create table depositor (
    customer_name char(15) not null,
    account_number int not null,
    primary key(customer_name, account_number),
    foreign key(account_number) references account(account_number)
);

drop table account;

show tables;

explain account;
```

### DML (Data Manipulation)

```sql
insert into account values(1, 'Perryridge', 500);

update account set branch_name = 'Downtown' where account_number = 1;

update account set branch_name = 'Uptown', balance = 1000 where account_number = 1;

delete from account where balance < 100;

select * from account;

select account_number, branch_name from account where balance > 200;

select customer_name, borrower.loan_number, amount
from borrower, loan
where borrower.loan_number = loan.loan_number
and branch_name = 'Perryridge';
```

### Indexing

```sql
create index idx_name on account(account_number);

drop index account.account_number;
```

### Transactions

```sql
begin;
insert into account values(99, 'Temp', 0);
select * from account;
rollback;   -- undoes the insert

begin;
update account set balance = 999 where account_number = 1;
commit;     -- persists the update
```

### Exit

```sql
exit;
```

---

## Important Notes

- **Identifiers** must start with a letter and contain only letters and underscores. Names like `t2` are rejected by the grammar. Use `account`, `students`, `branch_name`, etc.
- **Each statement** must end with a semicolon `;`.
- **NULL values** are typed as `null` (without quotes).
- **Strings** use single quotes: `'hello'`.
- **Storage** is backed by Python's built-in `dbm` module. All data is stored in a `DB/` directory created in the project root.
- To **wipe the database**, simply delete the `DB/` folder.

---

## Project Structure

| File | Purpose |
|---|---|
| `run.py` | CLI entry point — interactive SQL REPL |
| `app.py` | Web GUI entry point — Flask server |
| `grammar.lark` | SQL grammar definition (Lark EBNF) |
| `sql_transformer.py` | AST transformer — parses SQL into Python structures |
| `dbms.py` | Core engine — executes SQL statements |
| `db_model.py` | Data structures (`Table`, `Record`, `DB`) |
| `messages.py` | Success/error message classes |
| `utils.py` | Type validation, operator mappings |
| `run_tests.py` | Test runner for all features |
| `test/` | Test SQL files and expected outputs |
| `.github/workflows/test.yml` | CI workflow for GitHub Actions |
| `templates/index.html` | Web GUI frontend |

---

## Troubleshooting

### `python` not found

If `python` is not recognized, try `python3` or `py` instead. On Windows, make sure you installed Python from [python.org](https://www.python.org/downloads/) and checked **"Add Python to PATH"** during installation.

### PowerShell execution policy error

If PowerShell blocks the activation script:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then retry `venv\Scripts\Activate.ps1`.

### Port 8080 is already in use

If you see `Address already in use`, another process is using port 8080. Either:
- Stop the other process, or
- Change the port in `app.py`: `app.run(host='127.0.0.1', port=8081)`

### "SQLite objects created in a thread" error

This happens when the GUI handles requests in multiple threads. The project already sets `threaded=False` in `app.py` to prevent this. If you still see it, restart `app.py`.

### Corrupted database

If queries start returning strange errors, the `dbm` files may be corrupted:

```bash
# Windows
rmdir /s /q DB

# macOS / Linux
rm -rf DB
```

Then restart the app.

---

## Docker (Alternative)

The repo ships a `Dockerfile`, `docker-compose.yml`, and `Makefile` for a consistent cross-platform setup:

```bash
make build      # build the image (once)
make run        # start the interactive SQL REPL
make shell      # open a shell inside the container
make test       # pipe a few statements through the engine as a smoke test
make reset      # wipe the database volume
```

VS Code / Cursor users can instead **"Reopen in Container"** (see `.devcontainer/`).

---

## Implementation Details

- `grammar.lark` — Defines SQL grammar in EBNF. Using the Lark API, this file serves as the basis for parsing SQL queries into AST (Abstract Syntax Trees).
- `sql_transformer.py` — Inherits from Lark's `Transformer` class to handle the AST generated by the parser. It processes and returns tables, records, and columns selected in the query.
- `db_model.py` — Defines the data structures for schemas and records (each represented by `Table` and `Record` classes). It also contains a `DB` class which acts as a wrapper for manipulating `dbm` key/value stores. Metadata of schemas is stored in `MetaDB`, which inherits from the `DB` class.
- `dbms.py` — Handles SQL statements such as `CREATE TABLE`, `DROP TABLE`, `EXPLAIN/DESCRIBE/DESC`, `SHOW TABLES`, `INSERT`, `DELETE`, `SELECT`, `UPDATE`, `CREATE INDEX`, `DROP INDEX`, `BEGIN/COMMIT/ROLLBACK` through a `DBMS` class.
- `messages.py` — Defines exception classes for logging and error messages that indicate whether the SQL command was executed successfully by the `DBMS` class.
- `utils.py` — Defines function mappings for unknown variables and logical operations in SQL, as well as for parsed comparison/null operators. It also includes functions for validating data types, including `date` data types.
- `run.py` — Splits the query sequence into multiple statements and performs actions for each query. It imports the `Lark` class from the Lark library and generates a parser based on the grammar defined in the `grammar.lark` file. It also imports `SQLTransformer` to interpret the AST generated by the parser and extract the necessary data.

---

This project was done as part of Spring 2023 Database M1522.001800 course of Seoul National University.
