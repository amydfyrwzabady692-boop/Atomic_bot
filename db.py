import pyodbc
import os
from dotenv import load_dotenv

load_dotenv()

_SERVER = os.getenv('DB_SERVER', 'localhost')
_DB = os.getenv('DB_NAME', 'AccShop')


def get_conn():
    return pyodbc.connect(
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={_SERVER};"
        f"DATABASE={_DB};"
        f"Trusted_Connection=yes;"
        f"Encrypt=no;"
    )


def get_categories():
    with get_conn() as conn:
        rows = conn.cursor().execute("SELECT Id, Name FROM Categories").fetchall()
    return rows


def get_products(category_id=None, search=None, limit=12):
    with get_conn() as conn:
        cur = conn.cursor()
        sql = "SELECT Id, Name, Price, OldPrice, Badge, Image FROM Products WHERE IsActive=1"
        params = []
        if category_id:
            sql += " AND CategoryId=?"
            params.append(category_id)
        if search:
            sql += " AND Name LIKE ?"
            params.append(f"%{search}%")
        sql += f" ORDER BY CreatedAt DESC OFFSET 0 ROWS FETCH NEXT {limit} ROWS ONLY"
        rows = cur.execute(sql, params).fetchall()
    return rows


def get_product(pk):
    with get_conn() as conn:
        row = conn.cursor().execute(
            "SELECT Id, Name, Price, OldPrice, Badge, Image, Details FROM Products WHERE Id=? AND IsActive=1",
            pk
        ).fetchone()
    return row


def get_gems(plan_type=None):
    with get_conn() as conn:
        cur = conn.cursor()
        sql = "SELECT Id, Title, Amount, BonusAmount, Price, OldPrice, Badge, PurchaseType FROM GemPackages WHERE IsActive=1"
        params = []
        if plan_type:
            sql += " AND PlanType=?"
            params.append(plan_type)
        sql += " ORDER BY Price"
        rows = cur.execute(sql, params).fetchall()
    return rows


def get_gem(pk):
    with get_conn() as conn:
        row = conn.cursor().execute(
            "SELECT Id, Title, Amount, BonusAmount, Price, OldPrice, PurchaseType FROM GemPackages WHERE Id=? AND IsActive=1",
            pk
        ).fetchone()
    return row


def get_sensitivity_packs(platform='mobile', device=None):
    with get_conn() as conn:
        cur = conn.cursor()
        sql = "SELECT Id, Title, Price, OldPrice, Badge, DeviceType FROM SensitivityPacks WHERE IsActive=1 AND Platform=?"
        params = [platform]
        if device and device != 'all':
            sql += " AND DeviceType=?"
            params.append(device)
        rows = cur.execute(sql, params).fetchall()
    return rows


def get_or_create_user(telegram_id, first_name='', last_name='', username=''):
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT Id, Email, Phone FROM Users WHERE Username=?",
            f"tg_{telegram_id}"
        ).fetchone()
        if row:
            return row[0], False
        email = f"tg_{telegram_id}@telegram.bot"
        cur.execute(
            "INSERT INTO Users (Username, Email, Password, FirstName, LastName, IsActive, IsStaff, IsSuperUser) "
            "VALUES (?, ?, '', ?, ?, 1, 0, 0)",
            f"tg_{telegram_id}", email, first_name or '', last_name or ''
        )
        conn.commit()
        new_id = cur.execute(
            "SELECT Id FROM Users WHERE Username=?", f"tg_{telegram_id}"
        ).fetchone()[0]
        return new_id, True


def create_order(user_db_id, total, phone='', full_name=''):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO Orders (UserId, FullName, Email, Phone, TotalAmount, DiscountAmount, PaymentMethod, Status) "
            "VALUES (?, ?, ?, ?, ?, 0, 'online', 'pending')",
            user_db_id, full_name or 'کاربر تلگرام',
            f"tg_{user_db_id}@telegram.bot", phone or '', total
        )
        conn.commit()
        order_id = cur.execute(
            "SELECT TOP 1 Id FROM Orders WHERE UserId=? ORDER BY Id DESC", user_db_id
        ).fetchone()[0]
    return order_id


def add_order_item(order_id, product_name, price, qty=1):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO OrderItems (OrderId, ProductName, Price, Quantity) VALUES (?, ?, ?, ?)",
            order_id, product_name, price, qty
        )
        conn.commit()


def update_order_status(order_id, status):
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE Orders SET Status=? WHERE Id=?", status, order_id
        )
        conn.commit()


def create_ticket(user_db_id, subject, message, category='other'):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO SupportTickets (UserId, Subject, Category, Priority, Message, Status) "
            "VALUES (?, ?, ?, 'normal', ?, 'open')",
            user_db_id, subject, category, message
        )
        conn.commit()
        ticket_id = cur.execute(
            "SELECT TOP 1 Id FROM SupportTickets WHERE UserId=? ORDER BY Id DESC", user_db_id
        ).fetchone()[0]
        cur.execute(
            "INSERT INTO TicketMessages (TicketId, Sender, Text) VALUES (?, 'user', ?)",
            ticket_id, message
        )
        conn.commit()
    return ticket_id


def get_user_orders(user_db_id, limit=10):
    with get_conn() as conn:
        rows = conn.cursor().execute(
            "SELECT TOP (?) Id, TotalAmount, Status, CreatedAt FROM Orders "
            "WHERE UserId=? ORDER BY CreatedAt DESC",
            limit, user_db_id
        ).fetchall()
    return rows
