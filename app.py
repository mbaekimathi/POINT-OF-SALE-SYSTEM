from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
import pymysql
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import hashlib
import secrets
import random
from werkzeug.utils import secure_filename

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Configure upload folder for profile photos
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Create upload directory if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Database configuration
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'hotel_pos'),
    'charset': 'utf8mb4',
    'use_unicode': True
}

def get_db_connection():
    """Create and return a database connection"""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        return connection
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def safe_encode_string(text):
    """Safely encode a string to avoid Unicode encoding issues"""
    if text is None:
        return None
    if isinstance(text, str):
        # Remove any problematic Unicode characters and ensure ASCII compatibility
        return text.encode('ascii', 'ignore').decode('ascii')
    return str(text)

def log_cashier_activity(cashier_id, action_type, table_name=None, record_id=None, old_values=None, new_values=None, description=None, request=None):
    """Log cashier activity for audit purposes"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
            
        cursor = connection.cursor()
        
        # Get IP address and user agent from request
        ip_address = request.remote_addr if request else None
        user_agent = request.headers.get('User-Agent') if request else None
        
        cursor.execute("""
            INSERT INTO cashier_logs (cashier_id, action_type, table_name, record_id, old_values, new_values, description, ip_address, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            cashier_id,
            action_type,
            table_name,
            record_id,
            old_values,
            new_values,
            description,
            ip_address,
            user_agent
        ))
        
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except Exception as e:
        print(f"Error logging cashier activity: {e}")
        return False

def check_and_auto_close_sessions():
    """Check for sessions that should be auto-closed at midnight"""
    try:
        connection = get_db_connection()
        if not connection:
            return False
            
        cursor = connection.cursor()
        
        # Find active sessions from previous days
        cursor.execute("""
            SELECT id, cashier_id, starting_amount, session_date
            FROM cash_drawer_sessions 
            WHERE status = 'active' 
            AND session_date < CURDATE()
        """)
        
        old_sessions = cursor.fetchall()
        
        for session in old_sessions:
            session_id, cashier_id, starting_amount, session_date = session
            
            # Calculate totals for the session
            cursor.execute("""
                SELECT 
                    COALESCE(SUM(CASE WHEN transaction_type = 'cash_in' AND description != 'Starting cash amount' THEN amount ELSE 0 END), 0) as total_cash_in,
                    COALESCE(SUM(CASE WHEN transaction_type = 'cash_out' AND description NOT LIKE 'Safe drop%' AND description NOT LIKE 'End shift%' THEN amount ELSE 0 END), 0) as total_cash_out,
                    COALESCE(SUM(CASE WHEN description LIKE 'Safe drop%' THEN amount ELSE 0 END), 0) as safe_drops,
                    COALESCE(SUM(CASE WHEN description LIKE 'End shift%' THEN amount ELSE 0 END), 0) as end_shift_amount
                FROM cash_drawer_transactions 
                WHERE employee_id = %s 
                AND DATE(created_at) = %s
            """, (cashier_id, session_date))
            
            totals = cursor.fetchone()
            if totals:
                total_cash_in, total_cash_out, safe_drops, end_shift_amount = totals
                
                # Get sales for the day
                cursor.execute("""
                    SELECT COALESCE(SUM(total_amount), 0) 
                    FROM sales 
                    WHERE employee_id = %s AND DATE(sale_date) = %s
                """, (cashier_id, session_date))
                
                total_sales = cursor.fetchone()[0] or 0
                
                # Calculate expected balance
                expected_balance = starting_amount + total_cash_in + total_sales - total_cash_out - safe_drops
                
                # Use end shift amount if available, otherwise use expected balance
                ending_amount = end_shift_amount if end_shift_amount > 0 else expected_balance
                variance = ending_amount - expected_balance
                
                # Update session
                cursor.execute("""
                    UPDATE cash_drawer_sessions 
                    SET status = 'auto_closed',
                        end_time = CONCAT(session_date, ' 23:59:59'),
                        ending_amount = %s,
                        total_cash_in = %s,
                        total_cash_out = %s,
                        total_sales = %s,
                        variance = %s
                    WHERE id = %s
                """, (ending_amount, total_cash_in, total_cash_out, total_sales, variance, session_id))
                
                print(f"Auto-closed session {session_id} for cashier {cashier_id} on {session_date}")
        
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except Exception as e:
        print(f"Error auto-closing sessions: {e}")
        return False

def create_database():
    """Create database if it doesn't exist"""
    # Connect without specifying database
    db_config_no_db = DB_CONFIG.copy()
    db_config_no_db.pop('database', None)
    
    try:
        connection = pymysql.connect(**db_config_no_db)
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']}")
            connection.commit()
            print(f"Database '{DB_CONFIG['database']}' created or already exists")
        connection.close()
        return True
    except Exception as e:
        print(f"Database creation error: {e}")
        return False

def init_database():
    """Initialize database tables"""
    # First, try to create the database
    if not create_database():
        print("Failed to create database")
        return
    
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor() as cursor:
                # Create employees table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS employees (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        full_name VARCHAR(255) NOT NULL,
                        email VARCHAR(255) UNIQUE NOT NULL,
                        phone_number VARCHAR(20) NOT NULL,
                        employee_code VARCHAR(4) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        profile_photo VARCHAR(255),
                        role ENUM('admin', 'manager', 'cashier', 'butchery', 'employee') DEFAULT 'employee',
                        status ENUM('waiting_approval', 'active', 'suspended') DEFAULT 'waiting_approval',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)
                
                # Create off_days table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS off_days (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        employee_id INT NOT NULL,
                        off_date DATE NOT NULL,
                        off_type ENUM('weekend', 'holiday', 'sick_leave', 'vacation', 'personal', 'emergency', 'other') NOT NULL,
                        status ENUM('approved', 'pending', 'rejected', 'cancelled') DEFAULT 'pending',
                        reason TEXT,
                        approved_by INT,
                        approved_at TIMESTAMP NULL,
                        notes TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE,
                        FOREIGN KEY (approved_by) REFERENCES employees(id) ON DELETE SET NULL,
                        UNIQUE KEY unique_employee_off_date (employee_id, off_date)
                    )
                """)
                
                # Create items table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS items (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        description TEXT,
                        price DECIMAL(10,2) NOT NULL,
                        category VARCHAR(100) NOT NULL,
                        stock INT DEFAULT 0,
                        low_stock_threshold INT DEFAULT 10,
                        status ENUM('active', 'inactive') DEFAULT 'active',
                        image_url VARCHAR(500),
                        sku VARCHAR(100) UNIQUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)
                
                # Check if low_stock_threshold column exists and add it if it doesn't
                cursor.execute("SHOW COLUMNS FROM items LIKE 'low_stock_threshold'")
                if not cursor.fetchone():
                    try:
                        cursor.execute("ALTER TABLE items ADD COLUMN low_stock_threshold INT DEFAULT 10")
                        print("Added low_stock_threshold column to items table")
                    except Exception as e:
                        print(f"Error adding low_stock_threshold column: {e}")
                else:
                    print("low_stock_threshold column already exists")
                
                # Create or update stock_transactions table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS stock_transactions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        item_id INT NOT NULL,
                        action ENUM('stock_in', 'stock_out') NOT NULL,
                        quantity INT NOT NULL,
                        reason VARCHAR(255),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
                    )
                """)
                
                # Check if new columns exist and add them if they don't
                cursor.execute("SHOW COLUMNS FROM stock_transactions LIKE 'price_per_unit'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE stock_transactions ADD COLUMN price_per_unit DECIMAL(10,2)")
                
                cursor.execute("SHOW COLUMNS FROM stock_transactions LIKE 'total_amount'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE stock_transactions ADD COLUMN total_amount DECIMAL(10,2)")
                
                cursor.execute("SHOW COLUMNS FROM stock_transactions LIKE 'place_purchased_from'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE stock_transactions ADD COLUMN place_purchased_from VARCHAR(255)")
                
                cursor.execute("SHOW COLUMNS FROM stock_transactions LIKE 'employee_id'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE stock_transactions ADD COLUMN employee_id INT")
                
                cursor.execute("SHOW COLUMNS FROM stock_transactions LIKE 'employee_name'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE stock_transactions ADD COLUMN employee_name VARCHAR(255)")
                
                cursor.execute("SHOW COLUMNS FROM stock_transactions LIKE 'transaction_type'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE stock_transactions ADD COLUMN transaction_type ENUM('purchase', 'sale', 'return', 'waste') DEFAULT 'purchase'")
                
                cursor.execute("SHOW COLUMNS FROM stock_transactions LIKE 'selling_price'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE stock_transactions ADD COLUMN selling_price DECIMAL(10,2)")
                
                cursor.execute("SHOW COLUMNS FROM stock_transactions LIKE 'refund_issued'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE stock_transactions ADD COLUMN refund_issued BOOLEAN DEFAULT FALSE")
                
                # Update reason column to be longer if it exists
                cursor.execute("SHOW COLUMNS FROM stock_transactions LIKE 'reason'")
                if cursor.fetchone():
                    cursor.execute("ALTER TABLE stock_transactions MODIFY COLUMN reason VARCHAR(500)")
                
                # Add stock update toggle column to items table
                cursor.execute("SHOW COLUMNS FROM items LIKE 'stock_update_enabled'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE items ADD COLUMN stock_update_enabled BOOLEAN DEFAULT TRUE")
                
                # Add low stock threshold column to items table
                cursor.execute("SHOW COLUMNS FROM items LIKE 'low_stock_threshold'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE items ADD COLUMN low_stock_threshold INT DEFAULT 10")
                
                # Create stock settings table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS stock_settings (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        setting_name VARCHAR(100) NOT NULL UNIQUE,
                        setting_value TEXT,
                        description TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)
                
                # Insert default stock settings
                cursor.execute("""
                    INSERT IGNORE INTO stock_settings (setting_name, setting_value, description) VALUES
                    ('default_low_stock_threshold', '10', 'Default low stock threshold for items'),
                    ('enable_stock_alerts', 'true', 'Enable stock level alerts'),
                    ('alert_email', '', 'Email for stock alerts'),
                    ('auto_reorder_enabled', 'false', 'Enable automatic reorder recommendations')
                """)
                
                # Create sales table for tracking completed sales
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sales (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        receipt_number VARCHAR(10) NOT NULL UNIQUE,
                        employee_id INT NOT NULL,
                        employee_name VARCHAR(255) NOT NULL,
                        employee_code VARCHAR(4) NOT NULL,
                        subtotal DECIMAL(10,2) NOT NULL,
                        tax_amount DECIMAL(10,2) DEFAULT 0,
                        total_amount DECIMAL(10,2) NOT NULL,
                        tax_included BOOLEAN DEFAULT TRUE,
                        sale_date DATETIME NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (employee_id) REFERENCES employees(id)
                    )
                """)
                
                # Add status column to sales table if it doesn't exist
                try:
                    cursor.execute("ALTER TABLE sales ADD COLUMN status ENUM('pending', 'confirmed', 'cancelled') DEFAULT 'pending'")
                except Exception as e:
                    # Column might already exist, ignore error
                    pass
                
                # Add cashier_confirmed column to sales table if it doesn't exist
                try:
                    cursor.execute("ALTER TABLE sales ADD COLUMN cashier_confirmed TINYINT(1) DEFAULT 0")
                except Exception as e:
                    # Column might already exist, ignore error
                    pass
                
                # Create sales_items table for tracking individual items in each sale
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sales_items (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        sale_id INT NOT NULL,
                        item_id INT NOT NULL,
                        item_name VARCHAR(255) NOT NULL,
                        quantity INT NOT NULL,
                        unit_price DECIMAL(10,2) NOT NULL,
                        total_price DECIMAL(10,2) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE,
                        FOREIGN KEY (item_id) REFERENCES items(id)
                    )
                """)
                
                # Create hotel_settings table for storing hotel information
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS hotel_settings (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        hotel_name VARCHAR(255) NOT NULL,
                        company_email VARCHAR(255) NOT NULL,
                        company_phone VARCHAR(50) NOT NULL,
                        hotel_address TEXT,
                        business_type VARCHAR(100),
                        payment_method ENUM('buy_goods', 'paybill') NOT NULL,
                        till_number VARCHAR(20),
                        business_number VARCHAR(20),
                        account_number VARCHAR(50),
                        double_print BOOLEAN DEFAULT FALSE,
                        show_till BOOLEAN DEFAULT TRUE,
                        include_tax BOOLEAN DEFAULT TRUE,
                        show_images BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)
                
                # Add business_type column if it doesn't exist (migration)
                try:
                    cursor.execute("ALTER TABLE hotel_settings ADD COLUMN business_type VARCHAR(100) AFTER hotel_address")
                    print("Added business_type column to hotel_settings table")
                except Exception as e:
                    # Column might already exist, ignore error
                    pass
                
                # Add printing settings columns if they don't exist (migration)
                try:
                    cursor.execute("ALTER TABLE hotel_settings ADD COLUMN double_print BOOLEAN DEFAULT FALSE")
                    print("Added double_print column to hotel_settings table")
                except Exception as e:
                    # Column might already exist, ignore error
                    pass
                
                try:
                    cursor.execute("ALTER TABLE hotel_settings ADD COLUMN show_till BOOLEAN DEFAULT TRUE")
                    print("Added show_till column to hotel_settings table")
                except Exception as e:
                    # Column might already exist, ignore error
                    pass
                
                try:
                    cursor.execute("ALTER TABLE hotel_settings ADD COLUMN include_tax BOOLEAN DEFAULT TRUE")
                    print("Added include_tax column to hotel_settings table")
                except Exception as e:
                    # Column might already exist, ignore error
                    pass
                
                try:
                    cursor.execute("ALTER TABLE hotel_settings ADD COLUMN show_images BOOLEAN DEFAULT TRUE")
                    print("Added show_images column to hotel_settings table")
                except Exception as e:
                    # Column might already exist, ignore error
                    pass
                
                # Add receipt settings columns if they don't exist (migration)
                receipt_columns = [
                    ("receipt_width", "VARCHAR(20) DEFAULT '58mm'"),
                    ("receipt_font_size", "VARCHAR(20) DEFAULT 'medium'"),
                    ("receipt_bold_headers", "BOOLEAN DEFAULT TRUE"),
                    ("receipt_number_format", "VARCHAR(20) DEFAULT 'sequential'"),
                    ("receipt_number_prefix", "VARCHAR(10) DEFAULT 'POS'"),
                    ("receipt_starting_number", "INT DEFAULT 1001"),
                    ("receipt_header_title", "VARCHAR(255)"),
                    ("receipt_header_subtitle", "VARCHAR(255)"),
                    ("receipt_header_message", "TEXT"),
                    ("receipt_show_logo", "BOOLEAN DEFAULT FALSE"),
                    ("receipt_show_address", "BOOLEAN DEFAULT TRUE"),
                    ("receipt_show_contact", "BOOLEAN DEFAULT TRUE"),
                    ("receipt_footer_message", "TEXT"),
                    ("receipt_show_datetime", "BOOLEAN DEFAULT TRUE"),
                    ("receipt_show_cashier", "BOOLEAN DEFAULT TRUE"),
                    ("receipt_show_payment", "BOOLEAN DEFAULT TRUE"),
                    ("receipt_show_qr", "BOOLEAN DEFAULT FALSE"),
                    ("enable_receipt_status_update", "BOOLEAN DEFAULT TRUE"),
                    ("receipt_address", "TEXT"),
                    ("receipt_phone", "VARCHAR(50)"),
                    ("receipt_email", "VARCHAR(255)"),
                    ("receipt_logo_url", "VARCHAR(500)")
                ]
                
                for column_name, column_definition in receipt_columns:
                    try:
                        cursor.execute(f"ALTER TABLE hotel_settings ADD COLUMN {column_name} {column_definition}")
                        print(f"Added {column_name} column to hotel_settings table")
                    except Exception as e:
                        # Column might already exist, ignore error
                        pass
                
                # Create test admin user if it doesn't exist
                cursor.execute("SELECT COUNT(*) FROM employees WHERE employee_code = '0001'")
                admin_exists = cursor.fetchone()[0]
                
                if admin_exists == 0:
                    cursor.execute("""
                        INSERT INTO employees (full_name, email, phone_number, employee_code, password_hash, role, status)
                        VALUES ('Admin User', 'admin@hotel.com', '1234567890', '0001', %s, 'admin', 'active')
                    """, (hash_password('admin123'),))
                    print("Test admin user created: employee_code=0001, password=admin123")
                
                # Add sample items if they don't exist
                cursor.execute("SELECT COUNT(*) FROM items")
                items_count = cursor.fetchone()[0]
                
                if items_count == 0:
                    sample_items = [
                        ('Coffee', 'Fresh brewed coffee', 150.00, 'Beverages', 50),
                        ('Tea', 'Hot tea', 100.00, 'Beverages', 30),
                        ('Sandwich', 'Club sandwich', 300.00, 'Food', 20),
                        ('Cake', 'Chocolate cake slice', 200.00, 'Dessert', 15),
                        ('Water', 'Bottled water', 50.00, 'Beverages', 100)
                    ]
                    
                    for item in sample_items:
                        cursor.execute("""
                            INSERT INTO items (name, description, price, category, stock)
                            VALUES (%s, %s, %s, %s, %s)
                        """, item)
                    print("Sample items created")
                
                # Add sample sales data if it doesn't exist
                cursor.execute("SELECT COUNT(*) FROM sales")
                sales_count = cursor.fetchone()[0]
                
                if sales_count == 0:
                    # Get the admin user ID
                    cursor.execute("SELECT id FROM employees WHERE employee_code = '0001'")
                    admin_id = cursor.fetchone()[0]
                    
                    # Create sample sales for today
                    from datetime import datetime, timedelta, timedelta
                    today = datetime.now()
                    
                    sample_sales = [
                        ('R001', admin_id, 'Admin User', '0001', 500.00, 50.00, 550.00, 'confirmed', today.strftime('%Y-%m-%d 10:30:00')),
                        ('R002', admin_id, 'Admin User', '0001', 300.00, 30.00, 330.00, 'confirmed', today.strftime('%Y-%m-%d 11:15:00')),
                        ('R003', admin_id, 'Admin User', '0001', 750.00, 75.00, 825.00, 'confirmed', today.strftime('%Y-%m-%d 12:00:00')),
                        ('R004', admin_id, 'Admin User', '0001', 200.00, 20.00, 220.00, 'confirmed', today.strftime('%Y-%m-%d 14:30:00')),
                        ('R005', admin_id, 'Admin User', '0001', 400.00, 40.00, 440.00, 'confirmed', today.strftime('%Y-%m-%d 16:00:00'))
                    ]
                    
                    for sale in sample_sales:
                        cursor.execute("""
                            INSERT INTO sales (receipt_number, employee_id, employee_name, employee_code, 
                                             subtotal, tax_amount, total_amount, status, sale_date)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, sale)
                    
                    # Get the sales IDs and create sample sales_items
                    cursor.execute("SELECT id FROM sales ORDER BY id")
                    sale_ids = [row[0] for row in cursor.fetchall()]
                    
                    # Get item IDs
                    cursor.execute("SELECT id, price FROM items LIMIT 3")
                    items = cursor.fetchall()
                    
                    # Create sample sales_items
                    for i, sale_id in enumerate(sale_ids):
                        for j, (item_id, price) in enumerate(items):
                            quantity = (i + j + 1) % 3 + 1  # 1-3 quantity
                            cursor.execute("""
                                INSERT INTO sales_items (sale_id, item_id, item_name, quantity, unit_price, total_price)
                                VALUES (%s, %s, %s, %s, %s, %s)
                            """, (sale_id, item_id, f'Item {j+1}', quantity, price, price * quantity))
                    
                    print("Sample sales data created")
                
                # Create cash_drawer_transactions table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cash_drawer_transactions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        employee_id INT NOT NULL,
                        transaction_type ENUM('cash_in', 'cash_out') NOT NULL,
                        amount DECIMAL(10,2) NOT NULL,
                        description TEXT,
                        status ENUM('completed', 'pending', 'cancelled') DEFAULT 'completed',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
                    )
                """)
                
                # Create employee_payments table for tracking individual employee payments
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS employee_payments (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        employee_id INT NOT NULL,
                        cashier_id INT NOT NULL,
                        amount DECIMAL(10,2) NOT NULL,
                        status ENUM('completed', 'pending', 'cancelled') DEFAULT 'completed',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE,
                        FOREIGN KEY (cashier_id) REFERENCES employees(id) ON DELETE CASCADE
                    )
                """)
                print("Employee payments table created or already exists")
                
                # Create employee_balances table for tracking outstanding balances
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS employee_balances (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        employee_id INT NOT NULL UNIQUE,
                        total_sales DECIMAL(10,2) DEFAULT 0,
                        total_payments DECIMAL(10,2) DEFAULT 0,
                        outstanding_balance DECIMAL(10,2) DEFAULT 0,
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
                    )
                """)
                
                # Initialize balances for existing employees
                cursor.execute("""
                    INSERT IGNORE INTO employee_balances (employee_id, total_sales, total_payments, outstanding_balance)
                    SELECT 
                        e.id,
                        COALESCE(SUM(s.total_amount), 0) as total_sales,
                        0 as total_payments,
                        COALESCE(SUM(s.total_amount), 0) as outstanding_balance
                    FROM employees e
                    LEFT JOIN sales s ON e.id = s.employee_id 
                        AND s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                    WHERE e.status = 'active'
                    GROUP BY e.id
                """)
                
                # Create cashier_logs table for audit tracking
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cashier_logs (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        cashier_id INT NOT NULL,
                        action_type ENUM('create', 'edit', 'delete', 'view', 'open_drawer', 'close_drawer', 'count_cash', 'safe_drop') NOT NULL,
                        table_name VARCHAR(100),
                        record_id INT,
                        old_values JSON,
                        new_values JSON,
                        description TEXT,
                        ip_address VARCHAR(45),
                        user_agent TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (cashier_id) REFERENCES employees(id) ON DELETE CASCADE
                    )
                """)
                
                # Create cash_drawer_sessions table for tracking sessions
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cash_drawer_sessions (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        cashier_id INT NOT NULL,
                        session_date DATE NOT NULL,
                        start_time TIMESTAMP NOT NULL,
                        end_time TIMESTAMP NULL,
                        starting_amount DECIMAL(10,2) NOT NULL,
                        ending_amount DECIMAL(10,2) NULL,
                        status ENUM('active', 'closed', 'auto_closed') DEFAULT 'active',
                        total_cash_in DECIMAL(10,2) DEFAULT 0,
                        total_cash_out DECIMAL(10,2) DEFAULT 0,
                        total_sales DECIMAL(10,2) DEFAULT 0,
                        variance DECIMAL(10,2) DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (cashier_id) REFERENCES employees(id) ON DELETE CASCADE,
                        UNIQUE KEY unique_active_session (cashier_id, status) USING BTREE
                    )
                """)
                
                connection.commit()
                print("Database tables initialized successfully")
        except Exception as e:
            print(f"Database initialization error: {e}")
        finally:
            connection.close()

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def verify_password(stored_password, provided_password):
    """Verify a password against its hash"""
    return stored_password == provided_password

def hash_password(password):
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def get_role_dashboard_url(role):
    """Get the appropriate dashboard URL based on employee role"""
    role_urls = {
        'admin': '/admin/dashboard',
        'manager': '/manager/dashboard',
        'cashier': '/cashier/dashboard',
        'butchery': '/butchery/dashboard',
        'employee': '/employee/dashboard'
    }
    return role_urls.get(role, '/employee/dashboard')

def get_hotel_settings():
    """Get hotel settings from database"""
    try:
        connection = get_db_connection()
        if not connection:
            return {
                'hotel_name': 'Hotel POS',
                'company_email': '',
                'company_phone': '',
                'hotel_address': '',
                'payment_method': 'buy_goods',
                'till_number': '',
                'business_number': '',
                'account_number': ''
            }
        
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM hotel_settings ORDER BY id DESC LIMIT 1")
            settings = cursor.fetchone()
            
            if settings:
                return {
                    'hotel_name': settings[1],
                    'company_email': settings[2],
                    'company_phone': settings[3],
                    'hotel_address': settings[4],
                    'business_type': settings[5] if len(settings) > 5 else '',
                    'payment_method': settings[6] if len(settings) > 6 else 'buy_goods',
                    'till_number': settings[7] if len(settings) > 7 else '',
                    'business_number': settings[8] if len(settings) > 8 else '',
                    'account_number': settings[9] if len(settings) > 9 else ''
                }
            else:
                return {
                    'hotel_name': 'Hotel POS',
                    'company_email': '',
                    'company_phone': '',
                    'hotel_address': '',
                    'business_type': '',
                    'payment_method': 'buy_goods',
                    'till_number': '',
                    'business_number': '',
                    'account_number': ''
                }
    except Exception as e:
        print(f"Error fetching hotel settings: {e}")
        return {
            'hotel_name': 'Hotel POS',
            'company_email': '',
            'company_phone': '',
            'hotel_address': '',
            'business_type': '',
            'payment_method': 'buy_goods',
            'till_number': '',
            'business_number': '',
            'account_number': ''
        }
    finally:
        if 'connection' in locals():
            connection.close()

def get_employee_profile_photo(employee_id):
    """Get employee profile photo from database"""
    if not employee_id:
        return None
    
    connection = get_db_connection()
    if not connection:
        return None
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT profile_photo FROM employees WHERE id = %s", (employee_id,))
            result = cursor.fetchone()
            return result[0] if result and result[0] else None
    except Exception as e:
        print(f"Error fetching employee profile photo: {e}")
        return None
    finally:
        connection.close()
@app.route('/api/admin/cash-drawer/session/<int:session_id>/logs', methods=['GET'])
def admin_session_logs(session_id: int):
    """Return audit logs that happened within a session window for that cashier."""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Get session window and cashier
            cursor.execute("""
                SELECT cashier_id, session_date, start_time, COALESCE(end_time, NOW()) as end_time
                FROM cash_drawer_sessions WHERE id = %s
            """, (session_id,))
            sess = cursor.fetchone()
            if not sess:
                return jsonify({'success': False, 'message': 'Session not found'}), 404

            st = sess['start_time']
            et = sess['end_time']
            cashier_id = sess['cashier_id']

            try:
                cursor.execute(
                    """
                    SELECT action_type, table_name, record_id, old_values, new_values, description,
                           ip_address, user_agent, created_at
                    FROM cashier_logs
                    WHERE cashier_id = %s AND created_at BETWEEN %s AND %s
                    ORDER BY created_at ASC
                    """,
                    (cashier_id, st, et),
                )
                rows = cursor.fetchall() or []
            except Exception as e:
                # Backward compatibility for schemas missing some columns
                if hasattr(e, 'args') and e.args and 'Unknown column' in str(e):
                    cursor.execute(
                        """
                        SELECT action_type, table_name, record_id, description, created_at
                        FROM cashier_logs
                        WHERE cashier_id = %s AND created_at BETWEEN %s AND %s
                        ORDER BY created_at ASC
                        """,
                        (cashier_id, st, et),
                    )
                    base_rows = cursor.fetchall() or []
                    rows = []
                    for r in base_rows:
                        r = dict(r)
                        # backfill missing optional fields as None
                        r['old_values'] = None
                        r['new_values'] = None
                        r['ip_address'] = None
                        r['user_agent'] = None
                        rows.append(r)
                else:
                    raise
            for r in rows:
                if r.get('created_at'):
                    r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M')
            return jsonify({'success': True, 'logs': rows})
    except Exception as e:
        print(f"Error fetching session logs: {repr(e)}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching logs'}), 500
    finally:
        connection.close()

@app.route('/')
def index():
    """Landing page - redirects to Point of Sale"""
    return redirect(url_for('point_of_sale'))

@app.route('/dashboard')
def dashboard():
    """Main dashboard"""
    return render_template('dashboard.html')

@app.route('/pos')
def point_of_sale():
    """Point of Sale page"""
    hotel_settings = get_hotel_settings()
    return render_template('pos.html', hotel_settings=hotel_settings)


@app.route('/admin/dashboard')
def admin_dashboard():
    """Admin dashboard"""
    if 'employee_id' not in session or session.get('employee_role') != 'admin':
        # For testing, set a test admin session
        session['employee_id'] = 1
        session['employee_name'] = 'Admin User'
        session['employee_role'] = 'admin'
        session['employee_code'] = '0001'
    
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('dashboards/admin_dashboard.html', 
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/manager/dashboard')
def manager_dashboard():
    """Manager dashboard"""
    if 'employee_id' not in session or session.get('employee_role') != 'manager':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('dashboards/manager_dashboard.html', 
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/manager/human-resources')
def manager_human_resources():
    """Manager human resources management"""
    if 'employee_id' not in session or session.get('employee_role') != 'manager':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('manager/human_resources.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/manager/item-management')
def manager_item_management():
    """Manager item management"""
    if 'employee_id' not in session or session.get('employee_role') != 'manager':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('manager/item_management.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/manager/analytics')
def manager_analytics():
    """Manager analytics and reports"""
    if 'employee_id' not in session or session.get('employee_role') != 'manager':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('manager/analytics.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/manager/settings')
def manager_settings():
    """Manager system settings"""
    if 'employee_id' not in session or session.get('employee_role') != 'manager':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('manager/settings.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/manager/off-days-management')
def manager_off_days_management():
    """Manager off days management"""
    if 'employee_id' not in session or session.get('employee_role') != 'manager':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('manager/off_days_management.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)


@app.route('/cashier/dashboard')
def cashier_dashboard():
    """Cashier dashboard"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('dashboards/cashier_dashboard.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/cashier/cash-drawer')
def cashier_cash_drawer():
    """Cashier cash drawer management"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('cashier/cash_drawer.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/cashier/stock-management')
def cashier_stock_management():
    """Cashier stock management"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('cashier/stock_management.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/stock-audits')
def stock_audits():
    """Stock audits page - displays all stock transactions"""
    if 'employee_id' not in session:
        return redirect(url_for('index'))
    # Allow access to admin, manager, and cashier roles
    employee_role = session.get('employee_role')
    if employee_role not in ['admin', 'manager', 'cashier']:
        return redirect(url_for('index'))
    
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('stock_audits.html',
                         employee_name=session.get('employee_name'),
                         employee_role=employee_role,
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/api/cashier/stock-data', methods=['GET'])
def get_cashier_stock_data():
    """Get stock data for cashier stock management page"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            # Get all items with stock information
            cursor.execute("""
                SELECT 
                    id, name, description, price, category, stock, status, 
                    image_url, sku, stock_update_enabled, low_stock_threshold,
                    CASE 
                        WHEN stock_update_enabled = FALSE THEN 'No Tracking'
                        WHEN stock = 0 OR stock IS NULL THEN 'Out of Stock'
                        WHEN stock <= COALESCE(low_stock_threshold, 10) THEN 'Low Stock'
                        ELSE 'Good Stock'
                    END as stock_status
                FROM items 
                WHERE status = 'active'
                ORDER BY 
                    CASE 
                        WHEN stock = 0 OR stock IS NULL THEN 1
                        WHEN stock <= COALESCE(low_stock_threshold, 10) THEN 2
                        ELSE 3
                    END,
                    name
            """)
            items = cursor.fetchall()
            
            # Get summary statistics
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_items,
                    SUM(CASE WHEN stock_update_enabled = TRUE AND stock > COALESCE(low_stock_threshold, 10) THEN 1 ELSE 0 END) as good_stock_count,
                    SUM(CASE WHEN stock_update_enabled = TRUE AND stock > 0 AND stock <= COALESCE(low_stock_threshold, 10) THEN 1 ELSE 0 END) as low_stock_count,
                    SUM(CASE WHEN stock_update_enabled = TRUE AND (stock = 0 OR stock IS NULL) THEN 1 ELSE 0 END) as out_of_stock_count,
                    SUM(CASE WHEN stock_update_enabled = FALSE THEN 1 ELSE 0 END) as no_tracking_count
                FROM items 
                WHERE status = 'active'
            """)
            stats = cursor.fetchone()
            
            # Get today's sales count for items sold
            cursor.execute("""
                SELECT COUNT(DISTINCT si.item_name) as items_sold_today
                FROM sales_items si
                JOIN sales s ON si.sale_id = s.id
                WHERE DATE(s.sale_date) = CURDATE()
                AND s.employee_id = %s
            """, (session.get('employee_id'),))
            sales_data = cursor.fetchone()
            
            items_list = []
            for item in items:
                items_list.append({
                    'id': item[0],
                    'name': item[1],
                    'description': item[2],
                    'price': float(item[3]) if item[3] else 0.0,
                    'category': item[4],
                    'stock': item[5] or 0,
                    'status': item[6],
                    'image_url': item[7],
                    'sku': item[8],
                    'stock_update_enabled': bool(item[9]) if item[9] is not None else True,
                    'low_stock_threshold': item[10] or 10,
                    'stock_status': item[11]
                })
            
            return jsonify({
                'success': True, 
                'items': items_list,
                'stats': {
                    'total_items': stats[0] or 0,
                    'good_stock_count': stats[1] or 0,
                    'low_stock_count': stats[2] or 0,
                    'out_of_stock_count': stats[3] or 0,
                    'no_tracking_count': stats[4] or 0,
                    'items_sold_today': sales_data[0] or 0
                }
            })
            
    except Exception as e:
        print(f"Error fetching cashier stock data: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch stock data'})
    finally:
        connection.close()

@app.route('/api/stock-audits', methods=['GET'])
def get_stock_audits():
    """Get stock transactions with filters for stock audits page"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    employee_role = session.get('employee_role')
    if employee_role not in ['admin', 'manager', 'cashier']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        # Get filter parameters
        date_filter = request.args.get('date_filter', 'all')  # 'all', 'day', 'range', 'month'
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        month = request.args.get('month')  # Format: YYYY-MM
        employee_id = request.args.get('employee_id')
        item_id = request.args.get('item_id')
        transaction_type = request.args.get('transaction_type')  # 'stock_in', 'stock_out', or 'all'
        
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Build query
            query = """
                SELECT 
                    st.id,
                    st.item_id,
                    i.name as item_name,
                    st.action,
                    st.quantity,
                    st.reason,
                    st.created_at,
                    st.employee_id,
                    st.employee_name,
                    st.price_per_unit,
                    st.total_amount,
                    st.transaction_type,
                    st.place_purchased_from
                FROM stock_transactions st
                LEFT JOIN items i ON st.item_id = i.id
                WHERE 1=1
            """
            params = []
            
            # Filter by item (admin and manager only)
            if employee_role in ['admin', 'manager'] and item_id:
                query += " AND st.item_id = %s"
                params.append(int(item_id))
            
            # Filter by employee
            if employee_id:
                query += " AND st.employee_id = %s"
                params.append(int(employee_id))
            elif employee_role == 'cashier':
                # Cashiers can only see their own transactions
                query += " AND st.employee_id = %s"
                params.append(session.get('employee_id'))
            
            # Filter by transaction type (admin and manager only)
            if employee_role in ['admin', 'manager'] and transaction_type and transaction_type != 'all':
                query += " AND st.action = %s"
                params.append(transaction_type)
            
            # Date filters
            if date_filter == 'day' and date_from:
                query += " AND DATE(st.created_at) = %s"
                params.append(date_from)
            elif date_filter == 'range':
                if date_from:
                    query += " AND DATE(st.created_at) >= %s"
                    params.append(date_from)
                if date_to:
                    query += " AND DATE(st.created_at) <= %s"
                    params.append(date_to)
            elif date_filter == 'month' and month:
                query += " AND DATE_FORMAT(st.created_at, '%%Y-%%m') = %s"
                params.append(month)
            
            # Order by most recent first
            query += " ORDER BY st.created_at DESC LIMIT 1000"
            
            cursor.execute(query, params)
            transactions = cursor.fetchall()
            
            # Convert to list and format dates
            transactions_list = []
            for trans in transactions:
                transactions_list.append({
                    'id': trans['id'],
                    'item_id': trans['item_id'],
                    'item_name': trans['item_name'] or 'Unknown Item',
                    'action': trans['action'],
                    'action_label': 'Stock In' if trans['action'] == 'stock_in' else 'Stock Out',
                    'quantity': trans['quantity'],
                    'reason': trans['reason'] or '',
                    'created_at': trans['created_at'].isoformat() if trans['created_at'] else '',
                    'created_at_formatted': trans['created_at'].strftime('%Y-%m-%d %H:%M:%S') if trans['created_at'] else '',
                    'employee_id': trans['employee_id'],
                    'employee_name': trans['employee_name'] or 'Unknown',
                    'price_per_unit': float(trans['price_per_unit']) if trans['price_per_unit'] else None,
                    'total_amount': float(trans['total_amount']) if trans['total_amount'] else None,
                    'transaction_type': trans['transaction_type'],
                    'place_purchased_from': trans['place_purchased_from']
                })
            
            return jsonify({
                'success': True,
                'transactions': transactions_list,
                'count': len(transactions_list)
            })
            
    except Exception as e:
        print(f"Error fetching stock audits: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Failed to fetch stock audits: {str(e)}'}), 500
    finally:
        connection.close()

@app.route('/api/stock-audits/employees', methods=['GET'])
def get_stock_audits_employees():
    """Get list of employees for filter dropdown (admin/managers only)"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    employee_role = session.get('employee_role')
    if employee_role not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT DISTINCT 
                    st.employee_id,
                    st.employee_name
                FROM stock_transactions st
                WHERE st.employee_id IS NOT NULL
                ORDER BY st.employee_name
            """)
            employees = cursor.fetchall()
            
            employees_list = []
            for emp in employees:
                employees_list.append({
                    'id': emp['employee_id'],
                    'name': emp['employee_name'] or 'Unknown'
                })
            
            return jsonify({
                'success': True,
                'employees': employees_list
            })
            
    except Exception as e:
        print(f"Error fetching employees: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch employees'}), 500
    finally:
        connection.close()

@app.route('/api/stock-audits/items', methods=['GET'])
def get_stock_audits_items():
    """Get list of items for filter dropdown (admin/managers only)"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    employee_role = session.get('employee_role')
    if employee_role not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT DISTINCT 
                    i.id,
                    i.name
                FROM stock_transactions st
                JOIN items i ON st.item_id = i.id
                ORDER BY i.name
            """)
            items = cursor.fetchall()
            
            items_list = []
            for item in items:
                items_list.append({
                    'id': item['id'],
                    'name': item['name']
                })
            
            return jsonify({
                'success': True,
                'items': items_list
            })
            
    except Exception as e:
        print(f"Error fetching items: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch items'}), 500
    finally:
        connection.close()

@app.route('/api/cashier/item-stock-history/<int:item_id>', methods=['GET'])
def get_item_stock_history(item_id):
    """Get previous stock-in data for an item"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            # Get the most recent stock-in transaction for this item
            cursor.execute("""
                SELECT 
                    price_per_unit, 
                    place_purchased_from,
                    created_at,
                    quantity
                FROM stock_transactions 
                WHERE item_id = %s 
                AND action = 'stock_in'
                ORDER BY created_at DESC 
                LIMIT 1
            """, (item_id,))
            
            result = cursor.fetchone()
            
            if result:
                return jsonify({
                    'success': True,
                    'last_price_per_unit': float(result[0]) if result[0] else 0.0,
                    'last_place_purchased_from': result[1] or '',
                    'last_stock_in_date': result[2].isoformat() if result[2] else None,
                    'last_quantity': result[3] or 0
                })
            else:
                return jsonify({
                    'success': True,
                    'last_price_per_unit': 0.0,
                    'last_place_purchased_from': '',
                    'last_stock_in_date': None,
                    'last_quantity': 0
                })
            
    except Exception as e:
        print(f"Error fetching item stock history: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch stock history'})
    finally:
        connection.close()

@app.route('/api/cashier/stock-in', methods=['POST'])
def cashier_stock_in():
    """Allow cashiers to stock in items"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        # Get form data
        item_id = int(request.form.get('item_id', 0))
        quantity = int(request.form.get('quantity', 0))
        price_per_unit = float(request.form.get('price_per_unit', 0))
        place_purchased_from = request.form.get('place_purchased_from', '').strip().upper()
        
        # Get employee information from session
        employee_id = session.get('employee_id')
        employee_name = session.get('employee_name', 'Unknown')
        
        if item_id <= 0 or quantity <= 0:
            return jsonify({'success': False, 'message': 'Invalid item ID or quantity'})
        
        with connection.cursor() as cursor:
            # Get current stock and stock update setting
            cursor.execute("SELECT stock, stock_update_enabled, name FROM items WHERE id = %s", (item_id,))
            result = cursor.fetchone()
            if not result:
                return jsonify({'success': False, 'message': 'Item not found'})
            
            current_stock = result[0] or 0
            stock_update_enabled = result[1] if result[1] is not None else True
            item_name = result[2]
            
            # Calculate new stock only if stock update is enabled
            new_stock = current_stock
            if stock_update_enabled:
                new_stock = current_stock + quantity
                
                # Update stock only if stock update is enabled
                cursor.execute("""
                    UPDATE items 
                    SET stock = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (new_stock, item_id))
            
            # Log stock in transaction
            cursor.execute("""
                INSERT INTO stock_transactions 
                (item_id, action, quantity, price_per_unit, selling_price, 
                 reason, place_purchased_from, employee_id, employee_name, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """, (
                item_id, 'stock_in', quantity, price_per_unit, price_per_unit,
                'CASHIER STOCK IN', place_purchased_from, employee_id, employee_name
            ))
            
            connection.commit()
            
            return jsonify({
                'success': True, 
                'message': f'Successfully stocked in {quantity} units of {item_name}',
                'new_stock': new_stock,
                'stock_updated': stock_update_enabled
            })
            
    except Exception as e:
        print(f"Error processing cashier stock in: {e}")
        connection.rollback()
        return jsonify({'success': False, 'message': 'Failed to process stock in'})
    finally:
        connection.close()

@app.route('/cashier/receipt-confirmation')
def cashier_receipt_confirmation():
    """Cashier receipt confirmation"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('cashier/receipt_confirmation.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/cashier/payments')
def cashier_payments():
    """Cashier payments page showing all employees and their sales"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('cashier/payments.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)
@app.route('/api/cashier/employee-sales', methods=['GET'])
def get_employee_sales_data():
    """Get employee sales data for payments page"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Ensure employee_balances table exists
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS employee_balances (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        employee_id INT NOT NULL UNIQUE,
                        total_sales DECIMAL(10,2) DEFAULT 0,
                        total_payments DECIMAL(10,2) DEFAULT 0,
                        outstanding_balance DECIMAL(10,2) DEFAULT 0,
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
                    )
                """)
                
                # Initialize balances for existing employees who don't have balance records
                cursor.execute("""
                    INSERT IGNORE INTO employee_balances (employee_id, total_sales, total_payments, outstanding_balance)
                    SELECT 
                        e.id,
                        COALESCE(SUM(s.total_amount), 0) as total_sales,
                        0 as total_payments,
                        COALESCE(SUM(s.total_amount), 0) as outstanding_balance
                    FROM employees e
                    LEFT JOIN sales s ON e.id = s.employee_id 
                        AND s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                    WHERE e.status = 'active'
                    GROUP BY e.id
                """)
                
                connection.commit()
            except Exception as table_error:
                print(f"Error creating/initializing employee_balances table: {table_error}")
                # Continue with the query even if table creation fails
            
            # Get all employees with their sales and outstanding balance
            try:
                cursor.execute("""
                SELECT 
                    e.id,
                    e.full_name,
                    e.employee_code,
                    e.status,
                        COUNT(s.id) as total_sales,
                        COALESCE(SUM(s.total_amount), 0) as total_revenue,
                        COALESCE(eb.total_payments, 0) as total_payments,
                        COALESCE(eb.outstanding_balance, SUM(s.total_amount)) as outstanding_balance
                    FROM employees e
                    LEFT JOIN sales s ON e.id = s.employee_id 
                        AND s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                    LEFT JOIN employee_balances eb ON e.id = eb.employee_id
                    WHERE e.status = 'active'
                    GROUP BY e.id, e.full_name, e.employee_code, e.status, eb.total_payments, eb.outstanding_balance
                    ORDER BY outstanding_balance DESC, total_revenue DESC
                """)
            except Exception as query_error:
                print(f"Error with employee_balances join, using fallback query: {query_error}")
                # Fallback query without employee_balances table
                cursor.execute("""
                    SELECT 
                        e.id,
                        e.full_name,
                        e.employee_code,
                        e.status,
                        COUNT(s.id) as total_sales,
                        COALESCE(SUM(s.total_amount), 0) as total_revenue,
                        0 as total_payments,
                        COALESCE(SUM(s.total_amount), 0) as outstanding_balance
                FROM employees e
                LEFT JOIN sales s ON e.id = s.employee_id 
                    AND s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                WHERE e.status = 'active'
                GROUP BY e.id, e.full_name, e.employee_code, e.status
                    ORDER BY outstanding_balance DESC, total_revenue DESC
            """)
            
            employees = cursor.fetchall()
            
            # Get today's sales summary
            cursor.execute("""
                SELECT 
                    COUNT(*) as today_sales,
                    COALESCE(SUM(total_amount), 0) as today_revenue
                FROM sales 
                WHERE DATE(sale_date) = CURDATE()
            """)
            
            today_summary = cursor.fetchone()
            
            # Get this week's sales summary
            cursor.execute("""
                SELECT 
                    COUNT(*) as week_sales,
                    COALESCE(SUM(total_amount), 0) as week_revenue
                FROM sales 
                WHERE sale_date >= DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY)
            """)
            
            week_summary = cursor.fetchone()
            
            # Get total outstanding balance
            try:
                cursor.execute("""
                    SELECT 
                        COALESCE(SUM(outstanding_balance), 0) as total_outstanding
                    FROM employee_balances
                """)
                outstanding_summary = cursor.fetchone()
            except Exception as balance_error:
                print(f"Error getting outstanding balance, using fallback: {balance_error}")
                # Fallback: calculate from sales data
                cursor.execute("""
                    SELECT 
                        COALESCE(SUM(s.total_amount), 0) as total_outstanding
                    FROM employees e
                    LEFT JOIN sales s ON e.id = s.employee_id 
                        AND s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                    WHERE e.status = 'active'
                """)
                outstanding_summary = cursor.fetchone()
            
            # Format employee data
            employees_data = []
            for emp in employees:
                employees_data.append({
                    'id': emp[0],
                    'name': emp[1],
                    'code': emp[2],
                    'status': emp[3],
                    'total_sales': emp[4],
                    'total_revenue': float(emp[5]),
                    'total_payments': float(emp[6]),
                    'outstanding_balance': float(emp[7])
                })
            
            return jsonify({
                'success': True,
                'employees': employees_data,
                'summary': {
                    'today_sales': today_summary[0] if today_summary else 0,
                    'today_revenue': float(today_summary[1]) if today_summary else 0.0,
                    'week_sales': week_summary[0] if week_summary else 0,
                    'week_revenue': float(week_summary[1]) if week_summary else 0.0,
                    'total_outstanding': float(outstanding_summary[0]) if outstanding_summary else 0.0
                }
            })
            
    except Exception as e:
        print(f"Error fetching employee sales data: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch employee sales data'})
    finally:
        connection.close()

@app.route('/api/cashier/process-payment', methods=['POST'])
def process_employee_payment():
    """Process payment for an individual employee"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    
    required_fields = ['employee_id', 'amount']
    for field in required_fields:
        if field not in data:
            return jsonify({'success': False, 'message': f'Missing required field: {field}'}), 400
    
    employee_id = data['employee_id']
    amount = data['amount']
    cashier_id = session.get('employee_id')
    
    if amount <= 0:
        return jsonify({'success': False, 'message': 'Payment amount must be greater than 0'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Ensure employee_payments table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employee_payments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    employee_id INT NOT NULL,
                    cashier_id INT NOT NULL,
                    amount DECIMAL(10,2) NOT NULL,
                    status ENUM('completed', 'pending', 'cancelled') DEFAULT 'completed',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE,
                    FOREIGN KEY (cashier_id) REFERENCES employees(id) ON DELETE CASCADE
                )
            """)
            
            # Ensure employee_balances table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employee_balances (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    employee_id INT NOT NULL UNIQUE,
                    total_sales DECIMAL(10,2) DEFAULT 0,
                    total_payments DECIMAL(10,2) DEFAULT 0,
                    outstanding_balance DECIMAL(10,2) DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
                )
            """)
            
            # Verify employee exists and is active
            cursor.execute("SELECT id, full_name, employee_code FROM employees WHERE id = %s AND status = 'active'", (employee_id,))
            employee = cursor.fetchone()
            if not employee:
                return jsonify({'success': False, 'message': 'Employee not found or inactive'}), 404
            
            # Start transaction
            connection.autocommit = False
            
            # Insert payment record
            cursor.execute("""
                INSERT INTO employee_payments (employee_id, cashier_id, amount, status)
                VALUES (%s, %s, %s, 'completed')
            """, (employee_id, cashier_id, amount))
            
            payment_id = cursor.lastrowid
            
            # Update or create employee balance record
            print(f"Updating balance for employee {employee_id} with payment amount {amount}")
            cursor.execute("""
                INSERT INTO employee_balances (employee_id, total_sales, total_payments, outstanding_balance)
                SELECT 
                    %s,
                    COALESCE(SUM(s.total_amount), 0),
                    %s,
                    COALESCE(SUM(s.total_amount), 0) - %s
                FROM sales s
                WHERE s.employee_id = %s 
                AND s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                ON DUPLICATE KEY UPDATE
                    total_payments = total_payments + %s,
                    outstanding_balance = outstanding_balance - %s,
                    last_updated = CURRENT_TIMESTAMP
            """, (employee_id, amount, amount, employee_id, amount, amount))
            print(f"Balance update query executed successfully")
            
            # Get updated balance
            cursor.execute("""
                SELECT outstanding_balance FROM employee_balances WHERE employee_id = %s
            """, (employee_id,))
            balance_result = cursor.fetchone()
            new_balance = balance_result[0] if balance_result else 0
            
            # Log the payment action
            cursor.execute("""
                INSERT INTO cashier_logs (cashier_id, action_type, table_name, record_id, description)
                VALUES (%s, 'create', 'employee_payments', %s, %s)
            """, (cashier_id, payment_id, f"Processed payment of KES {amount} for employee {employee[1]} ({employee[2]}). New balance: KES {new_balance}"))
            
            # Commit transaction
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Payment processed successfully',
                'payment_id': payment_id,
                'employee_name': employee[1],
                'employee_code': employee[2],
                'amount': amount,
                'new_balance': float(new_balance)
            })
            
    except Exception as e:
        connection.rollback()
        print(f"Error processing payment: {e}")
        print(f"Payment data: employee_id={employee_id}, amount={amount}, cashier_id={cashier_id}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Failed to process payment: {str(e)}'}), 500
    finally:
        # Restore autocommit
        connection.autocommit = True
        connection.close()

@app.route('/api/init-employee-balances', methods=['POST'])
def init_employee_balances():
    """Initialize employee balances table and data"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Create employee_balances table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employee_balances (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    employee_id INT NOT NULL UNIQUE,
                    total_sales DECIMAL(10,2) DEFAULT 0,
                    total_payments DECIMAL(10,2) DEFAULT 0,
                    outstanding_balance DECIMAL(10,2) DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
                )
            """)
            
            # Initialize balances for all active employees
            cursor.execute("""
                INSERT IGNORE INTO employee_balances (employee_id, total_sales, total_payments, outstanding_balance)
                SELECT 
                    e.id,
                    COALESCE(SUM(s.total_amount), 0) as total_sales,
                    0 as total_payments,
                    COALESCE(SUM(s.total_amount), 0) as outstanding_balance
                FROM employees e
                LEFT JOIN sales s ON e.id = s.employee_id 
                    AND s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                WHERE e.status = 'active'
                GROUP BY e.id
            """)
            
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Employee balances initialized successfully'
            })
            
    except Exception as e:
        print(f"Error initializing employee balances: {e}")
        return jsonify({'success': False, 'message': 'Failed to initialize employee balances'}), 500
    finally:
        connection.close()

# Cash Drawer API Endpoints

# Text normalization to enforce uppercase and uniform spacing
def normalize_text(value):
    try:
        if value is None:
            return ''
        # Collapse whitespace and uppercase
        return ' '.join(str(value).split()).upper()
    except Exception:
        return str(value).upper() if value is not None else ''
@app.route('/api/cash-drawer/status', methods=['GET'])
def get_cash_drawer_status():
    """Get current cash drawer status. Defaults to active session date or today; accepts ?date=YYYY-MM-DD."""
    # For testing, allow access without session
    if 'employee_id' not in session:
        # Set a test session for development
        session['employee_id'] = 1
        session['employee_role'] = 'cashier'
        session['employee_name'] = 'Test Cashier'
    
    if session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized - Cashier role required'}), 401
    
    try:
        # Decide which date to use
        requested_date = request.args.get('date')
        selected_date = None

        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        if not requested_date:
            try:
                with connection.cursor() as c2:
                    c2.execute(
                        """
                        SELECT session_date FROM cash_drawer_sessions
                        WHERE cashier_id = %s AND status = 'active' LIMIT 1
                        """,
                        (session.get('employee_id'),)
                    )
                    r = c2.fetchone()
                    if r:
                        # r[0] is date
                        selected_date = r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0])
            except Exception:
                pass

        selected_date = requested_date or selected_date or datetime.now().strftime('%Y-%m-%d')

        cursor = connection.cursor()
        cursor.execute("""
            SELECT 
                (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions WHERE employee_id = %s AND DATE(created_at) = %s AND description = 'Starting cash amount') as opening_float,
                (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions WHERE employee_id = %s AND DATE(created_at) = %s AND transaction_type = 'cash_in' AND description != 'Starting cash amount') as cash_ins,
                (SELECT COALESCE(SUM(total_amount), 0) FROM sales WHERE DATE(sale_date) = %s AND employee_id = %s) as cash_sales,
                (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions WHERE employee_id = %s AND DATE(created_at) = %s AND transaction_type = 'cash_out' AND description NOT LIKE %s AND description NOT LIKE %s) as cash_outs,
                (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions WHERE employee_id = %s AND DATE(created_at) = %s AND description LIKE %s) as safe_drops,
                COUNT(CASE WHEN DATE(created_at) = %s THEN 1 END) as today_transactions,
                (SELECT COUNT(*) FROM cash_drawer_transactions WHERE DATE(created_at) = %s AND status = 'pending') as pending_count
            FROM cash_drawer_transactions 
            WHERE employee_id = %s
        """, (
            session.get('employee_id'), selected_date,
            session.get('employee_id'), selected_date,
            selected_date, session.get('employee_id'),
            session.get('employee_id'), selected_date, 'Safe drop%', 'End shift%',
            session.get('employee_id'), selected_date, 'Safe drop%',
            selected_date, selected_date, session.get('employee_id')
        ))
        
        result = cursor.fetchone()
        cursor.close()
        connection.close()
        
        # Calculate current balance: Opening Float + Cash In + Cash Sales - Cash Out - Safe Drop
        opening_float = float(result[0]) if result[0] else 0.0
        cash_ins = float(result[1]) if result[1] else 0.0
        cash_sales = float(result[2]) if result[2] else 0.0
        cash_outs = float(result[3]) if result[3] else 0.0
        safe_drops = float(result[4]) if result[4] else 0.0
        
        current_balance = opening_float + cash_ins + cash_sales - cash_outs - safe_drops
        
        return jsonify({
            'success': True,
            'data': {
                'current_balance': current_balance,
                'today_transactions': result[5] if result[5] else 0,
                'today_sales': cash_sales,
                'pending_count': result[6] if result[6] else 0,
                'drawer_status': 'open'  # You can implement logic to check if drawer is open
            }
        })
    except Exception as e:
        print(f"Error in get_cash_drawer_status: {e}")
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500

@app.route('/api/cash-drawer/open', methods=['POST'])
def open_cash_drawer():
    """Start new cash drawer session with starting amount"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        starting_amount = float(data.get('amount', 0))
        cashier_id = session.get('employee_id')
        
        # Check and auto-close any old sessions first
        check_and_auto_close_sessions()
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        cursor = connection.cursor()
        
        # Check if there's already an active session
        cursor.execute("""
            SELECT id FROM cash_drawer_sessions 
            WHERE cashier_id = %s AND status = 'active'
        """, (cashier_id,))
        
        existing_session = cursor.fetchone()
        if existing_session:
            return jsonify({
                'success': False, 
                'message': 'You already have an active cash drawer session. Please end your current session first.'
            }), 400
        
        # Start transaction
        connection.autocommit = False
        
        try:
            # Create new session
            cursor.execute("""
                INSERT INTO cash_drawer_sessions 
                (cashier_id, session_date, start_time, starting_amount, status)
                VALUES (%s, CURDATE(), NOW(), %s, 'active')
            """, (cashier_id, starting_amount))
            
            session_id = cursor.lastrowid
            
            # Create starting cash transaction
            cursor.execute("""
                INSERT INTO cash_drawer_transactions 
                (employee_id, transaction_type, amount, description, status, created_at)
                VALUES (%s, 'cash_in', %s, 'Starting cash amount', 'completed', NOW())
            """, (cashier_id, starting_amount))
            
            transaction_id = cursor.lastrowid
            
            # Commit transaction
            connection.commit()
            
            # Log the activity
            log_cashier_activity(
                cashier_id=cashier_id,
                action_type='open_drawer',
                table_name='cash_drawer_sessions',
                record_id=session_id,
                new_values={'starting_amount': starting_amount, 'session_date': datetime.now().strftime('%Y-%m-%d')},
                description=f'Started new cash drawer session with starting amount: shs {starting_amount:.2f}',
                request=request
            )
            
            return jsonify({
                'success': True,
                'message': f'New cash drawer session started with shs {starting_amount:.2f}',
                'session_id': session_id
            })
            
        except Exception as e:
            connection.rollback()
            raise e
        finally:
            connection.autocommit = True
            cursor.close()
            connection.close()
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/cash-drawer/cash-out', methods=['POST'])
def add_cash_out():
    """Add cash out transaction"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        amount = float(data.get('amount', 0))
        reason = normalize_text(data.get('reason', ''))
        requires_approval = data.get('requires_approval', 'no')
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO cash_drawer_transactions 
            (employee_id, transaction_type, amount, description, status, created_at)
            VALUES (%s, 'cash_out', %s, %s, %s, NOW())
        """, (session.get('employee_id'), amount, reason, 'pending' if requires_approval == 'yes' else 'completed'))
        
        # Get the transaction ID for logging
        transaction_id = cursor.lastrowid
        
        connection.commit()
        cursor.close()
        connection.close()
        
        # Log the activity
        log_cashier_activity(
            cashier_id=session.get('employee_id'),
            action_type='create',
            table_name='cash_drawer_transactions',
            record_id=transaction_id,
            new_values={'amount': amount, 'description': reason, 'status': 'pending' if requires_approval == 'yes' else 'completed'},
            description=f'Cash out of shs {amount:.2f} recorded: {reason}',
            request=request
        )
        
        return jsonify({
            'success': True,
            'message': f'Cash out of shs {amount:.2f} recorded: {reason}'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/cash-drawer/safe-drop', methods=['POST'])
def safe_drop():
    """Record safe drop transaction"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        amount = float(data.get('amount', 0))
        location = normalize_text(data.get('location', ''))
        received_by = normalize_text(data.get('received_by', ''))
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO cash_drawer_transactions 
            (employee_id, transaction_type, amount, description, status, created_at)
            VALUES (%s, 'cash_out', %s, %s, 'completed', NOW())
        """, (session.get('employee_id'), amount, f'Safe drop to {location} received by {received_by}'))
        
        # Get the transaction ID for logging
        transaction_id = cursor.lastrowid
        
        connection.commit()
        cursor.close()
        connection.close()
        
        # Log the activity
        log_cashier_activity(
            cashier_id=session.get('employee_id'),
            action_type='safe_drop',
            table_name='cash_drawer_transactions',
            record_id=transaction_id,
            new_values={'amount': amount, 'description': f'Safe drop to {location} received by {received_by}'},
            description=f'Safe drop of shs {amount:.2f} to {location} received by {received_by}',
            request=request
        )
        
        return jsonify({
            'success': True,
            'message': f'Safe drop of shs {amount:.2f} to {location} received by {received_by}'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/cash-drawer/cash-in', methods=['POST'])
def add_cash_in():
    """Add cash in transaction"""
    # For testing, allow access without session
    if 'employee_id' not in session:
        # Set a test session for development
        session['employee_id'] = 1
        session['employee_role'] = 'cashier'
        session['employee_name'] = 'Test Cashier'
    
    if session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized - Cashier role required'}), 401
    
    try:
        data = request.get_json()
        amount = float(data.get('amount', 0))
        reason = normalize_text(data.get('reason', ''))
        from_who = normalize_text(data.get('from_who', ''))
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO cash_drawer_transactions 
            (employee_id, transaction_type, amount, description, status, created_at)
            VALUES (%s, 'cash_in', %s, %s, 'completed', NOW())
        """, (session.get('employee_id'), amount, f'Cash in: {reason} from {from_who}'))
        
        # Get the transaction ID for logging
        transaction_id = cursor.lastrowid
        
        connection.commit()
        cursor.close()
        connection.close()
        
        # Log the activity
        log_cashier_activity(
            cashier_id=session.get('employee_id'),
            action_type='create',
            table_name='cash_drawer_transactions',
            record_id=transaction_id,
            new_values={'amount': amount, 'description': f'Cash in: {reason} from {from_who}'},
            description=f'Cash in of shs {amount:.2f} recorded: {reason} from {from_who}',
            request=request
        )
        
        return jsonify({
            'success': True,
            'message': f'Cash in of shs {amount:.2f} recorded: {reason} from {from_who}'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
@app.route('/api/cash-drawer/end-shift', methods=['POST'])
def end_shift():
    """End current cash drawer session with cash count"""
    # For testing, allow access without session
    if 'employee_id' not in session:
        # Set a test session for development
        session['employee_id'] = 1
        session['employee_role'] = 'cashier'
        session['employee_name'] = 'Test Cashier'
    
    if session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized - Cashier role required'}), 401
    
    try:
        data = request.get_json()
        counted_amount = float(data.get('counted_amount', 0))
        cashier_id = session.get('employee_id')
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        cursor = connection.cursor()
        
        # Check if there's an active session
        cursor.execute("""
            SELECT id, starting_amount, session_date FROM cash_drawer_sessions 
            WHERE cashier_id = %s AND status = 'active'
        """, (cashier_id,))
        
        active_session = cursor.fetchone()
        if not active_session:
            return jsonify({
                'success': False, 
                'message': 'No active cash drawer session found. Please start a session first.'
            }), 400
        
        session_id, starting_amount, session_date = active_session
        
        # Get current calculated balance for the session
        cursor.execute("""
            SELECT 
                (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions WHERE employee_id = %s AND DATE(created_at) = %s AND description = 'Starting cash amount') as opening_float,
                (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions WHERE employee_id = %s AND DATE(created_at) = %s AND transaction_type = 'cash_in' AND description != 'Starting cash amount') as cash_ins,
                (SELECT COALESCE(SUM(total_amount), 0) FROM sales WHERE DATE(sale_date) = %s AND employee_id = %s) as cash_sales,
                (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions WHERE employee_id = %s AND DATE(created_at) = %s AND transaction_type = 'cash_out' AND description NOT LIKE %s AND description NOT LIKE %s) as cash_outs,
                (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions WHERE employee_id = %s AND DATE(created_at) = %s AND description LIKE %s) as safe_drops
        """, (cashier_id, session_date, cashier_id, session_date, session_date, cashier_id, cashier_id, session_date, 'Safe drop%', 'End shift%', cashier_id, session_date, 'Safe drop%'))
        
        result = cursor.fetchone()
        
        # Calculate expected balance
        opening_float = float(result[0]) if result[0] else 0.0
        cash_ins = float(result[1]) if result[1] else 0.0
        cash_sales = float(result[2]) if result[2] else 0.0
        cash_outs = float(result[3]) if result[3] else 0.0
        safe_drops = float(result[4]) if result[4] else 0.0
        
        expected_balance = opening_float + cash_ins + cash_sales - cash_outs - safe_drops
        
        # Calculate variance
        variance = counted_amount - expected_balance
        variance_type = "excess" if variance > 0 else "deficit" if variance < 0 else "balanced"
        
        # Record the end shift transaction with variance info
        description = f'End shift - Counted: shs {counted_amount:.2f}, Expected: shs {expected_balance:.2f}, Variance: shs {variance:.2f} ({variance_type})'
        
        # Start transaction
        connection.autocommit = False
        
        try:
            # Create end shift transaction
            cursor.execute("""
            INSERT INTO cash_drawer_transactions 
            (employee_id, transaction_type, amount, description, status, created_at)
            VALUES (%s, 'cash_out', %s, %s, 'completed', NOW())
            """, (cashier_id, counted_amount, description))
            
            transaction_id = cursor.lastrowid
            
            # Update session with end details
            cursor.execute("""
                UPDATE cash_drawer_sessions 
                SET status = 'closed',
                    end_time = NOW(),
                    ending_amount = %s,
                    total_cash_in = %s,
                    total_cash_out = %s,
                    total_sales = %s,
                    variance = %s
                WHERE id = %s
            """, (counted_amount, cash_ins, cash_outs, cash_sales, variance, session_id))
        
            # Create variance tracking table if it doesn't exist
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS cash_drawer_variances (
                id INT AUTO_INCREMENT PRIMARY KEY,
                employee_id INT NOT NULL,
                expected_balance DECIMAL(10,2) NOT NULL,
                actual_counted DECIMAL(10,2) NOT NULL,
                variance_amount DECIMAL(10,2) NOT NULL,
                variance_type ENUM('excess', 'deficit', 'balanced') NOT NULL,
                shift_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
            )
        """)
        
            # Save variance details for audit purposes
            cursor.execute("""
            INSERT INTO cash_drawer_variances (employee_id, expected_balance, actual_counted, variance_amount, variance_type, shift_date)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (cashier_id, expected_balance, counted_amount, variance, variance_type, session_date))
        
            # Commit transaction
            connection.commit()
            
            # Log the activity
            log_cashier_activity(
                cashier_id=cashier_id,
                action_type='close_drawer',
                table_name='cash_drawer_sessions',
                record_id=session_id,
                new_values={'ending_amount': counted_amount, 'variance': variance, 'status': 'closed'},
                description=f'Ended cash drawer session - Counted: shs {counted_amount:.2f}, Expected: shs {expected_balance:.2f}, Variance: shs {variance:.2f}',
                request=request
            )
        
            # Prepare response message
            if variance == 0:
                message = f'Session ended successfully. Cash count matches expected balance: shs {counted_amount:.2f}'
            elif variance > 0:
                message = f'Session ended with EXCESS of shs {variance:.2f}. Counted: shs {counted_amount:.2f}, Expected: shs {expected_balance:.2f}'
            else:
                message = f'Session ended with DEFICIT of shs {abs(variance):.2f}. Counted: shs {counted_amount:.2f}, Expected: shs {expected_balance:.2f}'
            
            return jsonify({
                'success': True,
                'message': message,
                'session_id': session_id,
                'variance': {
                    'expected_balance': expected_balance,
                    'actual_counted': counted_amount,
                    'variance_amount': variance,
                    'variance_type': variance_type
                }
            })
            
        except Exception as e:
            connection.rollback()
            raise e
        finally:
            connection.autocommit = True
            cursor.close()
            connection.close()
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/cash-drawer/transactions', methods=['GET'])
def get_cash_drawer_transactions():
    """Get recent cash drawer transactions"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        # Resolve date to use: explicit ?date, else active session date, else today
        requested_date = request.args.get('date')
        selected_date = None

        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        if not requested_date:
            try:
                with connection.cursor() as c2:
                    c2.execute(
                        """
                        SELECT session_date FROM cash_drawer_sessions
                        WHERE cashier_id = %s AND status = 'active' LIMIT 1
                        """,
                        (session.get('employee_id'),)
                    )
                    r = c2.fetchone()
                    if r:
                        selected_date = r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0])
            except Exception:
                pass

        selected_date = requested_date or selected_date or datetime.now().strftime('%Y-%m-%d')
        cursor = connection.cursor()

        # Find the cashier session window (start/end) for the requested date
        cursor.execute(
            """
            SELECT id, start_time, COALESCE(end_time, NOW()) as end_time
            FROM cash_drawer_sessions
            WHERE cashier_id = %s AND session_date = %s
            ORDER BY start_time DESC
            LIMIT 1
            """,
            (session.get('employee_id'), selected_date)
        )
        session_row = cursor.fetchone()

        if session_row:
            # Filter transactions strictly within the session window
            _, session_start, session_end = session_row
            cursor.execute(
                """
                SELECT id, transaction_type, amount, description, status, created_at
                FROM cash_drawer_transactions
                WHERE employee_id = %s
                  AND created_at BETWEEN %s AND %s
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (session.get('employee_id'), session_start, session_end)
            )
        else:
            # Fallback: no session found that day → return empty list
            cursor.execute("SELECT 1 WHERE 1=0")
        
        transactions = []
        for row in cursor.fetchall():
            transactions.append({
                'id': row[0],
                'type': row[1],
                'amount': float(row[2]),
                'description': row[3],
                'status': row[4],
                'created_at': row[5].strftime('%I:%M %p') if row[5] else ''
            })
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'transactions': transactions
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/cash-drawer/transactions/<int:transaction_id>', methods=['PUT'])
def edit_cash_drawer_transaction(transaction_id):
    """Edit a cash drawer transaction"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        new_amount = float(data.get('amount', 0))
        new_description = normalize_text(data.get('description', ''))
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        cursor = connection.cursor()
        
        # Get the current transaction data for audit logging
        cursor.execute("""
            SELECT amount, description, transaction_type, status, DATE(created_at) as tx_date
            FROM cash_drawer_transactions 
            WHERE id = %s AND employee_id = %s
        """, (transaction_id, session.get('employee_id')))
        
        old_transaction = cursor.fetchone()
        if not old_transaction:
            return jsonify({'success': False, 'message': 'Transaction not found'}), 404
        
        old_values = {
            'amount': float(old_transaction[0]),
            'description': old_transaction[1],
            'transaction_type': old_transaction[2],
            'status': old_transaction[3]
        }

        # Enforce: cannot edit when session ended or transaction not in current active session date
        cursor.execute("""
            SELECT session_date FROM cash_drawer_sessions
            WHERE cashier_id = %s AND status = 'active' LIMIT 1
        """, (session.get('employee_id'),))
        active = cursor.fetchone()
        if not active:
            cursor.close(); connection.close()
            return jsonify({'success': False, 'message': 'Session ended. Editing transactions is not allowed.'}), 403
        active_date = active[0]
        tx_date = old_transaction[4]
        if str(active_date) != str(tx_date):
            cursor.close(); connection.close()
            return jsonify({'success': False, 'message': 'Cannot edit transactions outside the current session date.'}), 403
        
        # Update the transaction
        cursor.execute("""
            UPDATE cash_drawer_transactions 
            SET amount = %s, description = %s
            WHERE id = %s AND employee_id = %s
        """, (new_amount, new_description, transaction_id, session.get('employee_id')))
        
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'message': 'Transaction not found or not updated'}), 404
        
        connection.commit()
        cursor.close()
        connection.close()
        
        # Log the activity
        log_cashier_activity(
            cashier_id=session.get('employee_id'),
            action_type='edit',
            table_name='cash_drawer_transactions',
            record_id=transaction_id,
            old_values=old_values,
            new_values={'amount': new_amount, 'description': new_description, 'transaction_type': old_transaction[2], 'status': old_transaction[3]},
            description=f'Edited transaction ID {transaction_id}: Amount shs {old_values["amount"]:.2f} → shs {new_amount:.2f}',
            request=request
        )
        
        return jsonify({
            'success': True,
            'message': f'Transaction updated successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/cash-drawer/suggestions', methods=['GET'])
def cash_drawer_suggestions():
    """Return unique suggestions for inputs based on previous entries.
    Query params: field=[reason|from_who|location|received_by|description],
                  type=[cash_in|cash_out|safe_drop|any], query=partial text
    """
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    field = request.args.get('field', '').strip()
    tx_type = request.args.get('type', 'any').strip()
    q = normalize_text(request.args.get('query', ''))

    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500

        cursor = connection.cursor()
        # Fetch recent descriptions for this cashier to keep it fast
        if tx_type == 'any':
            cursor.execute("""
                SELECT transaction_type, description
                FROM cash_drawer_transactions
                WHERE employee_id = %s
                ORDER BY id DESC
                LIMIT 500
            """, (session.get('employee_id'),))
        else:
            cursor.execute("""
                SELECT transaction_type, description
                FROM cash_drawer_transactions
                WHERE employee_id = %s AND transaction_type = %s
                ORDER BY id DESC
                LIMIT 500
            """, (session.get('employee_id'), tx_type))

        rows = cursor.fetchall()
        cursor.close(); connection.close()

        def parse_parts(ttype, desc):
            desc_n = normalize_text(desc)
            if ttype == 'cash_in':
                # Format: "Cash in: {REASON} from {FROM_WHO}" or variations
                if 'CASH IN:' in desc_n and ' FROM ' in desc_n:
                    try:
                        after = desc_n.split('CASH IN:', 1)[1].strip()
                        reason_part, from_part = after.split(' FROM ', 1)
                        return {
                            'reason': reason_part.strip(),
                            'from_who': from_part.strip()
                        }
                    except Exception:
                        return {'description': desc_n}
            if ttype == 'cash_out':
                if desc_n.startswith('SAFE DROP TO ') and ' RECEIVED BY ' in desc_n:
                    try:
                        tmp = desc_n[len('SAFE DROP TO '):]
                        location_part, recv_part = tmp.split(' RECEIVED BY ', 1)
                        return {
                            'location': location_part.strip(),
                            'received_by': recv_part.strip()
                        }
                    except Exception:
                        return {'description': desc_n}
                # Regular cash out reason
                return {'reason': desc_n}
            # Fallback
            return {'description': desc_n}

        bucket = set()
        suggestions = []
        for ttype, desc in rows:
            parts = parse_parts(ttype, desc)
            candidate = parts.get(field) if field else parts.get('description')
            if not candidate:
                continue
            if q and not candidate.startswith(q):
                continue
            if candidate not in bucket:
                bucket.add(candidate)
                suggestions.append(candidate)
            if len(suggestions) >= 15:
                break

        return jsonify({'success': True, 'suggestions': suggestions})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/cash-drawer/transactions/<int:transaction_id>', methods=['DELETE'])
def delete_cash_drawer_transaction(transaction_id):
    """Delete a cash drawer transaction"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        cursor = connection.cursor()
        
        # Get the current transaction data for audit logging
        cursor.execute("""
            SELECT amount, description, transaction_type, status, DATE(created_at) as tx_date
            FROM cash_drawer_transactions 
            WHERE id = %s AND employee_id = %s
        """, (transaction_id, session.get('employee_id')))
        
        old_transaction = cursor.fetchone()
        if not old_transaction:
            return jsonify({'success': False, 'message': 'Transaction not found'}), 404
        
        old_values = {
            'amount': float(old_transaction[0]),
            'description': old_transaction[1],
            'transaction_type': old_transaction[2],
            'status': old_transaction[3]
        }

        # Enforce: cannot delete when session ended or transaction not in current active session date
        cursor.execute("""
            SELECT session_date FROM cash_drawer_sessions
            WHERE cashier_id = %s AND status = 'active' LIMIT 1
        """, (session.get('employee_id'),))
        active = cursor.fetchone()
        if not active:
            cursor.close(); connection.close()
            return jsonify({'success': False, 'message': 'Session ended. Deleting transactions is not allowed.'}), 403
        active_date = active[0]
        tx_date = old_transaction[4]
        if str(active_date) != str(tx_date):
            cursor.close(); connection.close()
            return jsonify({'success': False, 'message': 'Cannot delete transactions outside the current session date.'}), 403
        
        # Delete the transaction
        cursor.execute("""
            DELETE FROM cash_drawer_transactions 
            WHERE id = %s AND employee_id = %s
        """, (transaction_id, session.get('employee_id')))
        
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'message': 'Transaction not found or not deleted'}), 404
        
        connection.commit()
        cursor.close()
        connection.close()
        
        # Log the activity
        log_cashier_activity(
            cashier_id=session.get('employee_id'),
            action_type='delete',
            table_name='cash_drawer_transactions',
            record_id=transaction_id,
            old_values=old_values,
            description=f'Deleted transaction ID {transaction_id}: shs {old_values["amount"]:.2f} - {old_values["description"]}',
            request=request
        )
        
        return jsonify({
            'success': True,
            'message': f'Transaction deleted successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/cash-drawer/audit-logs', methods=['GET'])
def get_cashier_audit_logs():
    """Get cashier audit logs"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        cursor = connection.cursor()
        cursor.execute("""
            SELECT 
                cl.action_type,
                cl.table_name,
                cl.record_id,
                cl.old_values,
                cl.new_values,
                cl.description,
                cl.ip_address,
                cl.created_at,
                e.full_name as cashier_name
            FROM cashier_logs cl
            JOIN employees e ON cl.cashier_id = e.id
            WHERE cl.cashier_id = %s
            ORDER BY cl.created_at DESC 
            LIMIT 50
        """, (session.get('employee_id'),))
        
        logs = []
        for row in cursor.fetchall():
            logs.append({
                'action_type': row[0],
                'table_name': row[1],
                'record_id': row[2],
                'old_values': row[3],
                'new_values': row[4],
                'description': row[5],
                'ip_address': row[6],
                'created_at': row[7].strftime('%Y-%m-%d %I:%M %p') if row[7] else '',
                'cashier_name': row[8]
            })
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'logs': logs
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/cash-drawer/session-status', methods=['GET'])
def get_session_status():
    """Get current cash drawer session status"""
    if 'employee_id' not in session or session.get('employee_role') != 'cashier':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        # Check and auto-close any old sessions first
        check_and_auto_close_sessions()
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
            
        cursor = connection.cursor()
        
        # Get current active session
        cursor.execute("""
            SELECT id, session_date, start_time, starting_amount, status
            FROM cash_drawer_sessions 
            WHERE cashier_id = %s AND status = 'active'
        """, (session.get('employee_id'),))
        
        active_session = cursor.fetchone()
        
        cursor.close()
        connection.close()
        
        if active_session:
            session_id, session_date, start_time, starting_amount, status = active_session
            return jsonify({
                'success': True,
                'has_active_session': True,
                'session': {
                    'id': session_id,
                    'date': session_date.strftime('%Y-%m-%d'),
                    'start_time': start_time.strftime('%H:%M:%S'),
                    'starting_amount': float(starting_amount),
                    'status': status
                }
            })
        else:
            return jsonify({
                'success': True,
                'has_active_session': False,
                'message': 'No active session found'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/butchery/dashboard')
def butchery_dashboard():
    """Butchery dashboard"""
    if 'employee_id' not in session or session.get('employee_role') != 'butchery':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('dashboards/butchery_dashboard.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/employee/dashboard')
def employee_dashboard():
    """Employee dashboard"""
    if 'employee_id' not in session or session.get('employee_role') not in ['employee', 'admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('dashboards/employee_dashboard.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/test-permissions-settings')
def test_permissions_settings():
    """Test endpoint to verify permissions settings functionality"""
    try:
        connection = get_db_connection()
        if not connection:
            return "Database connection failed"
        
        cursor = connection.cursor()
        
        # Get current permissions setting
        cursor.execute("""
            SELECT enable_receipt_status_update
            FROM hotel_settings 
            ORDER BY id DESC 
            LIMIT 1
        """)
        result = cursor.fetchone()
        enable_status_update = result[0] if result else True
        
        # Get a sample receipt to test with
        cursor.execute("""
            SELECT id, receipt_number, status, cashier_confirmed
            FROM sales 
            ORDER BY id DESC 
            LIMIT 1
        """)
        receipt = cursor.fetchone()
        
        connection.close()
        
        if receipt:
            return f"""
            <h2>Permissions Settings Test</h2>
            <p><strong>Auto-Update Receipt Status:</strong> {enable_status_update}</p>
            <p><strong>Sample Receipt:</strong></p>
            <ul>
                <li>ID: {receipt[0]}</li>
                <li>Receipt Number: {receipt[1]}</li>
                <li>Status: {receipt[2]}</li>
                <li>Cashier Confirmed: {receipt[3]}</li>
            </ul>
            <p><strong>Test Instructions:</strong></p>
            <ol>
                <li>Go to Admin Settings > Permissions Settings</li>
                <li>Toggle the "Auto-Update Receipt Status" setting</li>
                <li>Go to Cashier Receipt Confirmation</li>
                <li>Confirm/unconfirm the sample receipt</li>
                <li>Check if the receipt status changes when toggle is ON</li>
            </ol>
            """
        else:
            return "No receipts found to test with. Create a sale first."
            
    except Exception as e:
        return f"Error: {e}"

@app.route('/test-hotel-settings')
def test_hotel_settings():
    """Test endpoint to check hotel settings"""
    settings = get_hotel_settings()
    return jsonify({
        'hotel_settings': settings,
        'hotel_name': settings.get('hotel_name', 'NOT_FOUND'),
        'raw_settings': str(settings)
    })

@app.route('/employee/login', methods=['POST'])
def employee_login():
    """Employee login endpoint"""
    data = request.get_json()
    employee_code = data.get('employee_code')
    password = data.get('password')
    
    if not employee_code or not password:
        return jsonify({'success': False, 'message': 'Employee code and password are required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, email, role, status, profile_photo 
                FROM employees 
                WHERE employee_code = %s AND password_hash = %s
            """, (employee_code, hash_password(password)))
            
            employee = cursor.fetchone()
            
            if employee:
                if employee['status'] == 'suspended':
                    return jsonify({'success': False, 'message': 'Your account has been suspended. Please contact your administrator.'}), 403
                elif employee['status'] == 'waiting_approval':
                    return jsonify({'success': False, 'message': 'Your account is waiting for approval. Please contact your administrator to activate your account.'}), 403
                elif employee['status'] != 'active':
                    return jsonify({'success': False, 'message': 'Your account is not active. Please contact your administrator.'}), 403
                
                # Login successful - only for active employees
                session['employee_id'] = employee['id']
                session['employee_name'] = employee['full_name']
                session['employee_role'] = employee['role']
                session['employee_code'] = employee_code
                
                # Determine redirect URL based on role
                redirect_url = get_role_dashboard_url(employee['role'])
                
                return jsonify({
                    'success': True, 
                    'message': 'Login successful',
                    'redirect_url': redirect_url,
                    'employee': {
                        'name': employee['full_name'],
                        'role': employee['role'],
                        'profile_photo': employee['profile_photo']
                    }
                })
            else:
                return jsonify({'success': False, 'message': 'Invalid employee code or password'}), 401
                
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'success': False, 'message': 'An error occurred during login'}), 500
    finally:
        connection.close()
@app.route('/employee/register', methods=['POST'])
def employee_register():
    """Employee registration endpoint"""
    data = request.form
    
    # Validate required fields
    required_fields = ['full_name', 'email', 'phone_number', 'employee_code', 'password', 'confirm_password']
    for field in required_fields:
        if field not in data:
            return jsonify({'success': False, 'message': f'{field.replace("_", " ").title()} is required'}), 400
    
    # Validate password confirmation
    if data.get('password') != data.get('confirm_password'):
        return jsonify({'success': False, 'message': 'Passwords do not match'}), 400
    
    # Validate employee code (4 digits)
    employee_code = data.get('employee_code')
    if not employee_code.isdigit() or len(employee_code) != 4:
        return jsonify({'success': False, 'message': 'Employee code must be exactly 4 digits'}), 400
    
    # Handle profile photo upload
    profile_photo = None
    if 'profile_photo' in request.files:
        file = request.files['profile_photo']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(f"{employee_code}_{file.filename}")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            profile_photo = filename
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if employee code already exists
            cursor.execute("SELECT id FROM employees WHERE employee_code = %s", (employee_code,))
            if cursor.fetchone():
                return jsonify({'success': False, 'message': 'Employee code already exists'}), 400
            
            # Check if email already exists
            cursor.execute("SELECT id FROM employees WHERE email = %s", (data.get('email'),))
            if cursor.fetchone():
                return jsonify({'success': False, 'message': 'Email already exists'}), 400
            
            # Insert new employee
            cursor.execute("""
                INSERT INTO employees (full_name, email, phone_number, employee_code, password_hash, profile_photo, role, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'employee', 'waiting_approval')
            """, (
                data.get('full_name'),
                data.get('email'),
                data.get('phone_number'),
                employee_code,
                hash_password(data.get('password')),
                profile_photo
            ))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Registration successful! Your account is waiting for approval.'})
            
    except Exception as e:
        print(f"Registration error: {e}")
        return jsonify({'success': False, 'message': 'An error occurred during registration'}), 500
    finally:
        connection.close()

@app.route('/employee/logout')
def employee_logout():
    """Employee logout endpoint"""
    session.clear()
    return redirect(url_for('index'))

@app.route('/employee/validate', methods=['POST'])
def validate_employee():
    """Validate employee code for receipt printing"""
    data = request.get_json()
    employee_code = data.get('employee_code')
    
    if not employee_code:
        return jsonify({'success': False, 'message': 'Employee code is required'}), 400
    
    if len(employee_code) != 4 or not employee_code.isdigit():
        return jsonify({'success': False, 'message': 'Employee code must be 4 digits'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Check if employee exists and is active
            cursor.execute("""
                SELECT id, full_name, employee_code, role, status 
                FROM employees 
                WHERE employee_code = %s AND status = 'active'
            """, (employee_code,))
            
            employee = cursor.fetchone()
            
            if employee:
                return jsonify({
                    'success': True, 
                    'employee': {
                        'id': employee['id'],
                        'name': employee['full_name'],
                        'employee_code': employee['employee_code'],
                        'role': employee['role']
                    }
                })
            else:
                return jsonify({'success': False, 'message': 'Invalid employee code'}), 404
                
    except Exception as e:
        print(f"Error validating employee: {e}")
        return jsonify({'success': False, 'message': 'Error validating employee'}), 500
    finally:
        connection.close()

@app.route('/api/receipt/next-number', methods=['GET'])
def get_next_receipt_number():
    """Get the next receipt number from database"""
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Get the highest receipt number from sales table
            cursor.execute("""
                SELECT COALESCE(MAX(CAST(receipt_number AS UNSIGNED)), 1000) as max_receipt_number 
                FROM sales
            """)
            
            result = cursor.fetchone()
            next_receipt_number = result[0] + 1 if result else 1001
            
            return jsonify({
                'success': True,
                'receipt_number': next_receipt_number
            })
            
    except Exception as e:
        print(f"Error getting next receipt number: {e}")
        return jsonify({'success': False, 'message': 'Error getting receipt number'}), 500
    finally:
        connection.close()


# Admin Navigation Routes
@app.route('/admin/role-page-view')
def admin_role_page_view():
    """Admin role page view"""
    if 'employee_id' not in session or session.get('employee_role') != 'admin':
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/role_page_view.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/admin/human-resources')
def admin_human_resources():
    """Admin human resources management"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/human_resources.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/admin/payroll')
def admin_payroll():
    """Admin payroll registration page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/payroll.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/admin/item-management')
def admin_item_management():
    """Admin item management"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/item_management.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/admin/analytics')
def admin_analytics():
    """Admin analytics and reports"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/analytics.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/admin/settings')
def admin_settings():
    """Admin system settings"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/settings.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/admin/off-days-management')
def admin_off_days_management():
    """Off days management page - shows calendar with all employees and their off days"""
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/off_days_management.html',
                         employee_name=session.get('employee_name', 'Guest'),
                         employee_role=session.get('employee_role', 'guest'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/admin/cashiers')
def admin_cashiers():
    """Admin cashiers management"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/cashiers.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/admin/cashier-transactions')
def admin_cashier_transactions_page():
    """Admin view - all transactions grouped by session"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/cashier_transactions.html',
                           employee_name=session.get('employee_name'),
                           employee_role=session.get('employee_role'),
                           employee_profile_photo=employee_profile_photo,
                           hotel_settings=hotel_settings)

@app.route('/admin/expenses-incurred')
def admin_expenses_incurred_page():
    """Admin view - all cash outs and safe drops"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('admin/expenses_incurred.html',
                           employee_name=session.get('employee_name'),
                           employee_role=session.get('employee_role'),
                           employee_profile_photo=employee_profile_photo,
                           hotel_settings=hotel_settings)

@app.route('/api/get-network-info', methods=['GET'])
def get_network_info():
    """Get local network information"""
    try:
        import socket
        import subprocess
        import platform
        
        # Get local IP address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Connect to a remote address (doesn't actually connect)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        except:
            local_ip = "127.0.0.1"
        finally:
            s.close()
        
        # Extract network range from local IP
        if local_ip.startswith('192.168.'):
            network_range = '.'.join(local_ip.split('.')[:3])
        elif local_ip.startswith('10.'):
            network_range = '.'.join(local_ip.split('.')[:2])
        elif local_ip.startswith('172.'):
            network_range = '.'.join(local_ip.split('.')[:2])
        else:
            # If we can't determine the network, return error
            return jsonify({
                'success': False,
                'error': 'Unable to determine network range from IP: ' + local_ip
            }), 400
        
        return jsonify({
            'success': True,
            'local_ip': local_ip,
            'network_range': network_range
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/scan-wifi-printers', methods=['POST'])
def scan_wifi_printers():
    """Advanced WiFi printer discovery using multiple protocols"""
    try:
        import socket
        import subprocess
        import time
        import re
        import json
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        print("Starting advanced WiFi printer discovery...")
        
        discovered_printers = []
        scan_methods_used = []
        
        # Method 1: mDNS/Bonjour Discovery
        def discover_mdns_printers():
            """Discover printers using mDNS/Bonjour"""
            printers = []
            try:
                print("Attempting mDNS discovery...")
                # Try using avahi-browse (Linux) or dns-sd (macOS/Windows)
                commands = [
                    ['avahi-browse', '-t', '-r', '_printer._tcp'],
                    ['avahi-browse', '-t', '-r', '_ipp._tcp'],
                    ['dns-sd', '-B', '_printer._tcp'],
                    ['dns-sd', '-B', '_ipp._tcp']
                ]
                
                for cmd in commands:
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                        if result.returncode == 0 and result.stdout:
                            # Parse mDNS results
                            lines = result.stdout.split('\n')
                            for line in lines:
                                if 'printer' in line.lower() or 'ipp' in line.lower():
                                    # Extract printer information
                                    parts = line.split()
                                    if len(parts) >= 3:
                                        name = parts[2] if len(parts) > 2 else 'Unknown Printer'
                                        printers.append({
                                            'name': name,
                                            'discovery_method': 'mDNS',
                                            'type': 'network_printer'
                                        })
                            break
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        continue
                        
                scan_methods_used.append('mDNS')
                print(f"mDNS discovery found {len(printers)} printers")
                
            except Exception as e:
                print(f"mDNS discovery failed: {e}")
            
            return printers
        
        # Method 2: ARP Table Analysis
        def discover_arp_devices():
            """Discover devices from ARP table and identify potential printers"""
            printers = []
            try:
                print("Scanning ARP table for network devices...")
                
                # Get ARP table
                if os.name == 'nt':  # Windows
                    result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=10)
                else:  # Linux/macOS
                    result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    lines = result.stdout.split('\n')
                    ips = []
                    
                    for line in lines:
                        # Extract IP addresses from ARP table
                        ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', line)
                        if ip_match:
                            ip = ip_match.group(1)
                            if not ip.startswith('224.') and not ip.endswith('.255'):  # Skip multicast and broadcast
                                ips.append(ip)
                    
                    print(f"Found {len(ips)} devices in ARP table")
                    
                    # Test each IP for printer services
                    def test_printer_services(ip):
                        printer_ports = [9100, 9101, 9102, 515, 631, 80, 443]
                        for port in printer_ports:
                            try:
                                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                sock.settimeout(2)
                                result = sock.connect_ex((ip, port))
                                sock.close()
                                
                                if result == 0:
                                    # Try to get printer info via HTTP
                                    printer_info = get_printer_info_http(ip, port)
                                    if printer_info:
                                        return {
                                            'ip': ip,
                                            'port': port,
                                            'name': printer_info.get('name', f'Network Printer at {ip}'),
                                            'model': printer_info.get('model', 'Unknown'),
                                            'discovery_method': 'ARP+Port',
                                            'status': 'available'
                                        }
                            except:
                                continue
                        return None
                    
                    # Use threading for faster scanning with timeout
                    with ThreadPoolExecutor(max_workers=8) as executor:
                        futures = [executor.submit(test_printer_services, ip) for ip in ips[:15]]  # Limit to first 15 IPs
                        for future in as_completed(futures, timeout=8):  # 8 second timeout
                            try:
                                result = future.result()
                                if result:
                                    printers.append(result)
                            except Exception:
                                continue
                
                scan_methods_used.append('ARP')
                print(f"ARP discovery found {len(printers)} printers")
                
            except Exception as e:
                print(f"ARP discovery failed: {e}")
            
            return printers
        
        # Method 3: SNMP Discovery
        def discover_snmp_printers():
            """Discover printers using SNMP"""
            printers = []
            try:
                print("Attempting SNMP discovery...")
                
                # Get network range from system
                network_info = get_network_info_internal()
                if network_info and 'network_range' in network_info:
                    base_ip = network_info['network_range']
                    
                    # Common SNMP OIDs for printer identification
                    printer_oids = [
                        '1.3.6.1.2.1.25.3.2.1.3.1',  # hrDeviceDescr
                        '1.3.6.1.2.1.1.1.0',         # sysDescr
                    ]
                    
                    def snmp_check(ip):
                        try:
                            # Simple SNMP check (would need pysnmp for full implementation)
                            # For now, we'll use a basic UDP probe
                            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            sock.settimeout(2)
                            
                            # SNMP GET request for sysDescr
                            snmp_request = bytes([
                                0x30, 0x29,  # SEQUENCE
                                0x02, 0x01, 0x00,  # version (SNMPv1)
                                0x04, 0x06, 0x70, 0x75, 0x62, 0x6c, 0x69, 0x63,  # community "public"
                                0xa0, 0x1c,  # GET request
                                0x02, 0x04, 0x00, 0x00, 0x00, 0x01,  # request ID
                                0x02, 0x01, 0x00,  # error status
                                0x02, 0x01, 0x00,  # error index
                                0x30, 0x0e,  # varbind list
                                0x30, 0x0c,  # varbind
                                0x06, 0x08, 0x2b, 0x06, 0x01, 0x02, 0x01, 0x01, 0x01, 0x00,  # OID 1.3.6.1.2.1.1.1.0
                                0x05, 0x00   # NULL
                            ])
                            
                            sock.sendto(snmp_request, (ip, 161))
                            data, addr = sock.recvfrom(1024)
                            sock.close()
                            
                            if data and len(data) > 20:
                                # Basic check if response contains printer-related keywords
                                response_str = str(data).lower()
                                if any(keyword in response_str for keyword in ['printer', 'hp', 'canon', 'epson', 'brother', 'lexmark']):
                                    return {
                                        'ip': ip,
                                        'port': 161,
                                        'name': f'SNMP Printer at {ip}',
                                        'discovery_method': 'SNMP',
                                        'status': 'available'
                                    }
                        except:
                            pass
                        return None
                    
                    # Test a smaller range for SNMP
                    test_ips = [f"{base_ip}.{i}" for i in range(1, 51)]  # Test first 50 IPs
                    
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        futures = [executor.submit(snmp_check, ip) for ip in test_ips]
                        for future in as_completed(futures):
                            result = future.result()
                            if result:
                                printers.append(result)
                
                scan_methods_used.append('SNMP')
                print(f"SNMP discovery found {len(printers)} printers")
                
            except Exception as e:
                print(f"SNMP discovery failed: {e}")
            
            return printers
        
        # Method 4: UPnP Discovery
        def discover_upnp_devices():
            """Discover devices using UPnP/SSDP"""
            printers = []
            try:
                print("Attempting UPnP discovery...")
                
                # UPnP SSDP multicast discovery
                ssdp_request = (
                    'M-SEARCH * HTTP/1.1\r\n'
                    'HOST: 239.255.255.250:1900\r\n'
                    'MAN: "ssdp:discover"\r\n'
                    'ST: upnp:rootdevice\r\n'
                    'MX: 3\r\n\r\n'
                ).encode()
                
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(5)
                sock.sendto(ssdp_request, ('239.255.255.250', 1900))
                
                responses = []
                start_time = time.time()
                while time.time() - start_time < 5:
                    try:
                        data, addr = sock.recvfrom(1024)
                        response = data.decode('utf-8', errors='ignore')
                        if 'printer' in response.lower() or any(brand in response.lower() for brand in ['hp', 'canon', 'epson', 'brother']):
                            responses.append((response, addr[0]))
                    except socket.timeout:
                        break
                    except:
                        continue
                
                sock.close()
                
                for response, ip in responses:
                    printers.append({
                        'ip': ip,
                        'port': 80,
                        'name': f'UPnP Printer at {ip}',
                        'discovery_method': 'UPnP',
                        'status': 'available'
                    })
                
                scan_methods_used.append('UPnP')
                print(f"UPnP discovery found {len(printers)} printers")
                
            except Exception as e:
                print(f"UPnP discovery failed: {e}")
            
            return printers
        
        # Simple and fast network scanning
        print("Running simple WiFi printer discovery...")
        discovered_printers = []
        scan_methods_used = []
        
        # Method 1: Dynamic network range scanning for thermal printers
        print("Scanning network range for thermal printers...")
        
        try:
            # Get local network info dynamically
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            network_base = '.'.join(local_ip.split('.')[:-1])
            print(f"Scanning {network_base}.x network for thermal printers on port 9100...")
            
            # Scan common thermal printer IP ranges
            thermal_ranges = []
            thermal_ranges.extend(range(1, 51))      # .1 to .50
            thermal_ranges.extend(range(100, 201))   # .100 to .200
            
            def test_thermal_printer(host_num):
                ip = f"{network_base}.{host_num}"
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.3)  # Very fast timeout
                    result = sock.connect_ex((ip, 9100))  # Test thermal printer port
                    sock.close()
                    
                    if result == 0:
                        printer_info = get_printer_info(ip, 9100)
                        print(f"[PRINTER] Found thermal printer at {ip}:9100")
                        return {
                            'ip': ip,
                            'port': 9100,
                            'name': printer_info.get('name', f'Thermal Printer at {ip}'),
                            'model': printer_info.get('model', 'ESC/POS Thermal Printer'),
                            'discovery_method': 'Network Scan',
                            'status': 'available'
                        }
                except:
                    pass
                return None
            
            # Use threading for fast scanning
            with ThreadPoolExecutor(max_workers=50) as executor:
                futures = [executor.submit(test_thermal_printer, host_num) for host_num in thermal_ranges]
                
                for future in as_completed(futures, timeout=8):  # 8 second timeout
                    try:
                        result = future.result()
                        if result:
                            discovered_printers.append(result)
                    except:
                        continue
            
            scan_methods_used.append('Network Scan')
        except Exception as e:
            print(f"Network scan failed: {e}")
        
        # Method 2: Dynamic ARP scan for all types of printers
        try:
            print("Scanning ARP table for network printers...")
            result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                # Extract all unique IPs from ARP table
                arp_ips = list(set(re.findall(r'(\d+\.\d+\.\d+\.\d+)', result.stdout)))
                print(f"Found {len(arp_ips)} devices in ARP table, testing for printer services...")
                
                def test_device_ports(ip):
                    # Skip if already found in thermal scan
                    if any(p['ip'] == ip for p in discovered_printers):
                        return None
                    
                    # Test all common printer ports
                    printer_ports = [9100, 9101, 9102, 80, 443, 515, 631]
                    for port in printer_ports:
                        try:
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(0.3)
                            if sock.connect_ex((ip, port)) == 0:
                                sock.close()
                                printer_info = get_printer_info(ip, port)
                                print(f"[FOUND] Found network device at {ip}:{port}")
                                return {
                                    'ip': ip,
                                    'port': port,
                                    'name': printer_info.get('name', f'Network Device at {ip}'),
                                    'model': printer_info.get('model', 'Network Device'),
                                    'discovery_method': 'ARP Discovery',
                                    'status': 'available'
                                }
                            sock.close()
                        except:
                            continue
                    return None
                
                # Test ARP devices with threading
                with ThreadPoolExecutor(max_workers=20) as executor:
                    futures = [executor.submit(test_device_ports, ip) for ip in arp_ips[:20]]  # Limit to 20 for speed
                    
                    for future in as_completed(futures, timeout=5):
                        try:
                            result = future.result()
                            if result:
                                discovered_printers.append(result)
                        except:
                            continue
                            
            scan_methods_used.append('ARP Discovery')
        except Exception as e:
            print(f"ARP scan failed: {e}")
        
        if not scan_methods_used:
            scan_methods_used = ['Network Scan']
        
        # Remove duplicates based on IP address
        unique_printers = {}
        for printer in discovered_printers:
            ip = printer.get('ip')
            if ip and ip not in unique_printers:
                unique_printers[ip] = printer
            elif ip and ip in unique_printers:
                # Merge information from multiple discovery methods
                existing = unique_printers[ip]
                existing['discovery_method'] += f", {printer['discovery_method']}"
                if 'model' in printer and printer['model'] != 'Unknown':
                    existing['model'] = printer['model']
        
        final_printers = list(unique_printers.values())
        
        print(f"Discovery completed. Found {len(discovered_printers)} printers using methods: {', '.join(scan_methods_used)}")
        
        return jsonify({
            'success': True,
            'printers': discovered_printers,
            'scan_methods': scan_methods_used,
            'total_found': len(discovered_printers)
        })
        
    except Exception as e:
        print(f"Advanced WiFi scanning error: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'fallback_message': 'Advanced discovery failed. Please use manual setup.'
        }), 500
@app.route('/api/scan-thermal-printers', methods=['POST'])
def scan_thermal_printers():
    """Real thermal printer discovery - no dummy data"""
    try:
        import socket
        import subprocess
        import re
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        print("[SCAN] Starting real thermal printer discovery...")
        
        discovered_printers = []
        scan_methods_used = []
        
        # Get network range from request
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        network_range = data.get('network_range', '192.168.1')
        
        print(f"Scanning network range: {network_range} for thermal printers...")
        
        # Method 1: Direct Network Scan for Thermal Printers
        def scan_network_for_thermal_printers():
            """Scan network range for thermal printers"""
            printers = []
            thermal_ports = [9100, 9101, 9102, 515, 631]  # Common thermal printer ports
            
            def test_thermal_printer(ip, port):
                sock = None
                test_sock = None
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)  # Reduced timeout
                    result = sock.connect_ex((ip, port))
                    
                    if result == 0:
                        # Test if it responds to ESC/POS commands (thermal printer test)
                        try:
                            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            test_sock.settimeout(0.5)  # Very short timeout
                            test_sock.connect((ip, port))
                            
                            # Send ESC/POS initialization command
                            test_sock.send(b'\x1B\x40')  # ESC @ - Initialize printer
                            
                            return {
                                'name': f'Thermal Printer at {ip}:{port}',
                                'ip': ip,
                                'port': port,
                                'discovery_method': 'Network Scan',
                                'model': 'Thermal Printer',
                                'type': 'thermal'
                            }
                        except:
                            # Still might be a printer, but not responding to ESC/POS
                            return {
                                'name': f'Printer at {ip}:{port}',
                                'ip': ip,
                                'port': port,
                                'discovery_method': 'Network Scan',
                                'model': 'Unknown Printer',
                                'type': 'unknown'
                            }
                except Exception as e:
                    # Skip this IP/port combination
                    pass
                finally:
                    # Ensure sockets are properly closed
                    try:
                        if sock:
                            sock.close()
                    except:
                        pass
                    try:
                        if test_sock:
                            test_sock.close()
                    except:
                        pass
                return None
            
            # Scan the network range
            base_ip = network_range.split('.')
            if len(base_ip) == 3:
                print(f"Scanning {network_range}.1-254 for thermal printers...")
                
            # Use threading for faster scanning with proper timeout handling
            with ThreadPoolExecutor(max_workers=10) as executor:  # Reduced workers
                futures = []
                
                # Limit IP range to prevent server overload
                ip_range = list(range(1, 51)) + list(range(100, 201))  # Common printer IP ranges
                for i in ip_range:
                    ip = f"{network_range}.{i}"
                    for port in thermal_ports:
                        futures.append(executor.submit(test_thermal_printer, ip, port))
                
                try:
                    for future in as_completed(futures, timeout=30):
                        try:
                            result = future.result(timeout=1)  # Individual future timeout
                            if result:
                                printers.append(result)
                                print(f"[SUCCESS] Found thermal printer: {result['name']}")
                        except Exception as e:
                            # Skip failed futures
                            continue
                except Exception as e:
                    print(f"[WARNING] Some futures didn't complete in time: {e}")
                    # Cancel remaining futures
                    for future in futures:
                        if not future.done():
                            future.cancel()
            
            scan_methods_used.append('Network Scan')
            return printers
        
        # Method 2: ARP Table Scan for Active Devices
        def scan_arp_table():
            """Scan ARP table for active devices and test for printers"""
            printers = []
            try:
                print("Scanning ARP table for active devices...")
                
                # Get ARP table
                result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    lines = result.stdout.split('\n')
                    ips = []
                    
                    for line in lines:
                        ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', line)
                        if ip_match:
                            ip = ip_match.group(1)
                            if ip.startswith(network_range.split('.')[0] + '.'):  # Only our network
                                ips.append(ip)
                    
                    print(f"Found {len(ips)} active devices in ARP table")
                    
                    # Test each IP for thermal printer ports
                    thermal_ports = [9100, 9101, 9102, 515, 631]
                    
                    def test_arp_device(ip):
                        for port in thermal_ports:
                            try:
                                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                sock.settimeout(2)
                                result = sock.connect_ex((ip, port))
                                sock.close()
                                
                                if result == 0:
                                    return {
                                        'name': f'Active Printer at {ip}:{port}',
                                        'ip': ip,
                                        'port': port,
                                        'discovery_method': 'ARP',
                                        'model': 'Unknown',
                                        'type': 'unknown'
                                    }
                            except:
                                continue
                        return None
                    
                    # Test devices with threading
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        futures = [executor.submit(test_arp_device, ip) for ip in ips[:15]]  # Limit for speed
                        
                        for future in as_completed(futures, timeout=15):
                            try:
                                result = future.result()
                                if result:
                                    printers.append(result)
                                    print(f"[SUCCESS] Found active printer: {result['name']}")
                            except:
                                continue
                
                scan_methods_used.append('ARP')
                return printers
                
            except Exception as e:
                print(f"ARP scan failed: {e}")
                return []
        
        # Execute discovery methods
        print("[SCAN] Executing thermal printer discovery...")
        
        # Method 1: Network scan for thermal printers
        network_printers = scan_network_for_thermal_printers()
        discovered_printers.extend(network_printers)
        
        # Method 2: ARP table scan
        arp_printers = scan_arp_table()
        discovered_printers.extend(arp_printers)
        
        # Remove duplicates based on IP address
        unique_printers = {}
        for printer in discovered_printers:
            ip = printer.get('ip')
            if ip and ip not in unique_printers:
                unique_printers[ip] = printer
            elif ip and ip in unique_printers:
                # Merge information from multiple discovery methods
                existing = unique_printers[ip]
                existing['discovery_method'] += f", {printer['discovery_method']}"
                if 'model' in printer and printer['model'] != 'Unknown':
                    existing['model'] = printer['model']
        
        final_printers = list(unique_printers.values())
        
        print(f"[SUCCESS] Discovery completed. Found {len(final_printers)} real thermal printers using methods: {', '.join(scan_methods_used)}")
        
        return jsonify({
            'success': True,
            'printers': final_printers,
            'scan_methods': scan_methods_used,
            'total_found': len(final_printers)
        })
        
    except Exception as e:
        print(f"[ERROR] Thermal printer discovery error: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'printers': [],
            'scan_methods': [],
            'total_found': 0
        }), 500

@app.route('/api/test-wifi-printer', methods=['POST'])
def test_wifi_printer():
    """Test connection to a WiFi printer"""
    try:
        data = request.get_json()
        ip = data.get('ip')
        port = data.get('port', 9100)
        
        if not ip:
            return jsonify({'success': False, 'error': 'IP address required'}), 400
        
        # Test connection to printer
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        
        try:
            result = sock.connect_ex((ip, port))
            sock.close()
            
            if result == 0:
                return jsonify({'success': True, 'message': 'Printer is reachable'})
            else:
                return jsonify({'success': False, 'error': 'Printer not reachable'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/print-wifi', methods=['POST'])
def print_wifi():
    """Print to a WiFi printer"""
    try:
        data = request.get_json()
        ip = data.get('ip')
        port = data.get('port', 9100)
        content = data.get('content', '')
        printer_name = data.get('printerName', f'Printer at {ip}')
        
        print(f"=== WiFi Print Request ===")
        print(f"Printer: {printer_name} ({ip}:{port})")
        print(f"Content length: {len(content)} characters")
        print(f"Content preview: {content[:200]}..." if len(content) > 200 else f"Content: {content}")
        
        if not ip or not content:
            print("Error: Missing IP or content")
            return jsonify({'success': False, 'error': 'IP address and content required'}), 400
        
        # Check if this looks like a thermal printer port
        thermal_ports = [9100, 9101, 9102, 515, 631]
        if port not in thermal_ports:
            print(f"Warning: Port {port} is not a typical thermal printer port")
        
        # Send print job to printer
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        
        try:
            print(f"Connecting to {ip}:{port}...")
            sock.connect((ip, port))
            print("Connected successfully")
            
            # Convert content to bytes if it's a string
            if isinstance(content, str):
                content = content.encode('utf-8')
            
            print(f"Sending {len(content)} bytes to printer...")
            
            # Send data in chunks
            chunk_size = 1024
            bytes_sent = 0
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                sock.send(chunk)
                bytes_sent += len(chunk)
            
            print(f"Successfully sent {bytes_sent} bytes to printer")
            sock.close()
            
            return jsonify({
                'success': True, 
                'message': f'Print job sent successfully to {printer_name}',
                'bytes_sent': bytes_sent,
                'printer_info': f'{ip}:{port}'
            })
            
        except socket.timeout:
            error_msg = f'Connection timeout to {ip}:{port}. Printer may be offline or unreachable.'
            print(f"Error: {error_msg}")
            return jsonify({'success': False, 'error': error_msg})
        except ConnectionRefusedError:
            error_msg = f'Connection refused by {ip}:{port}. Device may not be a printer or service is not running.'
            print(f"Error: {error_msg}")
            return jsonify({'success': False, 'error': error_msg})
        except Exception as e:
            error_msg = f'Failed to print to {ip}:{port}: {str(e)}'
            print(f"Error: {error_msg}")
            return jsonify({'success': False, 'error': error_msg})
            
    except Exception as e:
        print(f"WiFi Print API Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500




def get_printer_info(ip, port):
    """Get printer information from IP and port"""
    try:
        # Try to connect and get printer info
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((ip, port))
        
        # Send a simple query to get printer info
        if port in [9100, 9101, 9102]:  # ESC/POS ports
            # Send ESC/POS query
            query = b'\x1B\x40'  # Initialize printer
            sock.send(query)
            
        sock.close()
        
        # Return actual printer information based on port
        port_models = {
            9100: 'ESC/POS Thermal Printer',
            9101: 'ESC/POS Thermal Printer (Alt)',
            9102: 'ESC/POS Thermal Printer (Alt2)',
            515: 'LPR/LPD Network Printer',
            631: 'IPP Network Printer'
        }
        
        return {
            'name': f'WiFi Printer at {ip}:{port}',
            'model': port_models.get(port, 'Network Printer')
        }
    except Exception as e:
        return {
            'name': f'WiFi Printer at {ip}:{port}',
            'model': 'Network Printer'
        }

def get_printer_info_http(ip, port):
    """Get printer information via HTTP"""
    try:
        import urllib.request
        import json
        
        # Try common HTTP endpoints for printer information
        endpoints = [
            f'http://{ip}:{port}/api/printer/status',
            f'http://{ip}:{port}/printer_info',
            f'http://{ip}:{port}/status',
            f'http://{ip}:{port}/',
        ]
        
        for endpoint in endpoints:
            try:
                with urllib.request.urlopen(endpoint, timeout=3) as response:
                    content = response.read().decode('utf-8')
                    
                    # Check if content contains printer-related information
                    if any(keyword in content.lower() for keyword in ['printer', 'hp', 'canon', 'epson', 'brother', 'lexmark']):
                        # Try to parse as JSON first
                        try:
                            data = json.loads(content)
                            return {
                                'name': data.get('name', f'HTTP Printer at {ip}'),
                                'model': data.get('model', 'Network Printer')
                            }
                        except:
                            # Parse HTML/text content for printer info
                            return {
                                'name': f'HTTP Printer at {ip}',
                                'model': 'Network Printer'
                            }
            except:
                continue
                
        return None
        
    except Exception as e:
        return None

def get_network_info_internal():
    """Internal function to get network information"""
    try:
        import socket
        
        # Get local IP address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        except:
            local_ip = "127.0.0.1"
        finally:
            s.close()
        
        # Extract network range from local IP
        if local_ip.startswith('192.168.'):
            network_range = '.'.join(local_ip.split('.')[:3])
        elif local_ip.startswith('10.'):
            network_range = '.'.join(local_ip.split('.')[:2])
        elif local_ip.startswith('172.'):
            network_range = '.'.join(local_ip.split('.')[:2])
        else:
            return None
        
        return {
            'local_ip': local_ip,
            'network_range': network_range
        }
        
    except Exception as e:
        return None

@app.route('/employee/off-days')
def employee_off_days():
    """Employee off days viewing page"""
    if 'employee_id' not in session:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('employee/off_days.html',
                         employee_name=session.get('employee_name'),
                         employee_id=session.get('employee_id'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/employee/profile-management')
def employee_profile_management():
    """Employee profile management page"""
    if 'employee_id' not in session:
        return redirect(url_for('index'))
    
    # Get employee details from database
    connection = get_db_connection()
    if not connection:
        return redirect(url_for('index'))
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, email, phone_number, employee_code, 
                       profile_photo, role, status, created_at, updated_at
                FROM employees 
                WHERE id = %s
            """, (session.get('employee_id'),))
            employee = cursor.fetchone()
            
            if not employee:
                return redirect(url_for('index'))
            
            # Convert datetime objects to strings
            if employee['created_at']:
                employee['created_at'] = employee['created_at'].isoformat()
            if employee['updated_at']:
                employee['updated_at'] = employee['updated_at'].isoformat()
            
            hotel_settings = get_hotel_settings()
            employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
            
            return render_template('employee/profile_management.html',
                                 employee=employee,
                                 employee_name=session.get('employee_name'),
                                 employee_role=session.get('employee_role'),
                                 employee_profile_photo=employee_profile_photo,
                                 hotel_settings=hotel_settings)
            
    except Exception as e:
        print(f"Error fetching employee profile: {e}")
        return redirect(url_for('index'))
    finally:
        connection.close()

# Employee Profile Management API
@app.route('/api/employee/profile/update', methods=['POST'])
def update_employee_profile():
    """Update employee profile information"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.form
    employee_id = session.get('employee_id')
    
    # Validate required fields
    required_fields = ['full_name', 'email', 'phone_number']
    for field in required_fields:
        if field not in data or not data[field].strip():
            return jsonify({'success': False, 'message': f'{field.replace("_", " ").title()} is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if email is already taken by another employee
            cursor.execute("""
                SELECT id FROM employees 
                WHERE email = %s AND id != %s
            """, (data.get('email'), employee_id))
            if cursor.fetchone():
                return jsonify({'success': False, 'message': 'Email already exists'}), 400
            
            # Handle profile photo upload
            profile_photo = None
            if 'profile_photo' in request.files:
                file = request.files['profile_photo']
                if file and file.filename and allowed_file(file.filename):
                    # Get employee code for filename
                    cursor.execute("SELECT employee_code FROM employees WHERE id = %s", (employee_id,))
                    result = cursor.fetchone()
                    if result:
                        employee_code = result[0]
                        filename = secure_filename(f"{employee_code}_{file.filename}")
                        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                        profile_photo = filename
            
            # Update employee information
            if profile_photo:
                cursor.execute("""
                    UPDATE employees 
                    SET full_name = %s, email = %s, phone_number = %s, 
                        profile_photo = %s, updated_at = NOW()
                    WHERE id = %s
                """, (data.get('full_name'), data.get('email'), 
                      data.get('phone_number'), profile_photo, employee_id))
            else:
                cursor.execute("""
                    UPDATE employees 
                    SET full_name = %s, email = %s, phone_number = %s, 
                        updated_at = NOW()
                    WHERE id = %s
                """, (data.get('full_name'), data.get('email'), 
                      data.get('phone_number'), employee_id))
            
            connection.commit()
            
            # Update session with new name
            session['employee_name'] = data.get('full_name')
            
            return jsonify({'success': True, 'message': 'Profile updated successfully'})
            
    except Exception as e:
        print(f"Error updating employee profile: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while updating profile'}), 500
    finally:
        connection.close()

@app.route('/api/employee/profile/change-password', methods=['POST'])
def change_employee_password():
    """Change employee password"""
    if 'employee_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    employee_id = session.get('employee_id')
    
    # Validate required fields
    required_fields = ['current_password', 'new_password', 'confirm_password']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({'success': False, 'message': f'{field.replace("_", " ").title()} is required'}), 400
    
    # Validate password confirmation
    if data.get('new_password') != data.get('confirm_password'):
        return jsonify({'success': False, 'message': 'New passwords do not match'}), 400
    
    # Validate password length
    if len(data.get('new_password')) < 6:
        return jsonify({'success': False, 'message': 'Password must be at least 6 characters long'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Verify current password
            cursor.execute("SELECT password_hash FROM employees WHERE id = %s", (employee_id,))
            result = cursor.fetchone()
            if not result or not verify_password(data.get('current_password'), result[0]):
                return jsonify({'success': False, 'message': 'Current password is incorrect'}), 400
            
            # Update password
            new_password_hash = hash_password(data.get('new_password'))
            cursor.execute("""
                UPDATE employees 
                SET password_hash = %s, updated_at = NOW()
                WHERE id = %s
            """, (new_password_hash, employee_id))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Password changed successfully'})
            
    except Exception as e:
        print(f"Error changing password: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while changing password'}), 500
    finally:
        connection.close()

# HR Management API Endpoints
@app.route('/api/hr/employees', methods=['GET'])
def get_all_employees():
    """Get all employees for HR management and calendar view"""
    # Allow access for calendar viewing without authentication
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, email, phone_number, employee_code, 
                       profile_photo, role, status, created_at, updated_at
                FROM employees 
                ORDER BY created_at DESC
            """)
            employees = cursor.fetchall()
            
            # Convert datetime objects to strings for JSON serialization
            for employee in employees:
                if employee['created_at']:
                    employee['created_at'] = employee['created_at'].isoformat()
                if employee['updated_at']:
                    employee['updated_at'] = employee['updated_at'].isoformat()
            
            return jsonify({'success': True, 'employees': employees})
            
    except Exception as e:
        print(f"Error fetching employees: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching employees'}), 500
    finally:
        connection.close()

@app.route('/api/payroll/register', methods=['POST'])
def register_payroll_profile():
    """Create or update payroll profile for an employee"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    data = request.get_json() or {}
    required = ['employee_id', 'basic_salary', 'payment_frequency']
    for field in required:
        if field not in data or data[field] in [None, '']:
            return jsonify({'success': False, 'message': f'Missing field: {field}'}), 400

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500

    try:
        with connection.cursor() as cursor:
            # Ensure table exists
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS payroll_profiles (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    employee_id INT NOT NULL,
                    basic_salary DECIMAL(12,2) NOT NULL DEFAULT 0,
                    allowances DECIMAL(12,2) NOT NULL DEFAULT 0,
                    deductions DECIMAL(12,2) NOT NULL DEFAULT 0,
                    payment_frequency VARCHAR(32) NOT NULL,
                    bank_name VARCHAR(100),
                    account_number VARCHAR(100),
                    kra_pin VARCHAR(32),
                    nssf VARCHAR(32),
                    nhif VARCHAR(32),
                    helb VARCHAR(32),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uniq_employee (employee_id),
                    CONSTRAINT fk_payroll_employee FOREIGN KEY (employee_id) REFERENCES employees(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )

            # Upsert profile
            cursor.execute(
                """
                INSERT INTO payroll_profiles (
                    employee_id, basic_salary, allowances, deductions, payment_frequency,
                    bank_name, account_number, kra_pin, nssf, nhif, helb
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    basic_salary = VALUES(basic_salary),
                    allowances = VALUES(allowances),
                    deductions = VALUES(deductions),
                    payment_frequency = VALUES(payment_frequency),
                    bank_name = VALUES(bank_name),
                    account_number = VALUES(account_number),
                    kra_pin = VALUES(kra_pin),
                    nssf = VALUES(nssf),
                    nhif = VALUES(nhif),
                    helb = VALUES(helb)
                """,
                (
                    int(data.get('employee_id')),
                    float(data.get('basic_salary') or 0),
                    float(data.get('allowances') or 0),
                    float(data.get('deductions') or 0),
                    str(data.get('payment_frequency') or 'monthly'),
                    (data.get('bank_name') or '').strip() or None,
                    (data.get('account_number') or '').strip() or None,
                    (data.get('kra_pin') or '').strip() or None,
                    (data.get('nssf') or '').strip() or None,
                    (data.get('nhif') or '').strip() or None,
                    (data.get('helb') or '').strip() or None,
                )
            )

        connection.commit()
        return jsonify({'success': True, 'message': 'Payroll profile saved'})
    except Exception as e:
        print(f"Error saving payroll profile: {e}")
        connection.rollback()
        return jsonify({'success': False, 'message': 'Error saving payroll profile'}), 500
    finally:
        connection.close()

@app.route('/api/hr/employees/<int:employee_id>', methods=['GET'])
def get_employee_details(employee_id):
    """Get specific employee details"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, email, phone_number, employee_code, 
                       profile_photo, role, status, created_at, updated_at
                FROM employees 
                WHERE id = %s
            """, (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                return jsonify({'success': False, 'message': 'Employee not found'}), 404
            
            # Convert datetime objects to strings
            if employee['created_at']:
                employee['created_at'] = employee['created_at'].isoformat()
            if employee['updated_at']:
                employee['updated_at'] = employee['updated_at'].isoformat()
            
            return jsonify({'success': True, 'employee': employee})
            
    except Exception as e:
        print(f"Error fetching employee: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching employee details'}), 500
    finally:
        connection.close()
@app.route('/api/hr/employees/<int:employee_id>', methods=['PUT'])
def update_employee(employee_id):
    """Update employee details"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if employee exists
            cursor.execute("SELECT id FROM employees WHERE id = %s", (employee_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Employee not found'}), 404
            
            # Build update query dynamically
            update_fields = []
            update_values = []
            
            if 'full_name' in data:
                update_fields.append("full_name = %s")
                update_values.append(data['full_name'])
            
            if 'email' in data:
                # Check if email already exists for another employee
                cursor.execute("SELECT id FROM employees WHERE email = %s AND id != %s", 
                             (data['email'], employee_id))
                if cursor.fetchone():
                    return jsonify({'success': False, 'message': 'Email already exists'}), 400
                update_fields.append("email = %s")
                update_values.append(data['email'])
            
            if 'phone_number' in data:
                update_fields.append("phone_number = %s")
                update_values.append(data['phone_number'])
            
            if 'password' in data and data['password']:
                update_fields.append("password_hash = %s")
                update_values.append(hash_password(data['password']))
            
            if 'role' in data:
                # Restrict managers from changing roles to admin
                if session.get('employee_role') == 'manager' and data['role'] == 'admin':
                    return jsonify({'success': False, 'message': 'Managers cannot assign admin roles'}), 403
                update_fields.append("role = %s")
                update_values.append(data['role'])
            
            if 'status' in data:
                update_fields.append("status = %s")
                update_values.append(data['status'])
            
            if not update_fields:
                return jsonify({'success': False, 'message': 'No valid fields to update'}), 400
            
            # Add updated_at
            update_fields.append("updated_at = CURRENT_TIMESTAMP")
            update_values.append(employee_id)
            
            query = f"UPDATE employees SET {', '.join(update_fields)} WHERE id = %s"
            cursor.execute(query, update_values)
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Employee updated successfully'})
            
    except Exception as e:
        print(f"Error updating employee: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while updating employee'}), 500
    finally:
        connection.close()

@app.route('/api/hr/employees/<int:employee_id>/approve', methods=['POST'])
def approve_employee(employee_id):
    """Approve pending employee"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if employee exists and is pending
            cursor.execute("SELECT status FROM employees WHERE id = %s", (employee_id,))
            result = cursor.fetchone()
            if not result:
                return jsonify({'success': False, 'message': 'Employee not found'}), 404
            
            if result[0] != 'waiting_approval':
                return jsonify({'success': False, 'message': 'Employee is not pending approval'}), 400
            
            # Approve employee
            cursor.execute("""
                UPDATE employees 
                SET status = 'active', updated_at = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (employee_id,))
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Employee approved successfully'})
            
    except Exception as e:
        print(f"Error approving employee: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while approving employee'}), 500
    finally:
        connection.close()

@app.route('/api/hr/employees/<int:employee_id>/suspend', methods=['POST'])
def suspend_employee(employee_id):
    """Suspend employee"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if employee exists
            cursor.execute("SELECT id FROM employees WHERE id = %s", (employee_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Employee not found'}), 404
            
            # Suspend employee
            cursor.execute("""
                UPDATE employees 
                SET status = 'suspended', updated_at = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (employee_id,))
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Employee suspended successfully'})
            
    except Exception as e:
        print(f"Error suspending employee: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while suspending employee'}), 500
    finally:
        connection.close()

@app.route('/api/hr/employees/<int:employee_id>/activate', methods=['POST'])
def activate_employee(employee_id):
    """Activate employee"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if employee exists
            cursor.execute("SELECT id FROM employees WHERE id = %s", (employee_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Employee not found'}), 404
            
            # Activate employee
            cursor.execute("""
                UPDATE employees 
                SET status = 'active', updated_at = CURRENT_TIMESTAMP 
                WHERE id = %s
            """, (employee_id,))
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Employee activated successfully'})
            
    except Exception as e:
        print(f"Error activating employee: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while activating employee'}), 500
    finally:
        connection.close()

@app.route('/api/admin/cashiers', methods=['GET'])
def get_cashiers():
    """Get all cashiers for admin cashiers management"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, full_name, email, phone_number, employee_code, 
                       profile_photo, role, status, created_at, updated_at
                FROM employees 
                WHERE role = 'cashier'
                ORDER BY created_at DESC
            """)
            cashiers = cursor.fetchall()
            
            # Convert datetime objects to strings for JSON serialization
            for cashier in cashiers:
                if cashier['created_at']:
                    cashier['created_at'] = cashier['created_at'].isoformat()
                if cashier['updated_at']:
                    cashier['updated_at'] = cashier['updated_at'].isoformat()
            
            return jsonify({'success': True, 'cashiers': cashiers})
            
    except Exception as e:
        print(f"Error fetching cashiers: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching cashiers'}), 500
    finally:
        connection.close()

@app.route('/api/admin/cash-drawer/sessions/live', methods=['GET'])
def admin_live_cash_drawer_sessions():
    """Return active cash drawer sessions for all cashiers, including current balance snapshot."""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Get active sessions with cashier info
            cursor.execute(
                """
                SELECT s.id as session_id, s.cashier_id, s.session_date, s.start_time, s.starting_amount,
                       e.full_name, e.employee_code, e.profile_photo
                FROM cash_drawer_sessions s
                JOIN employees e ON e.id = s.cashier_id
                WHERE s.status = 'active'
                ORDER BY s.start_time DESC
                """
            )
            sessions = cursor.fetchall()

            results = []
            active_cashier_ids = set()
            for sess in sessions:
                cashier_id = sess['cashier_id']
                active_cashier_ids.add(cashier_id)
                # Compute current balance similar to status endpoint but for session date
                cursor.execute(
                    """
                    SELECT 
                        (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions
                         WHERE employee_id = %s AND DATE(created_at) = %s AND description = 'Starting cash amount') AS opening_float,
                        (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions
                         WHERE employee_id = %s AND DATE(created_at) = %s AND transaction_type = 'cash_in' AND description != 'Starting cash amount') AS cash_ins,
                        (SELECT COALESCE(SUM(total_amount), 0) FROM sales
                         WHERE employee_id = %s AND DATE(sale_date) = %s) AS cash_sales,
                        (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions
                         WHERE employee_id = %s AND DATE(created_at) = %s AND transaction_type = 'cash_out' AND description NOT LIKE %s AND description NOT LIKE %s) AS cash_outs,
                        (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions
                         WHERE employee_id = %s AND DATE(created_at) = %s AND description LIKE %s) AS safe_drops
                    """,
                    (
                        cashier_id, sess['session_date'],
                        cashier_id, sess['session_date'],
                        cashier_id, sess['session_date'],
                        cashier_id, sess['session_date'], 'Safe drop%', 'End shift%',
                        cashier_id, sess['session_date'], 'Safe drop%'
                    )
                )
                row = cursor.fetchone() or {}
                opening_float = float(row.get('opening_float') or 0)
                cash_ins = float(row.get('cash_ins') or 0)
                cash_sales = float(row.get('cash_sales') or 0)
                cash_outs = float(row.get('cash_outs') or 0)
                safe_drops = float(row.get('safe_drops') or 0)
                current_balance = opening_float + cash_ins + cash_sales - cash_outs - safe_drops

                # Today's counts (for that date)
                cursor.execute(
                    """
                    SELECT 
                        COUNT(CASE WHEN DATE(created_at) = %s THEN 1 END) as today_transactions,
                        (SELECT COUNT(*) FROM cash_drawer_transactions WHERE employee_id = %s AND DATE(created_at) = %s AND status = 'pending') as pending_count
                    FROM cash_drawer_transactions WHERE employee_id = %s
                    """,
                    (sess['session_date'], cashier_id, sess['session_date'], cashier_id)
                )
                counts = cursor.fetchone() or {}
                today_transactions = counts.get('today_transactions') or 0
                pending_count = counts.get('pending_count') or 0

                # Recent transactions within session window
                cursor.execute(
                    """
                    SELECT transaction_type, amount, description, status, created_at
                    FROM cash_drawer_transactions
                    WHERE employee_id = %s AND created_at BETWEEN %s AND NOW()
                    ORDER BY created_at DESC LIMIT 10
                    """,
                    (cashier_id, sess['start_time'])
                )
                tx_rows = cursor.fetchall() or []
                recent_transactions = []
                for r in tx_rows:
                    t_type = r.get('transaction_type')
                    t_amount = float(r.get('amount') or 0)
                    t_desc = r.get('description')
                    t_status = r.get('status')
                    t_time = r.get('created_at')
                    recent_transactions.append({
                        'type': t_type,
                        'amount': t_amount,
                        'description': t_desc,
                        'status': t_status,
                        'created_at': t_time.strftime('%H:%M') if t_time else ''
                    })

                # Recent sales within session date (latest 10)
                cursor.execute(
                    """
                    SELECT receipt_number, total_amount, sale_date
                    FROM sales
                    WHERE employee_id = %s AND DATE(sale_date) = %s
                    ORDER BY sale_date DESC
                    LIMIT 10
                    """,
                    (cashier_id, sess['session_date'])
                )
                sale_rows = cursor.fetchall() or []
                recent_sales = []
                for sr in sale_rows:
                    receipt = sr.get('receipt_number')
                    amount = float(sr.get('total_amount') or 0)
                    sdt = sr.get('sale_date')
                    recent_sales.append({
                        'receipt': receipt,
                        'amount': amount,
                        'time': sdt.strftime('%H:%M') if sdt else ''
                    })

                results.append({
                    'session_id': sess['session_id'],
                    'cashier_id': cashier_id,
                    'cashier_name': sess['full_name'],
                    'employee_code': sess['employee_code'],
                    'profile_photo': sess.get('profile_photo'),
                    'session_date': sess['session_date'].isoformat() if hasattr(sess['session_date'], 'isoformat') else str(sess['session_date']),
                    'start_time': sess['start_time'].isoformat() if hasattr(sess['start_time'], 'isoformat') else str(sess['start_time']),
                    'starting_amount': float(sess['starting_amount']) if sess['starting_amount'] is not None else 0.0,
                    'current_balance': current_balance,
                    'opening_float': opening_float,
                    'cash_ins': cash_ins,
                    'cash_sales': cash_sales,
                    'cash_outs': cash_outs,
                    'safe_drops': safe_drops,
                    'today_transactions': today_transactions,
                    'pending_count': pending_count,
                    'recent_transactions': recent_transactions,
                    'recent_sales': recent_sales,
                    'is_active': True
                })

            # If no active session for some cashiers, include the latest session today as a fallback snapshot
            include_last = True
            if include_last:
                cursor.execute(
                    """
                    SELECT s.id as session_id, s.cashier_id, s.session_date, s.start_time, COALESCE(s.end_time, NOW()) as end_time,
                           s.starting_amount, e.full_name, e.employee_code, e.profile_photo
                    FROM cash_drawer_sessions s
                    JOIN employees e ON e.id = s.cashier_id
                    WHERE s.session_date = CURDATE() AND s.status <> 'active'
                    ORDER BY s.end_time DESC
                    """
                )
                fallback_rows = cursor.fetchall() or []
                for sess in fallback_rows:
                    cashier_id = sess['cashier_id']
                    if cashier_id in active_cashier_ids:
                        continue
                    # summary for fallback session
                    cursor.execute(
                        """
                        SELECT 
                            (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions
                                 WHERE employee_id = %s AND DATE(created_at) = %s AND description = 'Starting cash amount') AS opening_float,
                            (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions
                                 WHERE employee_id = %s AND DATE(created_at) = %s AND transaction_type = 'cash_in' AND description != 'Starting cash amount') AS cash_ins,
                            (SELECT COALESCE(SUM(total_amount), 0) FROM sales
                                 WHERE employee_id = %s AND DATE(sale_date) = %s) AS cash_sales,
                            (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions
                                 WHERE employee_id = %s AND DATE(created_at) = %s AND transaction_type = 'cash_out' AND description NOT LIKE %s AND description NOT LIKE %s) AS cash_outs,
                            (SELECT COALESCE(SUM(amount), 0) FROM cash_drawer_transactions
                                 WHERE employee_id = %s AND DATE(created_at) = %s AND description LIKE %s) AS safe_drops
                        """,
                        (
                            cashier_id, sess['session_date'],
                            cashier_id, sess['session_date'],
                            cashier_id, sess['session_date'],
                            cashier_id, sess['session_date'], 'Safe drop%', 'End shift%',
                            cashier_id, sess['session_date'], 'Safe drop%'
                        )
                    )
                    row = cursor.fetchone() or {}
                    opening_float = float(row.get('opening_float') or 0)
                    cash_ins = float(row.get('cash_ins') or 0)
                    cash_sales = float(row.get('cash_sales') or 0)
                    cash_outs = float(row.get('cash_outs') or 0)
                    safe_drops = float(row.get('safe_drops') or 0)
                    current_balance = opening_float + cash_ins + cash_sales - cash_outs - safe_drops

                    # recent tx in that session window
                    cursor.execute(
                        """
                        SELECT transaction_type, amount, description, status, created_at
                        FROM cash_drawer_transactions
                        WHERE employee_id = %s AND created_at BETWEEN %s AND %s
                        ORDER BY created_at DESC LIMIT 10
                        """,
                        (cashier_id, sess['start_time'], sess['end_time'])
                    )
                    tx_rows = cursor.fetchall() or []
                    recent_transactions = []
                    for r in tx_rows:
                        t_type = r.get('transaction_type')
                        t_amount = float(r.get('amount') or 0)
                        t_desc = r.get('description')
                        t_status = r.get('status')
                        t_time = r.get('created_at')
                        recent_transactions.append({
                            'type': t_type,
                            'amount': t_amount,
                            'description': t_desc,
                            'status': t_status,
                            'created_at': t_time.strftime('%H:%M') if t_time else ''
                        })

                    # recent sales
                    cursor.execute(
                        """
                        SELECT receipt_number, total_amount, sale_date
                        FROM sales
                        WHERE employee_id = %s AND DATE(sale_date) = %s
                        ORDER BY sale_date DESC
                        LIMIT 10
                        """,
                        (cashier_id, sess['session_date'])
                    )
                    sale_rows = cursor.fetchall() or []
                    recent_sales = []
                    for sr in sale_rows:
                        receipt = sr.get('receipt_number')
                        amount = float(sr.get('total_amount') or 0)
                        sdt = sr.get('sale_date')
                        recent_sales.append({
                            'receipt': receipt,
                            'amount': amount,
                            'time': sdt.strftime('%H:%M') if sdt else ''
                        })

                    results.append({
                        'session_id': sess['session_id'],
                        'cashier_id': cashier_id,
                        'cashier_name': sess['full_name'],
                        'employee_code': sess['employee_code'],
                        'profile_photo': sess.get('profile_photo'),
                        'session_date': sess['session_date'].isoformat() if hasattr(sess['session_date'], 'isoformat') else str(sess['session_date']),
                        'start_time': sess['start_time'].isoformat() if hasattr(sess['start_time'], 'isoformat') else str(sess['start_time']),
                        'starting_amount': float(sess['starting_amount']) if sess['starting_amount'] is not None else 0.0,
                        'current_balance': current_balance,
                        'opening_float': opening_float,
                        'cash_ins': cash_ins,
                        'cash_sales': cash_sales,
                        'cash_outs': cash_outs,
                        'safe_drops': safe_drops,
                        'today_transactions': 0,
                        'pending_count': 0,
                        'recent_transactions': recent_transactions,
                        'recent_sales': recent_sales,
                        'is_active': False
                    })

            return jsonify({'success': True, 'sessions': results})
    except Exception as e:
        print(f"Error fetching live sessions: {repr(e)}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching sessions'}), 500
    finally:
        connection.close()

@app.route('/api/admin/cash-drawer/sessions/with-transactions', methods=['GET'])
def admin_sessions_with_transactions():
    """Return recent sessions with all their transactions (optionally filter by date or cashier)."""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    selected_date = request.args.get('date')  # YYYY-MM-DD
    cashier_id = request.args.get('cashier_id')
    session_id = request.args.get('session_id')

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Build sessions query
            where = []
            params = []
            if selected_date:
                where.append("s.session_date = %s")
                params.append(selected_date)
            if cashier_id:
                where.append("s.cashier_id = %s")
                params.append(int(cashier_id))
            if session_id:
                where.append("s.id = %s")
                params.append(int(session_id))
            where_sql = (" WHERE " + " AND ".join(where)) if where else ""

            cursor.execute(f"""
                SELECT s.id as session_id, s.cashier_id, e.full_name, e.employee_code,
                       s.session_date, s.start_time, s.end_time, s.status,
                       s.starting_amount, s.ending_amount
                FROM cash_drawer_sessions s
                JOIN employees e ON e.id = s.cashier_id
                {where_sql}
                ORDER BY s.start_time DESC
                LIMIT 50
            """, tuple(params))
            sessions = cursor.fetchall() or []

            results = []
            for sess in sessions:
                st = sess['start_time']
                et = sess['end_time'] or datetime.now()
                cursor.execute("""
                    SELECT id, transaction_type, amount, description, status, created_at
                    FROM cash_drawer_transactions
                    WHERE employee_id = %s AND created_at BETWEEN %s AND %s
                    ORDER BY created_at ASC
                """, (sess['cashier_id'], st, et))
                txs = cursor.fetchall() or []
                for t in txs:
                    if t.get('created_at'):
                        t['created_at'] = t['created_at'].strftime('%Y-%m-%d %H:%M')
                    t['amount'] = float(t.get('amount') or 0)

                results.append({
                    'session_id': sess['session_id'],
                    'cashier_id': sess['cashier_id'],
                    'cashier_name': sess['full_name'],
                    'employee_code': sess['employee_code'],
                    'session_date': sess['session_date'].strftime('%Y-%m-%d') if hasattr(sess['session_date'], 'strftime') else str(sess['session_date']),
                    'start_time': sess['start_time'].strftime('%Y-%m-%d %H:%M') if hasattr(sess['start_time'], 'strftime') else str(sess['start_time']),
                    'end_time': sess['end_time'].strftime('%Y-%m-%d %H:%M') if (sess.get('end_time') and hasattr(sess['end_time'], 'strftime')) else (str(sess['end_time']) if sess.get('end_time') else None),
                    'status': sess['status'],
                    'starting_amount': float(sess.get('starting_amount') or 0),
                    'ending_amount': float(sess.get('ending_amount') or 0),
                    'transactions': txs
                })

            return jsonify({'success': True, 'sessions': results})
    except Exception as e:
        print(f"Error fetching sessions with transactions: {repr(e)}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching data'}), 500
    finally:
        connection.close()

@app.route('/api/admin/expenses-incurred', methods=['GET'])
def admin_expenses_incurred_api():
    """Return cash outs and safe drops, filterable by date range and cashier."""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    start_date = request.args.get('start_date')  # YYYY-MM-DD
    end_date = request.args.get('end_date')      # YYYY-MM-DD
    cashier_id = request.args.get('cashier_id')
    tx_type = (request.args.get('type') or '').lower()  # 'cash_out' | 'safe_drop' | 'end_shift' | '' (all)

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Base classification: safe drops are recorded as cash_out with description starting 'Safe drop'
            # End shift are recorded as cash_out with description starting 'End shift'
            # We allow filtering by type while keeping a single query where possible
            params = []
            if tx_type == 'cash_out':
                where = ["t.transaction_type = 'cash_out' AND t.description NOT LIKE %s AND t.description NOT LIKE %s"]
                params.append('Safe drop%')
                params.append('End shift%')
            elif tx_type == 'safe_drop':
                where = ["t.description LIKE %s"]
                params.append('Safe drop%')
            elif tx_type == 'end_shift':
                where = ["t.description LIKE %s"]
                params.append('End shift%')
            else:
                where = ["(t.transaction_type = 'cash_out' OR t.description LIKE %s OR t.description LIKE %s)"]
                params.append('Safe drop%')
                params.append('End shift%')
            if start_date:
                where.append("DATE(t.created_at) >= %s")
                params.append(start_date)
            if end_date:
                where.append("DATE(t.created_at) <= %s")
                params.append(end_date)
            if cashier_id:
                where.append("t.employee_id = %s")
                params.append(int(cashier_id))
            where_sql = (" WHERE " + " AND ".join(where)) if where else ""

            cursor.execute(f"""
                SELECT 
                    t.id,
                    t.employee_id,
                    e.full_name AS cashier_name,
                    e.employee_code,
                    t.transaction_type,
                    t.amount,
                    t.description,
                    t.status,
                    t.created_at
                FROM cash_drawer_transactions t
                JOIN employees e ON e.id = t.employee_id
                {where_sql}
                ORDER BY t.created_at DESC
                LIMIT 500
            """, tuple(params))
            rows = cursor.fetchall() or []

            total_cash_out = 0.0
            total_safe_drop = 0.0
            total_end_shift = 0.0
            for r in rows:
                if r.get('created_at'):
                    r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M') if hasattr(r['created_at'], 'strftime') else str(r['created_at'])
                r['amount'] = float(r.get('amount') or 0)
                desc_l = (r.get('description') or '').lower()
                if desc_l.startswith('safe drop'):
                    r['category'] = 'safe_drop'
                elif desc_l.startswith('end shift'):
                    r['category'] = 'end_shift'
                else:
                    r['category'] = 'cash_out'
                if r['category'] == 'cash_out':
                    total_cash_out += r['amount']
                elif r['category'] == 'safe_drop':
                    total_safe_drop += r['amount']
                else:
                    total_end_shift += r['amount']

            return jsonify({
                'success': True,
                'items': rows,
                'totals': {
                    'cash_out': round(total_cash_out, 2),
                    'safe_drop': round(total_safe_drop, 2),
                    'end_shift': round(total_end_shift, 2),
                    'overall': round(total_cash_out + total_safe_drop + total_end_shift, 2)
                }
            })
    except Exception as e:
        print(f"Error fetching expenses incurred: {repr(e)}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching data'}), 500
    finally:
        connection.close()

@app.route('/api/hr/employees/<int:employee_id>', methods=['DELETE'])
def delete_employee(employee_id):
    """Delete employee"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    # Prevent admin from deleting themselves
    if employee_id == session.get('employee_id'):
        return jsonify({'success': False, 'message': 'You cannot delete your own account'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if employee exists
            cursor.execute("SELECT id FROM employees WHERE id = %s", (employee_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Employee not found'}), 404
            
            # Delete employee
            cursor.execute("DELETE FROM employees WHERE id = %s", (employee_id,))
            connection.commit()
            
            return jsonify({'success': True, 'message': 'Employee deleted successfully'})
            
    except Exception as e:
        print(f"Error deleting employee: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while deleting employee'}), 500
    finally:
        connection.close()

@app.route('/api/off-days/calendar/<int:year>/<int:month>', methods=['GET'])
def get_off_days_calendar(year, month):
    """Get off days calendar data for a specific month"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Get all employees
            cursor.execute("""
                SELECT id, full_name, employee_code, role, status
                FROM employees 
                WHERE status IN ('active', 'waiting_approval')
                ORDER BY full_name
            """)
            employees = cursor.fetchall()
            
            # For now, return empty off-days data since we don't have an off_days table yet
            # This will prevent the 404 errors
            off_days_data = {
                'employees': employees,
                'off_days': [],
                'year': year,
                'month': month
            }
            
            return jsonify({'success': True, 'data': off_days_data})
            
    except Exception as e:
        print(f"Error fetching off-days calendar: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching calendar data'}), 500
    finally:
        connection.close()

@app.route('/api/off-days/employee/<int:employee_id>/stats', methods=['GET'])
def get_employee_off_days_stats(employee_id):
    """Get off days statistics for a specific employee"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Get employee details
            cursor.execute("""
                SELECT id, full_name, employee_code, role, status
                FROM employees 
                WHERE id = %s
            """, (employee_id,))
            employee = cursor.fetchone()
            
            if not employee:
                return jsonify({'success': False, 'message': 'Employee not found'}), 404
            
            # For now, return empty stats since we don't have an off_days table yet
            stats = {
                'employee': employee,
                'total_off_days': 0,
                'used_off_days': 0,
                'remaining_off_days': 0,
                'pending_requests': 0
            }
            
            return jsonify({'success': True, 'data': stats})
            
    except Exception as e:
        print(f"Error fetching employee off-days stats: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching employee stats'}), 500
    finally:
        connection.close()
@app.route('/api/hr/stats', methods=['GET'])
def get_hr_stats():
    """Get HR statistics"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500
    
    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            # Get total employees
            cursor.execute("SELECT COUNT(*) as total FROM employees")
            total_employees = cursor.fetchone()['total']
            
            # Get employees by status
            cursor.execute("""
                SELECT status, COUNT(*) as count 
                FROM employees 
                GROUP BY status
            """)
            status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
            
            # Get employees by role
            cursor.execute("""
                SELECT role, COUNT(*) as count 
                FROM employees 
                GROUP BY role
            """)
            role_counts = {row['role']: row['count'] for row in cursor.fetchall()}
            
            return jsonify({
                'success': True,
                'stats': {
                    'total_employees': total_employees,
                    'status_counts': status_counts,
                    'role_counts': role_counts
                }
            })
            
    except Exception as e:
        print(f"Error fetching HR stats: {e}")
        return jsonify({'success': False, 'message': 'An error occurred while fetching statistics'}), 500
    finally:
        connection.close()

# Item Management API Endpoints
@app.route('/api/items', methods=['GET'])
def get_items():
    """Get all items"""
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, name, description, price, category, stock, status, 
                       image_url, sku, stock_update_enabled, created_at, updated_at
                FROM items 
                ORDER BY created_at DESC
            """)
            items = cursor.fetchall()
            
            items_list = []
            for item in items:
                items_list.append({
                    'id': item[0],
                    'name': item[1],
                    'description': item[2],
                    'price': float(item[3]) if item[3] else 0.0,
                    'category': item[4],
                    'stock': item[5] or 0,
                    'status': item[6],
                    'image_url': item[7],
                    'sku': item[8],
                    'stock_update_enabled': bool(item[9]) if item[9] is not None else True,
                    'created_at': item[10].isoformat() if item[10] else None,
                    'updated_at': item[11].isoformat() if item[11] else None
                })
            
            return jsonify({'success': True, 'items': items_list})
            
    except Exception as e:
        print(f"Error fetching items: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch items'})
    finally:
        connection.close()

@app.route('/api/pos/items', methods=['GET'])
def get_pos_items():
    """Get active items for POS system"""
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            # Get only active items for POS
            cursor.execute("""
                SELECT id, name, description, price, category, stock, 
                       image_url, sku, stock_update_enabled
                FROM items 
                WHERE status = 'active'
                ORDER BY category, name
            """)
            items = cursor.fetchall()
            
            items_list = []
            for item in items:
                items_list.append({
                    'id': item[0],
                    'name': item[1],
                    'description': item[2],
                    'price': float(item[3]) if item[3] else 0.0,
                    'category': item[4],
                    'stock': item[5] or 0,
                    'image_url': item[6],
                    'sku': item[7],
                    'stock_update_enabled': bool(item[8]) if item[8] is not None else True
                })
            
            return jsonify({'success': True, 'items': items_list})
            
    except Exception as e:
        print(f"Error fetching POS items: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch items'})
    finally:
        connection.close()

@app.route('/api/pos/process-sale', methods=['POST'])
def process_pos_sale():
    """Process a POS sale and update stock for items with stock tracking enabled"""
    print("[PROCESS] POS Sale processing started...")
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        data = request.get_json()
        order_items = data.get('items', [])
        
        print(f"[ITEMS] Processing {len(order_items)} items")
        
        if not order_items:
            return jsonify({'success': False, 'message': 'No items in order'})
        
        # Get employee info from session
        employee_id = session.get('employee_id')
        employee_name = session.get('employee_name', 'Unknown')
        
        print(f"[EMPLOYEE] Employee: {employee_name} (ID: {employee_id})")
        
        # Calculate totals
        subtotal = sum(item.get('price', 0) * item.get('quantity', 0) for item in order_items)
        tax_amount = data.get('tax_amount', 0)
        total_amount = subtotal + tax_amount
        
        # Generate receipt number
        receipt_number = f"POS{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        with connection.cursor() as cursor:
            # Check if receipt number already exists
            cursor.execute("SELECT id FROM sales WHERE receipt_number = %s", (receipt_number,))
            if cursor.fetchone():
                receipt_number = f"POS{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100, 999)}"
            
            # Insert sale record
            cursor.execute("""
                INSERT INTO sales (
                    receipt_number, employee_id, employee_name, 
                    subtotal, tax_amount, total_amount, tax_included, sale_date, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                receipt_number, employee_id, employee_name, 
                subtotal, tax_amount, total_amount, data.get('tax_included', True), 
                data.get('sale_date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')), 'completed'
            ))
            
            sale_id = cursor.lastrowid
            print(f"[SAVE] Sale record created - ID: {sale_id}, Receipt: {receipt_number}")
            
            # Process each item in the order
            for item in order_items:
                item_id = item.get('id')
                quantity = item.get('quantity', 0)
                price = item.get('price', 0)
                
                print(f"[ITEM] Processing item {item_id}: {quantity}x @ {price}")
                
                if not item_id or quantity <= 0:
                    print(f"[WARN] Skipping invalid item: {item}")
                    continue
                
                # Insert sale item
                cursor.execute("""
                    INSERT INTO sales_items (sale_id, item_id, item_name, quantity, unit_price, total_price)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    sale_id, item_id, item.get('name', ''), quantity, price, price * quantity
                ))
                
                # Check if item exists and get current stock info
                cursor.execute("""
                    SELECT stock, stock_update_enabled, name 
                    FROM items 
                    WHERE id = %s AND status = 'active'
                """, (item_id,))
                
                result = cursor.fetchone()
                if not result:
                    print(f"[WARN] Item {item_id} not found or inactive")
                    continue
                
                # Debug: Let's also check what's in the database for this item
                cursor.execute("""
                    SELECT id, name, stock_update_enabled, status 
                    FROM items 
                    WHERE id = %s
                """, (item_id,))
                debug_result = cursor.fetchone()
                if debug_result:
                    print(f"[DEBUG] Item {item_id}: name='{debug_result[1]}', stock_update_enabled={debug_result[2]}, status='{debug_result[3]}'")
                
                current_stock = result[0] or 0
                stock_update_enabled_raw = result[1]
                stock_update_enabled = stock_update_enabled_raw if stock_update_enabled_raw is not None else True
                item_name = result[2]
                
                print(f"[STOCK] Item: {item_name}")
                print(f"   Current stock: {current_stock}")
                print(f"   Stock tracking raw value: {stock_update_enabled_raw} (type: {type(stock_update_enabled_raw)})")
                print(f"   Stock tracking enabled: {stock_update_enabled}")
                
                # Only update stock if stock tracking is enabled
                if stock_update_enabled:
                    new_stock = current_stock - quantity
                    
                    # Update item stock
                    cursor.execute("""
                        UPDATE items 
                        SET stock = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (new_stock, item_id))
                    
                    print(f"[SUCCESS] Stock updated: {current_stock} -> {new_stock} (sold {quantity})")
                else:
                    print(f"[SKIP] Stock tracking disabled for {item_name}")
                
                # Log stock out transaction (regardless of stock tracking setting)
                cursor.execute("""
                    INSERT INTO stock_transactions 
                    (item_id, action, quantity, price_per_unit, total_amount, 
                     employee_id, employee_name, transaction_type, selling_price, 
                     reason, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """, (
                    item_id, 'stock_out', quantity, price, 
                    price * quantity, employee_id, employee_name, 
                    'sale', price, 'POS Sale'
                ))
                
                print(f"[LOG] Stock transaction logged for {item_name}")
            
            connection.commit()
            print(f"[SUCCESS] POS Sale completed successfully - Receipt: {receipt_number}")
            
            return jsonify({
                'success': True, 
                'message': 'Sale processed successfully',
                'receipt_number': receipt_number,
                'sale_id': sale_id
            })
            
    except Exception as e:
        print(f"[ERROR] Error processing POS sale: {e}")
        connection.rollback()
        return jsonify({'success': False, 'message': 'Failed to process sale'})
    finally:
        connection.close()

@app.route('/api/debug/stock-settings', methods=['GET'])
def debug_stock_settings():
    """Debug endpoint to check stock_update_enabled settings for all items"""
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, name, stock_update_enabled, status 
                FROM items 
                ORDER BY id
            """)
            items = cursor.fetchall()
            
            debug_info = []
            for item in items:
                debug_info.append({
                    'id': item[0],
                    'name': item[1],
                    'stock_update_enabled': item[2],
                    'status': item[3]
                })
            
            return jsonify({
                'success': True,
                'items': debug_info
            })
            
    except Exception as e:
        print(f"Error getting stock settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to get stock settings'})
    finally:
        connection.close()

@app.route('/api/stock-settings', methods=['GET'])
def get_stock_settings():
    """Get stock settings"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            # Get default settings from hotel_settings table
            cursor.execute("""
                SELECT setting_name, setting_value 
                FROM hotel_settings 
                WHERE setting_name IN ('default_low_stock_threshold', 'default_percentage_threshold', 'auto_reorder_enabled')
            """)
            settings_result = cursor.fetchall()
            
            # Convert to dictionary with default values
            settings = {
                'defaultLowStockThreshold': 10,
                'defaultPercentageThreshold': 20,
                'autoReorderEnabled': False
            }
            
            for setting in settings_result:
                if setting[0] == 'default_low_stock_threshold':
                    settings['defaultLowStockThreshold'] = int(setting[1])
                elif setting[0] == 'default_percentage_threshold':
                    settings['defaultPercentageThreshold'] = int(setting[1])
                elif setting[0] == 'auto_reorder_enabled':
                    settings['autoReorderEnabled'] = setting[1].lower() == 'true'
            
            return jsonify({
                'success': True,
                'settings': settings
            })
            
    except Exception as e:
        print(f"Error getting stock settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to get stock settings'})
    finally:
        connection.close()

@app.route('/api/stock-settings', methods=['POST'])
def update_stock_settings():
    """Update stock settings"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    setting_name = data.get('setting_name')
    setting_value = data.get('setting_value')
    
    if not setting_name or setting_value is None:
        return jsonify({'success': False, 'message': 'Setting name and value are required'})
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO hotel_settings (setting_name, setting_value) 
                VALUES (%s, %s) 
                ON DUPLICATE KEY UPDATE setting_value = %s, updated_at = CURRENT_TIMESTAMP
            """, (setting_name, setting_value, setting_value))
            
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': 'Stock setting updated successfully'
            })
            
    except Exception as e:
        print(f"Error updating stock setting: {e}")
        return jsonify({'success': False, 'message': 'Failed to update stock setting'})
    finally:
        connection.close()

@app.route('/api/stock/settings', methods=['GET'])
def get_stock_settings_api():
    """Get stock settings for the frontend"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            # Get default settings
            cursor.execute("""
                SELECT setting_name, setting_value 
                FROM hotel_settings 
                WHERE setting_name IN ('default_low_stock_threshold', 'default_percentage_threshold', 'auto_reorder_enabled')
            """)
            settings_result = cursor.fetchall()
            
            # Convert to dictionary
            settings = {}
            for setting in settings_result:
                settings[setting[0]] = setting[1]
            
            # Set defaults if not found
            default_settings = {
                'defaultLowStockThreshold': int(settings.get('default_low_stock_threshold', 10)),
                'defaultPercentageThreshold': int(settings.get('default_percentage_threshold', 20)),
                'autoReorderEnabled': settings.get('auto_reorder_enabled', 'false').lower() == 'true'
            }
            
            return jsonify({'success': True, 'settings': default_settings})
    
    except Exception as e:
        print(f"Error getting stock settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to get stock settings'})
    finally:
        connection.close()

@app.route('/api/stock/settings', methods=['POST'])
def update_stock_settings_api():
    """Update stock settings for the frontend"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    default_low_stock_threshold = data.get('defaultLowStockThreshold')
    default_percentage_threshold = data.get('defaultPercentageThreshold')
    auto_reorder_enabled = data.get('autoReorderEnabled')
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            # Update or insert settings
            settings_to_update = [
                ('default_low_stock_threshold', str(default_low_stock_threshold)),
                ('default_percentage_threshold', str(default_percentage_threshold)),
                ('auto_reorder_enabled', str(auto_reorder_enabled).lower())
            ]
            
            for setting_name, setting_value in settings_to_update:
                cursor.execute("""
                    INSERT INTO hotel_settings (setting_name, setting_value) 
                    VALUES (%s, %s) 
                    ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value), updated_at = CURRENT_TIMESTAMP
                """, (setting_name, setting_value))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Stock settings updated successfully'})
    
    except Exception as e:
        print(f"Error updating stock settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to update stock settings'})
    finally:
        connection.close()

@app.route('/api/stock/item-threshold', methods=['POST'])
def update_item_threshold():
    """Update low stock threshold for a specific item"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    item_id = data.get('item_id')
    threshold = data.get('threshold')
    
    if not item_id or threshold is None:
        return jsonify({'success': False, 'message': 'Item ID and threshold are required'})
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            # Update item's low stock threshold
            cursor.execute("""
                UPDATE items 
                SET low_stock_threshold = %s, updated_at = CURRENT_TIMESTAMP 
                WHERE id = %s AND status = 'active'
            """, (threshold, item_id))
            
            if cursor.rowcount == 0:
                return jsonify({'success': False, 'message': 'Item not found or not active'})
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Item threshold updated successfully'})
    
    except Exception as e:
        print(f"Error updating item threshold: {e}")
        return jsonify({'success': False, 'message': 'Failed to update item threshold'})
    finally:
        connection.close()

@app.route('/api/stock-analytics/enhanced', methods=['GET'])
def get_enhanced_stock_analytics():
    """Get enhanced stock analytics with detailed insights"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            # Get all items with stock levels
            cursor.execute("""
                SELECT 
                    i.id, i.name, i.category, i.stock, i.stock_update_enabled, 
                    i.low_stock_threshold, i.status,
                    COALESCE(i.stock, 0) as current_stock,
                    CASE 
                        WHEN i.stock_update_enabled = FALSE THEN 'No Tracking'
                        WHEN i.stock = 0 OR i.stock IS NULL THEN 'Out of Stock'
                        WHEN i.stock <= COALESCE(i.low_stock_threshold, 10) THEN 'Low Stock'
                        ELSE 'Good Stock'
                    END as stock_status
                FROM items i
                WHERE i.status = 'active'
                ORDER BY i.name
            """)
            all_items = cursor.fetchall()
            
            # Get stock usage analytics (last 30 days)
            cursor.execute("""
                SELECT 
                    st.item_id,
                    i.name,
                    SUM(CASE WHEN st.action = 'stock_out' THEN st.quantity ELSE 0 END) as total_sold,
                    SUM(CASE WHEN st.action = 'stock_in' THEN st.quantity ELSE 0 END) as total_received,
                    COUNT(DISTINCT DATE(st.created_at)) as days_with_activity
                FROM stock_transactions st
                JOIN items i ON st.item_id = i.id
                WHERE st.created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                AND i.status = 'active'
                GROUP BY st.item_id, i.name
                ORDER BY total_sold DESC
            """)
            usage_analytics = cursor.fetchall()
            
            # Get most used items
            cursor.execute("""
                SELECT 
                    st.item_id,
                    i.name,
                    SUM(st.quantity) as total_quantity,
                    COUNT(*) as transaction_count,
                    AVG(st.price_per_unit) as avg_price
                FROM stock_transactions st
                JOIN items i ON st.item_id = i.id
                WHERE st.action = 'stock_out' 
                AND st.created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                AND i.status = 'active'
                GROUP BY st.item_id, i.name
                ORDER BY total_quantity DESC
                LIMIT 10
            """)
            most_used_items = cursor.fetchall()
            
            # Get low stock alerts
            cursor.execute("""
                SELECT 
                    i.id, i.name, i.stock, i.low_stock_threshold,
                    COALESCE(i.stock, 0) as current_stock,
                    COALESCE(i.low_stock_threshold, 10) as threshold
                FROM items i
                WHERE i.status = 'active' 
                AND i.stock_update_enabled = TRUE
                AND (i.stock IS NULL OR i.stock <= COALESCE(i.low_stock_threshold, 10))
                ORDER BY i.stock ASC
            """)
            low_stock_alerts = cursor.fetchall()
            
            # Get reorder recommendations
            cursor.execute("""
                SELECT 
                    i.id, i.name, i.stock, i.low_stock_threshold,
                    COALESCE(usage.total_sold, 0) as avg_usage_30_days,
                    CASE 
                        WHEN COALESCE(usage.total_sold, 0) = 0 THEN COALESCE(i.low_stock_threshold, 10) * 2
                        ELSE GREATEST(COALESCE(usage.total_sold, 0) * 1.5, COALESCE(i.low_stock_threshold, 10) * 2)
                    END as recommended_order_qty
                FROM items i
                LEFT JOIN (
                    SELECT 
                        item_id,
                        SUM(quantity) as total_sold
                    FROM stock_transactions 
                    WHERE action = 'stock_out' 
                    AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                    GROUP BY item_id
                ) usage ON i.id = usage.item_id
                WHERE i.status = 'active' 
                AND i.stock_update_enabled = TRUE
                AND (i.stock IS NULL OR i.stock <= COALESCE(i.low_stock_threshold, 10))
                ORDER BY recommended_order_qty DESC
            """)
            reorder_recommendations = cursor.fetchall()
            
            # Process data
            items_data = []
            for item in all_items:
                items_data.append({
                    'id': item[0],
                    'name': item[1],
                    'category': item[2],
                    'stock': item[3],
                    'stock_update_enabled': item[4],
                    'low_stock_threshold': item[5],
                    'status': item[6],
                    'current_stock': item[7],
                    'stock_status': item[8]
                })
            
            usage_data = []
            for usage in usage_analytics:
                usage_data.append({
                    'item_id': usage[0],
                    'name': usage[1],
                    'total_sold': usage[2],
                    'total_received': usage[3],
                    'days_with_activity': usage[4]
                })
            
            most_used_data = []
            for item in most_used_items:
                most_used_data.append({
                    'item_id': item[0],
                    'name': item[1],
                    'total_quantity': item[2],
                    'transaction_count': item[3],
                    'avg_price': float(item[4]) if item[4] else 0
                })
            
            low_stock_data = []
            for item in low_stock_alerts:
                low_stock_data.append({
                    'id': item[0],
                    'name': item[1],
                    'stock': item[2],
                    'threshold': item[3],
                    'current_stock': item[4],
                    'threshold_value': item[5]
                })
            
            reorder_data = []
            for item in reorder_recommendations:
                reorder_data.append({
                    'id': item[0],
                    'name': item[1],
                    'current_stock': item[2],
                    'threshold': item[3],
                    'avg_usage_30_days': item[4],
                    'recommended_order_qty': int(item[5])
                })
            
            return jsonify({
                'success': True,
                'data': {
                    'all_items': items_data,
                    'usage_analytics': usage_data,
                    'most_used_items': most_used_data,
                    'low_stock_alerts': low_stock_data,
                    'reorder_recommendations': reorder_data
                }
            })
            
    except Exception as e:
        print(f"Error getting enhanced stock analytics: {e}")
        return jsonify({'success': False, 'message': 'Failed to get stock analytics'})
    finally:
        connection.close()

@app.route('/api/items', methods=['POST'])
def create_item():
    """Create a new item"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        # Get form data
        name = request.form.get('name', '').strip().upper()
        description = request.form.get('description', '').strip().upper()
        price = float(request.form.get('price', 0))
        category = request.form.get('category', '').strip()
        
        # Validate required fields
        if not name or not description or not category or price <= 0:
            return jsonify({'success': False, 'message': 'All fields are required and price must be greater than 0'})
        
        # Handle image upload
        image_url = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                # Save image to static/uploads directory
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                filename = timestamp + filename
                file_path = os.path.join('static', 'uploads', filename)
                file.save(file_path)
                image_url = f'/static/uploads/{filename}'
        
        # Generate SKU
        sku = f"{category[:3].upper()}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO items (name, description, price, category, stock, status, image_url, sku)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (name, description, price, category, 0, 'active', image_url, sku))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Item created successfully'})
            
    except Exception as e:
        print(f"Error creating item: {e}")
        return jsonify({'success': False, 'message': 'Failed to create item'})
    finally:
        connection.close()

@app.route('/api/items/<int:item_id>', methods=['GET'])
def get_item(item_id):
    """Get a specific item by ID"""
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, name, description, price, category, stock, status, 
                       image_url, sku, stock_update_enabled, created_at, updated_at
                FROM items 
                WHERE id = %s
            """, (item_id,))
            item = cursor.fetchone()
            
            if item:
                item_data = {
                    'id': item[0],
                    'name': item[1],
                    'description': item[2],
                    'price': float(item[3]) if item[3] else 0.0,
                    'category': item[4],
                    'stock': item[5] or 0,
                    'status': item[6],
                    'image_url': item[7],
                    'sku': item[8],
                    'stock_update_enabled': bool(item[9]) if item[9] is not None else True,
                    'created_at': item[10].isoformat() if item[10] else None,
                    'updated_at': item[11].isoformat() if item[11] else None
                }
                return jsonify({'success': True, 'item': item_data})
            else:
                return jsonify({'success': False, 'message': 'Item not found'})
            
    except Exception as e:
        print(f"Error fetching item: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch item'})
    finally:
        connection.close()
@app.route('/api/items/<int:item_id>', methods=['PUT'])
def update_item(item_id):
    """Update an existing item"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        # Get form data
        name = request.form.get('name', '').strip().upper()
        description = request.form.get('description', '').strip().upper()
        price = float(request.form.get('price', 0))
        category = request.form.get('category', '').strip().upper()
        
        # Validate required fields
        if not name or not description or not category or price <= 0:
            return jsonify({'success': False, 'message': 'All fields are required and price must be greater than 0'})
        
        # Handle image upload
        image_url = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                # Save image to static/uploads directory
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                filename = timestamp + filename
                file_path = os.path.join('static', 'uploads', filename)
                file.save(file_path)
                image_url = f'/static/uploads/{filename}'
        
        with connection.cursor() as cursor:
            # Check if item exists
            cursor.execute("SELECT id FROM items WHERE id = %s", (item_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Item not found'})
            
            # Update item
            if image_url:
                cursor.execute("""
                    UPDATE items 
                    SET name = %s, description = %s, price = %s, category = %s, 
                        image_url = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (name, description, price, category, image_url, item_id))
            else:
                cursor.execute("""
                    UPDATE items 
                    SET name = %s, description = %s, price = %s, category = %s, 
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (name, description, price, category, item_id))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Item updated successfully'})
            
    except Exception as e:
        print(f"Error updating item: {e}")
        return jsonify({'success': False, 'message': f'Failed to update item: {str(e)}'})
    finally:
        connection.close()

@app.route('/api/items/<int:item_id>', methods=['DELETE'])
def delete_item(item_id):
    """Delete an item"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        with connection.cursor() as cursor:
            # Check if item exists
            cursor.execute("SELECT id FROM items WHERE id = %s", (item_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Item not found'})
            
            # Delete item
            cursor.execute("DELETE FROM items WHERE id = %s", (item_id,))
            connection.commit()
            return jsonify({'success': True, 'message': 'Item deleted successfully'})
            
    except Exception as e:
        print(f"Error deleting item: {e}")
        return jsonify({'success': False, 'message': 'Failed to delete item'})
    finally:
        connection.close()

@app.route('/api/items/<int:item_id>/status', methods=['PUT'])
def update_item_status(item_id):
    """Update item status (active/inactive)"""
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        data = request.get_json()
        status = data.get('status', '').strip().lower()
        
        if status not in ['active', 'inactive']:
            return jsonify({'success': False, 'message': 'Invalid status. Must be active or inactive'})
        
        with connection.cursor() as cursor:
            # Check if item exists
            cursor.execute("SELECT id FROM items WHERE id = %s", (item_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Item not found'})
            
            # Update status
            cursor.execute("""
                UPDATE items 
                SET status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (status, item_id))
            
            connection.commit()
            return jsonify({'success': True, 'message': f'Item {status} successfully'})
            
    except Exception as e:
        print(f"Error updating item status: {e}")
        return jsonify({'success': False, 'message': 'Failed to update item status'})
    finally:
        connection.close()

@app.route('/api/items/<int:item_id>/stock-toggle', methods=['PUT'])
def toggle_stock_update(item_id):
    """Toggle stock update setting for an item"""
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        data = request.get_json()
        stock_update_enabled = data.get('stock_update_enabled')
        
        if stock_update_enabled is None:
            return jsonify({'success': False, 'message': 'Invalid stock update setting'})
        
        with connection.cursor() as cursor:
            # Check if item exists
            cursor.execute("SELECT id FROM items WHERE id = %s", (item_id,))
            if not cursor.fetchone():
                return jsonify({'success': False, 'message': 'Item not found'})
            
            cursor.execute("""
                UPDATE items 
                SET stock_update_enabled = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (stock_update_enabled, item_id))
            connection.commit()
            
            status_text = "enabled" if stock_update_enabled else "disabled"
            return jsonify({'success': True, 'message': f'Stock update tracking {status_text}'})
            
    except Exception as e:
        print(f"Error toggling stock update: {e}")
        return jsonify({'success': False, 'message': 'Failed to update stock tracking setting'})
    finally:
        connection.close()

@app.route('/api/items/<int:item_id>/stock', methods=['POST'])
def update_item_stock(item_id):
    """Update item stock (stock in/out) with detailed transaction information"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'})
    
    try:
        # Get form data
        action = request.form.get('action', '').strip()
        quantity = int(request.form.get('quantity', 0))
        reason = request.form.get('reason', '').strip().upper()
        
        # Get employee information from session
        employee_id = session.get('employee_id')
        employee_name = session.get('employee_name', 'Unknown')
        
        if action not in ['stock_in', 'stock_out'] or quantity <= 0:
            return jsonify({'success': False, 'message': 'Invalid action or quantity'})
        
        with connection.cursor() as cursor:
            # Get current stock and stock update setting
            cursor.execute("SELECT stock, stock_update_enabled FROM items WHERE id = %s", (item_id,))
            result = cursor.fetchone()
            if not result:
                return jsonify({'success': False, 'message': 'Item not found'})
            
            current_stock = result[0] or 0
            stock_update_enabled = result[1] if result[1] is not None else True
            
            # Calculate new stock only if stock update is enabled
            new_stock = current_stock
            if stock_update_enabled:
                if action == 'stock_in':
                    new_stock = current_stock + quantity
                else:  # stock_out
                    new_stock = current_stock - quantity
                    if new_stock < 0:
                        return jsonify({'success': False, 'message': 'Insufficient stock'})
                
                # Update stock only if stock update is enabled
                cursor.execute("""
                    UPDATE items 
                    SET stock = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (new_stock, item_id))
            
            # Prepare transaction data based on action
            if action == 'stock_in':
                # Stock In fields
                price_per_unit = float(request.form.get('price_per_unit', 0))
                total_amount = price_per_unit * quantity
                place_purchased_from = request.form.get('place_purchased_from', '').strip().upper()
                transaction_type = 'purchase'
                selling_price = None
                refund_issued = None
            else:
                # Stock Out fields
                price_per_unit = None
                total_amount = None
                place_purchased_from = None
                transaction_type = request.form.get('transaction_type', 'sale')  # sale, return, waste
                selling_price = float(request.form.get('selling_price', 0)) if request.form.get('selling_price') else None
                refund_issued = request.form.get('refund_issued') == 'true' if transaction_type == 'return' else None
            
            # Log detailed transaction
            cursor.execute("""
                INSERT INTO stock_transactions (
                    item_id, action, quantity, price_per_unit, total_amount, 
                    place_purchased_from, employee_id, employee_name, 
                    transaction_type, selling_price, refund_issued, reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                item_id, action, quantity, price_per_unit, total_amount,
                place_purchased_from, employee_id, employee_name,
                transaction_type, selling_price, refund_issued, reason
            ))
            
            connection.commit()
            
            if stock_update_enabled:
                return jsonify({'success': True, 'message': f'Stock updated successfully. New stock: {new_stock}'})
            else:
                return jsonify({'success': True, 'message': f'Transaction logged successfully. Stock tracking is disabled for this item.'})
            
    except Exception as e:
        print(f"Error updating stock: {e}")
        return jsonify({'success': False, 'message': 'Failed to update stock'})
    finally:
        connection.close()

def create_sample_data():
    """Create sample data for demonstration"""
    connection = get_db_connection()
    if not connection:
        return
    
    try:
        with connection.cursor() as cursor:
            # Admin user is already created in init_database(), skip here
            print("Admin user already created in init_database(), skipping creation in sample data")
            
            # Create sample employees
            sample_employees = [
                    ('John Doe', 'john@hotel.com', '1234567891', '0002', 'employee'),
                    ('Jane Smith', 'jane@hotel.com', '1234567892', '0003', 'cashier'),
                    ('Mike Johnson', 'mike@hotel.com', '1234567893', '0004', 'manager'),
                    ('Sarah Wilson', 'sarah@hotel.com', '1234567894', '0005', 'employee')
            ]
            
            for name, email, phone, code, role in sample_employees:
                cursor.execute("""
                    INSERT INTO employees (full_name, email, phone_number, employee_code, password_hash, role, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'active')
                """, (name, email, phone, code, hash_password('password123'), role))
            
            connection.commit()
            print("Sample data created successfully")
                
    except Exception as e:
        print(f"Error creating sample data: {e}")
    finally:
        connection.close()

@app.route('/api/sales', methods=['POST'])
def save_sale_to_database():
    """Save sale data to database before printing receipt"""
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        receipt_number = data.get('receipt_number')
        employee_id = data.get('employee_id')
        employee_name = data.get('employee_name')
        items = data.get('items', [])
        subtotal = data.get('subtotal', 0)
        tax_amount = data.get('tax_amount', 0)
        total_amount = data.get('total_amount', 0)
        tax_included = data.get('tax_included', True)
        
        if not all([receipt_number, employee_id, employee_name, items]):
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        try:
            with connection.cursor() as cursor:
                # Check for duplicate receipt number
                cursor.execute("SELECT id FROM sales WHERE receipt_number = %s", (receipt_number,))
                if cursor.fetchone():
                    return jsonify({'success': False, 'message': 'Receipt number already exists'}), 400
                
                # Insert sale record (without employee_code for confidentiality)
                cursor.execute("""
                    INSERT INTO sales (receipt_number, employee_id, employee_name, 
                                     subtotal, tax_amount, total_amount, tax_included, sale_date, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (receipt_number, employee_id, employee_name, subtotal, tax_amount, total_amount, tax_included, data.get('sale_date', datetime.now().strftime('%Y-%m-%d %H:%M:%S')), 'pending'))
                
                sale_id = cursor.lastrowid
                
                # Insert sale items and update stock
                for item in items:
                    item_id = item.get('id')
                    quantity = item.get('quantity', 0)
                    
                    # Insert sale item
                    cursor.execute("""
                        INSERT INTO sales_items (sale_id, item_id, item_name, quantity, unit_price, total_price)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        sale_id,
                        item_id,
                        item.get('name'),
                        quantity,
                        item.get('price'),
                        quantity * item.get('price', 0)
                    ))
                    
                    # Update stock if stock tracking is enabled
                    cursor.execute("""
                        SELECT stock, stock_update_enabled, name 
                        FROM items 
                        WHERE id = %s AND status = 'active'
                    """, (item_id,))
                    
                    result = cursor.fetchone()
                    if result:
                        current_stock = result[0] or 0
                        stock_update_enabled = result[1] if result[1] is not None else True
                        item_name = result[2]
                        
                        # Only update stock if stock tracking is enabled
                        if stock_update_enabled:
                            new_stock = current_stock - quantity
                            
                            # Update item stock
                            cursor.execute("""
                                UPDATE items 
                                SET stock = %s, updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                            """, (new_stock, item_id))
                            
                            print(f"Updated stock for {item_name}: {current_stock} -> {new_stock} (sold {quantity})")
                        
                        # Log stock out transaction
                        cursor.execute("""
                            INSERT INTO stock_transactions 
                            (item_id, action, quantity, price_per_unit, total_amount, 
                             employee_id, employee_name, transaction_type, selling_price, 
                             reason, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        """, (
                            item_id, 'stock_out', quantity, item.get('price', 0), 
                            quantity * item.get('price', 0), employee_id, employee_name, 
                            'sale', item.get('price', 0), f'Sale - Receipt {receipt_number}'
                        ))
                
                connection.commit()
                
                print(f"[SUCCESS] Sale saved successfully - Receipt: {receipt_number}, Sale ID: {sale_id}")
                print(f"   Items sold: {len(items)}")
                print(f"   Total amount: {total_amount}")
                
                return jsonify({
                    'success': True,
                    'message': 'Sale saved successfully',
                    'sale_id': sale_id,
                    'receipt_number': receipt_number
                })
                
        except Exception as e:
            connection.rollback()
            print(f"Error saving sale: {e}")
            return jsonify({'success': False, 'message': 'Error saving sale'}), 500
        finally:
            connection.close()
            
    except Exception as e:
        print(f"Error processing sale request: {e}")
        return jsonify({'success': False, 'message': 'Error processing request'}), 500

@app.route('/api/receipt/next-number', methods=['GET'])
def get_next_receipt_number_from_db():
    """Get the next receipt number from database"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        cursor = connection.cursor()
        
        # Get the next receipt number (starting from 1001 if no sales exist)
        cursor.execute("""
            SELECT COALESCE(MAX(CAST(receipt_number AS UNSIGNED)), 1000) + 1 as next_receipt_number
            FROM sales
        """)
        
        result = cursor.fetchone()
        next_receipt_number = result[0] if result else 1001
        
        return jsonify({
            'success': True,
            'receipt_number': str(next_receipt_number).zfill(4)
        })
        
    except Exception as e:
        print(f"Error getting next receipt number: {e}")
        return jsonify({'success': False, 'message': 'Error getting receipt number'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/receipts', methods=['GET'])
def get_receipts():
    """Get list of all printed receipts for reprinting with optional date filter"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get filters from query parameters
        date_filter = request.args.get('date')
        status_filter = request.args.get('status')
        
        # Build the query with optional filters
        where_conditions = []
        params = []
        
        if date_filter:
            where_conditions.append("DATE(s.sale_date) = %s")
            params.append(date_filter)
        
        if status_filter == 'confirmed':
            where_conditions.append("s.cashier_confirmed = 1")
        elif status_filter == 'unconfirmed':
            where_conditions.append("(s.cashier_confirmed = 0 OR s.cashier_confirmed IS NULL)")
        
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)
        
        query = f"""
            SELECT 
                s.id,
                s.receipt_number,
                s.employee_name,
                s.subtotal,
                s.tax_amount,
                s.total_amount,
                s.sale_date,
                s.status,
                COALESCE(s.cashier_confirmed, 0) as cashier_confirmed,
                COUNT(si.id) as item_count
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            {where_clause}
            GROUP BY s.id
            ORDER BY s.sale_date DESC
            LIMIT 100
        """
        
        cursor.execute(query, params)
        
        receipts = cursor.fetchall()
        
        # Convert datetime objects to strings for JSON serialization
        for receipt in receipts:
            if receipt['sale_date']:
                receipt['sale_date'] = receipt['sale_date'].isoformat()
        
        return jsonify({
            'success': True,
            'receipts': receipts
        })
        
    except Exception as e:
        print(f"Error fetching receipts: {e}")
        return jsonify({'success': False, 'message': 'Error fetching receipts'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/receipts/<int:receipt_id>', methods=['GET'])
def get_receipt_details(receipt_id):
    """Get detailed receipt information including items for reprinting"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get receipt details (excluding employee_code for confidentiality)
        cursor.execute("""
            SELECT 
                s.id,
                s.receipt_number,
                s.employee_name,
                s.subtotal,
                s.tax_amount,
                s.total_amount,
                s.sale_date,
                s.tax_included
            FROM sales s
            WHERE s.id = %s
        """, (receipt_id,))
        
        receipt = cursor.fetchone()
        if not receipt:
            return jsonify({'success': False, 'message': 'Receipt not found'}), 404
        
        # Get receipt items
        cursor.execute("""
            SELECT 
                si.item_name,
                si.quantity,
                si.unit_price,
                si.total_price
            FROM sales_items si
            WHERE si.sale_id = %s
            ORDER BY si.id
        """, (receipt_id,))
        
        items = cursor.fetchall()
        
        # Convert datetime to string
        if receipt['sale_date']:
            receipt['sale_date'] = receipt['sale_date'].isoformat()
        
        return jsonify({
            'success': True,
            'receipt': receipt,
            'items': items
        })
        
    except Exception as e:
        print(f"Error fetching receipt details: {e}")
        return jsonify({'success': False, 'message': 'Error fetching receipt details'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/receipt/<int:receipt_id>')
def view_receipt_qr(receipt_id):
    """Public endpoint to view receipt details via QR code"""
    try:
        connection = get_db_connection()
        if not connection:
            return render_template('receipt_view.html', error='Database connection failed')
        
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # Get receipt details
        cursor.execute("""
            SELECT 
                s.id,
                s.receipt_number,
                s.employee_name,
                s.subtotal,
                s.tax_amount,
                s.total_amount,
                s.sale_date,
                s.tax_included
            FROM sales s
            WHERE s.id = %s
        """, (receipt_id,))
        
        receipt = cursor.fetchone()
        if not receipt:
            return render_template('receipt_view.html', error='Receipt not found')
        
        # Get receipt items
        cursor.execute("""
            SELECT 
                si.item_name,
                si.quantity,
                si.unit_price,
                si.total_price
            FROM sales_items si
            WHERE si.sale_id = %s
            ORDER BY si.id
        """, (receipt_id,))
        
        items = cursor.fetchall()
        
        # Get hotel settings for display
        cursor.execute("""
            SELECT hotel_name, company_phone, company_email, hotel_address
            FROM hotel_settings 
            ORDER BY id DESC 
            LIMIT 1
        """)
        hotel_settings = cursor.fetchone() or {}
        
        # Get receipt settings
        cursor.execute("""
            SELECT receipt_header_title, receipt_header_subtitle, receipt_footer_message,
                   receipt_show_address, receipt_show_contact, receipt_address, 
                   receipt_phone, receipt_email
            FROM hotel_settings 
            ORDER BY id DESC 
            LIMIT 1
        """)
        receipt_settings = cursor.fetchone() or {}
        
        return render_template('receipt_view.html', 
                             receipt=receipt, 
                             items=items,
                             hotel_settings=hotel_settings,
                             receipt_settings=receipt_settings)
        
    except Exception as e:
        print(f"Error fetching receipt for QR view: {e}")
        return render_template('receipt_view.html', error='Error loading receipt')
    finally:
        if connection:
            connection.close()

@app.route('/api/receipts/<int:receipt_id>/reprint', methods=['POST'])
def reprint_receipt(receipt_id):
    """Log a receipt reprint action"""
    try:
        data = request.get_json()
        employee_code = data.get('employee_code')
        
        if not employee_code:
            return jsonify({'success': False, 'message': 'Employee code required'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        cursor = connection.cursor()
        
        # Verify employee exists and is active
        cursor.execute("""
            SELECT id, full_name, employee_code 
            FROM employees 
            WHERE employee_code = %s AND status = 'active'
        """, (employee_code,))
        
        employee = cursor.fetchone()
        if not employee:
            return jsonify({'success': False, 'message': 'Invalid employee code'}), 400
        
        # Verify receipt exists
        cursor.execute("SELECT id, receipt_number FROM sales WHERE id = %s", (receipt_id,))
        receipt = cursor.fetchone()
        if not receipt:
            return jsonify({'success': False, 'message': 'Receipt not found'}), 404
        
        # Log the reprint action to the sales table
        cursor.execute("""
            UPDATE sales 
            SET status = 'reprinted', 
                updated_at = NOW() 
            WHERE id = %s
        """, (receipt_id,))
        
        connection.commit()
        
        # Log the reprint action
        print(f"Receipt #{receipt[1]} reprinted by employee {employee[1]} ({employee[2]})")
        
        return jsonify({
            'success': True,
            'message': f'Receipt #{receipt[1]} reprinted successfully',
            'employee': {
                'id': employee[0],
                'name': employee[1],
                'employee_code': employee[2]
            }
        })
        
    except Exception as e:
        print(f"Error logging reprint: {e}")
        return jsonify({'success': False, 'message': 'Error logging reprint'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/receipts/<int:receipt_id>/confirm', methods=['POST'])
def toggle_cashier_confirmation(receipt_id):
    """Toggle cashier confirmation for a receipt"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        cursor = connection.cursor()
        
        # First, check if the receipt exists and get current confirmation status
        cursor.execute("""
            SELECT id, cashier_confirmed, receipt_number, status
            FROM sales 
            WHERE id = %s
        """, (receipt_id,))
        
        receipt = cursor.fetchone()
        if not receipt:
            return jsonify({'success': False, 'message': 'Receipt not found'}), 404
        
        # Get permissions setting
        cursor.execute("""
            SELECT enable_receipt_status_update
            FROM hotel_settings 
            ORDER BY id DESC 
            LIMIT 1
        """)
        settings_result = cursor.fetchone()
        enable_status_update = settings_result[0] if settings_result else True
        
        # Toggle the confirmation status
        new_status = 1 if receipt[1] == 0 else 0
        
        # Update cashier confirmation
        cursor.execute("""
            UPDATE sales 
            SET cashier_confirmed = %s
            WHERE id = %s
        """, (new_status, receipt_id))
        
        # If enabled and confirming (new_status = 1), also update receipt status to 'confirmed'
        if enable_status_update and new_status == 1:
            cursor.execute("""
                UPDATE sales 
                SET status = 'confirmed'
                WHERE id = %s
            """, (receipt_id,))
        
        connection.commit()
        
        status_text = "confirmed" if new_status == 1 else "unconfirmed"
        
        return jsonify({
            'success': True,
            'message': f'Receipt #{receipt[2]} {status_text} by cashier',
            'cashier_confirmed': new_status,
            'status_updated': enable_status_update and new_status == 1
        })
        
    except Exception as e:
        print(f"Error toggling cashier confirmation: {e}")
        return jsonify({'success': False, 'message': 'Error updating confirmation'}), 500
    finally:
        if connection:
            connection.close()
@app.route('/api/receipts/update-status', methods=['POST'])
def update_receipt_status():
    """Update status of multiple receipts and handle stock accordingly"""
    try:
        data = request.get_json()
        receipt_ids = data.get('receipt_ids', [])
        status = data.get('status', '')
        
        if not receipt_ids:
            return jsonify({'success': False, 'message': 'No receipt IDs provided'}), 400
        
        if status not in ['pending', 'confirmed', 'cancelled']:
            return jsonify({'success': False, 'message': 'Invalid status'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        cursor = connection.cursor()
        
        # Get employee information for logging
        employee_id = session.get('employee_id')
        employee_name = session.get('employee_name', 'Unknown')
        
        # Process each receipt individually to handle stock properly
        processed_receipts = []
        stock_updates_count = 0
        
        for receipt_id in receipt_ids:
            try:
                # Get current receipt status and items
                cursor.execute("""
                    SELECT s.id, s.receipt_number, s.status, s.employee_name
                    FROM sales s 
                    WHERE s.id = %s
                """, (receipt_id,))
                
                receipt = cursor.fetchone()
                if not receipt:
                    continue
                
                current_status = receipt[2]
                receipt_number = receipt[1]
                
                # Get receipt items
                cursor.execute("""
                    SELECT si.item_id, si.quantity, si.price, i.name, i.stock, i.stock_update_enabled
                    FROM sales_items si
                    JOIN items i ON si.item_id = i.id
                    WHERE si.sale_id = %s
                """, (receipt_id,))
                
                receipt_items = cursor.fetchall()
                
                # Handle stock updates based on status change
                if status == 'cancelled' and current_status != 'cancelled':
                    # Restore stock for cancelled receipts
                    for item in receipt_items:
                        item_id, quantity, price, item_name, current_stock, stock_enabled = item
                        
                        if stock_enabled is None or stock_enabled:  # Default to True if None
                            # Restore stock
                            new_stock = current_stock + quantity
                            cursor.execute("""
                                UPDATE items 
                                SET stock = %s, updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                            """, (new_stock, item_id))
                            
                            # Log stock in transaction for cancellation
                            cursor.execute("""
                                INSERT INTO stock_transactions 
                                (item_id, action, quantity, price_per_unit, total_amount, 
                                 employee_id, employee_name, transaction_type, selling_price, 
                                 reason, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                            """, (
                                item_id, 'stock_in', quantity, price, 
                                price * quantity, employee_id, employee_name, 
                                'cancellation', price, f'Receipt Cancellation - Receipt #{receipt_number}'
                            ))
                            
                            stock_updates_count += 1
                            print(f"[CANCELLATION] Restored stock for {item_name}: {current_stock} -> {new_stock} (+{quantity})")
                
                elif status == 'confirmed' and current_status != 'confirmed':
                    # Only deduct stock if the receipt was previously cancelled
                    # (because stock was already deducted during the original sale)
                    if current_status == 'cancelled':
                        # Deduct stock for previously cancelled receipts
                        for item in receipt_items:
                            item_id, quantity, price, item_name, current_stock, stock_enabled = item
                            
                            if stock_enabled is None or stock_enabled:  # Default to True if None
                                # Deduct stock
                                new_stock = current_stock - quantity
                                cursor.execute("""
                                    UPDATE items 
                                    SET stock = %s, updated_at = CURRENT_TIMESTAMP
                                    WHERE id = %s
                                """, (new_stock, item_id))
                                
                                # Log stock out transaction for confirmation
                                cursor.execute("""
                                    INSERT INTO stock_transactions 
                                    (item_id, action, quantity, price_per_unit, total_amount, 
                                     employee_id, employee_name, transaction_type, selling_price, 
                                     reason, created_at)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                                """, (
                                    item_id, 'stock_out', quantity, price, 
                                    price * quantity, employee_id, employee_name, 
                                    'sale', price, f'Receipt Confirmation - Receipt #{receipt_number}'
                                ))
                                
                                stock_updates_count += 1
                                print(f"[CONFIRMATION] Deducted stock for {item_name}: {current_stock} -> {new_stock} (-{quantity})")
                    else:
                        # For pending receipts, stock was already deducted during sale
                        print(f"[CONFIRMATION] Receipt #{receipt_number} confirmed - no stock changes needed (was {current_status})")
                
                # Update receipt status
                cursor.execute("UPDATE sales SET status = %s WHERE id = %s", (status, receipt_id))
                processed_receipts.append(receipt_id)
                
            except Exception as e:
                print(f"Error processing receipt {receipt_id}: {e}")
                continue
        
        connection.commit()
        
        # Prepare response message
        status_text = status.title()
        message = f'Successfully updated {len(processed_receipts)} receipt(s) status to {status_text}'
        if stock_updates_count > 0:
            message += f' and processed {stock_updates_count} stock updates'
        
        return jsonify({
            'success': True, 
            'message': message,
            'updated_count': len(processed_receipts),
            'stock_updates': stock_updates_count
        })
        
    except Exception as e:
        print(f"Error updating receipt status: {e}")
        return jsonify({'success': False, 'message': 'Error updating receipt status'}), 500
    finally:
        if connection:
            connection.close()

# Analytics Routes
@app.route('/analytics')
def analytics():
    """Main analytics dashboard"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics.html', 
                         employee_name=session.get('employee_name'), 
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/analytics/sales')
def analytics_sales():
    """Sales analytics page - Admin and Manager access"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics_sales.html', 
                         employee_name=session.get('employee_name'), 
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/analytics/items')
def analytics_items():
    """Item analytics page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics_items.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/analytics/stock')
def analytics_stock():
    """Stock analytics overview page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics_stock.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/analytics/stock/inventory')
def analytics_stock_inventory():
    """Stock inventory management page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics_stock_inventory.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/analytics/stock/charts')
def analytics_stock_charts():
    """Stock charts analytics page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics_stock_charts.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/analytics/stock/reports')
def analytics_stock_reports():
    """Stock reports analytics page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics_stock_reports.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/analytics/stock/recommendations')
def analytics_stock_recommendations():
    """Stock recommendations analytics page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics_stock_recommendations.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/analytics/employees')
def analytics_employees():
    """Employee analytics page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics_employees.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/analytics/periods')
def analytics_periods():
    """Period analytics page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    hotel_settings = get_hotel_settings()
    employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
    return render_template('analytics_periods.html',
                         employee_name=session.get('employee_name'),
                         employee_role=session.get('employee_role'),
                         employee_profile_photo=employee_profile_photo,
                         hotel_settings=hotel_settings)

@app.route('/api/analytics/items', methods=['POST'])
def api_analytics_items():
    """API endpoint for item analytics data"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        data_type = data.get('dataType', 'general')  # 'general' or 'verified'
        filter_type = data.get('filterType', 'single')
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'})
        
        cursor = connection.cursor()
        
        # Build WHERE clause based on data type and filter
        where_conditions = []
        params = []
        
        # Data type filter
        if data_type == 'verified':
            where_conditions.append("s.status = 'confirmed'")
        # 'general' includes all statuses (pending, confirmed, cancelled)
        
        # Date filter
        if filter_type == 'single' and data.get('singleDate'):
            where_conditions.append("DATE(s.sale_date) = %s")
            params.append(data['singleDate'])
        elif filter_type == 'range' and data.get('fromDate') and data.get('toDate'):
            where_conditions.append("DATE(s.sale_date) BETWEEN %s AND %s")
            params.extend([data['fromDate'], data['toDate']])
        elif filter_type == 'month' and data.get('month'):
            where_conditions.append("DATE_FORMAT(s.sale_date, '%%Y-%%m') = %s")
            params.append(data['month'])
        elif filter_type == 'year' and data.get('year'):
            where_conditions.append("YEAR(s.sale_date) = %s")
            params.append(data['year'])
        
        where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
        
        # Get summary statistics
        summary_query = f"""
            SELECT 
                COUNT(DISTINCT s.id) as total_transactions,
                SUM(si.quantity) as total_items_sold,
                SUM(s.total_amount) as total_revenue,
                AVG(item_counts.item_count) as avg_items_per_sale
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            LEFT JOIN (
                SELECT sale_id, COUNT(*) as item_count
                FROM sales_items
                GROUP BY sale_id
            ) item_counts ON s.id = item_counts.sale_id
            {where_clause}
        """
        
        cursor.execute(summary_query, params)
        summary_result = cursor.fetchone()
        
        summary = {
            'totalTransactions': summary_result[0] or 0,
            'totalItemsSold': summary_result[1] or 0,
            'totalRevenue': float(summary_result[2] or 0),
            'avgItemsPerSale': float(summary_result[3] or 0)
        }
        
        # Get quantity sold by item with peak time and best selling employee
        # For subqueries, we need to handle parameters differently
        # Build the main query first
        quantity_query = f"""
            SELECT 
                si.item_name,
                SUM(si.quantity) as total_quantity,
                (SELECT HOUR(s2.sale_date) 
                 FROM sales s2 
                 JOIN sales_items si2 ON s2.id = si2.sale_id 
                 WHERE si2.item_name = si.item_name
                 {(' AND ' + ' AND '.join(where_conditions)) if where_conditions else ''}
                 GROUP BY HOUR(s2.sale_date) 
                 ORDER BY COUNT(*) DESC 
                 LIMIT 1) as peak_hour,
                (SELECT s3.employee_name 
                 FROM sales s3 
                 JOIN sales_items si3 ON s3.id = si3.sale_id 
                 WHERE si3.item_name = si.item_name
                 {(' AND ' + ' AND '.join(where_conditions)) if where_conditions else ''}
                 GROUP BY s3.employee_name 
                 ORDER BY SUM(si3.quantity) DESC 
                 LIMIT 1) as best_employee
            FROM sales s
            JOIN sales_items si ON s.id = si.sale_id
            {where_clause}
            GROUP BY si.item_name
            ORDER BY total_quantity DESC
        """
        
        # For subqueries, we need to repeat the parameters for each subquery
        # Since each subquery uses the same WHERE conditions, we need to duplicate params
        subquery_params = []
        if where_conditions:
            # Count how many subqueries we have (2 in this case)
            subquery_params = params * 2  # Duplicate params for both subqueries
        
        # Combine main query params with subquery params
        all_params = params + subquery_params
        
        cursor.execute(quantity_query, all_params)
        quantity_results = cursor.fetchall()
        quantity_sold = []
        for row in quantity_results:
            item_data = {
                'name': row[0], 
                'quantity': row[1],
                'peakTime': f"{row[2] or 0}:00" if row[2] is not None else "N/A",
                'bestEmployee': row[3] or "N/A"
            }
            quantity_sold.append(item_data)
        
        # Get peak sales times (hour of day analysis)
        peak_query = f"""
            SELECT 
                si.item_name,
                HOUR(s.sale_date) as hour_of_day,
                COUNT(*) as sales_count
            FROM sales s
            JOIN sales_items si ON s.id = si.sale_id
            {where_clause}
            GROUP BY si.item_name, HOUR(s.sale_date)
            ORDER BY si.item_name, sales_count DESC
        """
        
        cursor.execute(peak_query, params)
        peak_results = cursor.fetchall()
        
        # Process peak sales data
        peak_sales = {}
        for row in peak_results:
            item_name = row[0]
            hour = row[1]
            count = row[2]
            
            if item_name not in peak_sales:
                peak_sales[item_name] = {'peakTime': f"{hour}:00", 'sales': count}
            elif count > peak_sales[item_name]['sales']:
                peak_sales[item_name] = {'peakTime': f"{hour}:00", 'sales': count}
        
        peak_sales_list = [{'name': name, 'peakTime': data['peakTime'], 'sales': data['sales']} 
                          for name, data in peak_sales.items()]
        peak_sales_list.sort(key=lambda x: x['sales'], reverse=True)
        
        # Get top selling employees
        employees_query = f"""
            SELECT 
                s.employee_name,
                COUNT(DISTINCT s.id) as transaction_count,
                SUM(si.quantity) as items_sold
            FROM sales s
            JOIN sales_items si ON s.id = si.sale_id
            {where_clause}
            GROUP BY s.employee_name
            ORDER BY items_sold DESC
            LIMIT 10
        """
        
        cursor.execute(employees_query, params)
        employee_results = cursor.fetchall()
        top_employees = [{'name': row[0], 'sales': row[2]} for row in employee_results]
        
        # Get popular item pairs (items sold together in same transaction)
        pairs_query = f"""
            SELECT 
                si1.item_name as item1,
                si2.item_name as item2,
                COUNT(*) as pair_count
            FROM sales s
            JOIN sales_items si1 ON s.id = si1.sale_id
            JOIN sales_items si2 ON s.id = si2.sale_id
            WHERE si1.item_name < si2.item_name
            {(' AND ' + ' AND '.join(where_conditions)) if where_conditions else ''}
            GROUP BY si1.item_name, si2.item_name
            HAVING pair_count > 1
            ORDER BY pair_count DESC
            LIMIT 10
        """
        
        cursor.execute(pairs_query, params)
        pairs_results = cursor.fetchall()
        item_pairs = [{'item1': row[0], 'item2': row[1], 'count': row[2]} for row in pairs_results]
        
        # Get top items (same as quantity sold but formatted for top items section)
        top_items = quantity_sold[:5]
        
        # Prepare chart data
        if filter_type == 'single':
            # Bar chart for single day - show more items
            chart_data = {
                'labels': [item['name'] for item in quantity_sold[:15]],
                'data': [item['quantity'] for item in quantity_sold[:15]]
            }
        else:
            # Line chart for multiple days - get daily totals
            daily_query = f"""
                SELECT 
                    DATE(s.sale_date) as sale_date,
                    SUM(si.quantity) as daily_quantity
                FROM sales s
                JOIN sales_items si ON s.id = si.sale_id
                {where_clause}
                GROUP BY DATE(s.sale_date)
                ORDER BY sale_date
            """
            
            cursor.execute(daily_query, params)
            daily_results = cursor.fetchall()
            
            chart_data = {
                'labels': [row[0].strftime('%m/%d') for row in daily_results],
                'data': [row[1] for row in daily_results]
            }
        
        connection.close()
        
        analytics_data = {
            'summary': summary,
            'quantitySold': quantity_sold,
            'peakSales': peak_sales_list,
            'topEmployees': top_employees,
            'itemPairs': item_pairs,
            'topItems': top_items,
            'chartData': chart_data
        }
        
        return jsonify({
            'success': True,
            'analytics': analytics_data
        })
        
    except Exception as e:
        print(f"Error in item analytics API: {e}")
        return jsonify({'success': False, 'message': 'Error processing analytics data'}), 500
@app.route('/api/analytics/stock', methods=['POST'])
def api_analytics_stock():
    """API endpoint for stock analytics data"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        data_type = data.get('dataType', 'general')
        filter_type = data.get('filterType', 'single')
        date_range = data.get('dateRange', 30)  # Extract dateRange parameter
        
        # Handle new date filter parameters
        date_filter_type = data.get('filterType', 'single')  # 'day', 'range', 'month', 'preset'
        date_filter_data = data.get('date', None)  # For single day
        start_date = data.get('startDate', None)  # For date range
        end_date = data.get('endDate', None)  # For date range
        month = data.get('month', None)  # For month filter (YYYY-MM format)
        period = data.get('period', None)  # For preset periods
        
        print(f"Date filter debug - Type: {date_filter_type}, Month: {month}, Period: {period}")
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'})
        
        cursor = connection.cursor()
        
        # Build date filter conditions
        def build_date_filter_conditions():
            """Build SQL date filter conditions based on filter type"""
            conditions = []
            
            if date_filter_type == 'day' and date_filter_data:
                conditions.append(f"s.sale_date = '{date_filter_data}'")
                conditions.append(f"st.created_at >= '{date_filter_data} 00:00:00' AND st.created_at <= '{date_filter_data} 23:59:59'")
            elif date_filter_type == 'range' and start_date and end_date:
                conditions.append(f"s.sale_date BETWEEN '{start_date}' AND '{end_date}'")
                conditions.append(f"st.created_at >= '{start_date} 00:00:00' AND st.created_at <= '{end_date} 23:59:59'")
            elif date_filter_type == 'month' and month:
                year, month_num = month.split('-')
                conditions.append(f"YEAR(s.sale_date) = {year} AND MONTH(s.sale_date) = {month_num}")
                conditions.append(f"YEAR(st.created_at) = {year} AND MONTH(st.created_at) = {month_num}")
            elif date_filter_type == 'preset' and period:
                today = datetime.now().date()
                if period == 'today':
                    conditions.append(f"s.sale_date = '{today}'")
                    conditions.append(f"DATE(st.created_at) = '{today}'")
                elif period == 'yesterday':
                    yesterday = today - timedelta(days=1)
                    conditions.append(f"s.sale_date = '{yesterday}'")
                    conditions.append(f"DATE(st.created_at) = '{yesterday}'")
                elif period == 'last7days':
                    week_ago = today - timedelta(days=7)
                    conditions.append(f"s.sale_date >= '{week_ago}'")
                    conditions.append(f"DATE(st.created_at) >= '{week_ago}'")
                elif period == 'last30days':
                    month_ago = today - timedelta(days=30)
                    conditions.append(f"s.sale_date >= '{month_ago}'")
                    conditions.append(f"DATE(st.created_at) >= '{month_ago}'")
                elif period == 'last90days':
                    quarter_ago = today - timedelta(days=90)
                    conditions.append(f"s.sale_date >= '{quarter_ago}'")
                    conditions.append(f"DATE(st.created_at) >= '{quarter_ago}'")
                elif period == 'thisMonth':
                    first_of_month = today.replace(day=1)
                    conditions.append(f"s.sale_date >= '{first_of_month}'")
                    conditions.append(f"DATE(st.created_at) >= '{first_of_month}'")
                elif period == 'lastMonth':
                    first_of_last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
                    last_day_of_last_month = today.replace(day=1) - timedelta(days=1)
                    conditions.append(f"s.sale_date BETWEEN '{first_of_last_month}' AND '{last_day_of_last_month}'")
                    conditions.append(f"DATE(st.created_at) BETWEEN '{first_of_last_month}' AND '{last_day_of_last_month}'")
                elif period == 'thisYear':
                    first_of_year = today.replace(month=1, day=1)
                    conditions.append(f"s.sale_date >= '{first_of_year}'")
                    conditions.append(f"DATE(st.created_at) >= '{first_of_year}'")
                elif period == 'lastYear':
                    last_year = today.year - 1
                    conditions.append(f"YEAR(s.sale_date) = {last_year}")
                    conditions.append(f"YEAR(st.created_at) = {last_year}")
            
            return conditions
        
        # Get date filter conditions
        date_conditions = build_date_filter_conditions()
        print(f"Date conditions generated: {date_conditions}")
        
        # Default to current month if no filter is provided
        if len(date_conditions) == 0:
            current_month = datetime.now().strftime('%Y-%m')
            year, month_num = current_month.split('-')
            sales_date_condition = f"YEAR(s.sale_date) = {year} AND MONTH(s.sale_date) = {month_num}"
            stock_date_condition = f"YEAR(st.created_at) = {year} AND MONTH(st.created_at) = {month_num}"
            print(f"Using default current month: {current_month}")
        else:
            sales_date_condition = date_conditions[0]
            stock_date_condition = date_conditions[1] if len(date_conditions) > 1 else date_conditions[0]
        
        print(f"Sales date condition: {sales_date_condition}")
        print(f"Stock date condition: {stock_date_condition}")
        
        # Get summary statistics using actual thresholds
        summary_query = """
            SELECT 
                COUNT(*) as total_items,
                AVG(COALESCE(stock, 0)) as avg_stock_level,
                COUNT(CASE WHEN stock <= COALESCE(low_stock_threshold, 10) AND stock > 0 THEN 1 END) as low_stock_count,
                COUNT(CASE WHEN stock = 0 OR stock IS NULL THEN 1 END) as out_of_stock_count
            FROM items
            WHERE status = 'active'
        """
        
        cursor.execute(summary_query)
        summary_result = cursor.fetchone()
        
        summary = {
            'totalItems': summary_result[0] or 0,
            'avgStockLevel': round(float(summary_result[1] or 0), 1),
            'lowStockCount': summary_result[2] or 0,
            'outOfStockCount': summary_result[3] or 0,
            'avgTurnoverRate': 0.0
        }
        
        # Get comprehensive item profitability data with real sales and buying prices
        stock_levels_query = f"""
            SELECT 
                i.id,
                i.name,
                i.category,
                COALESCE(i.stock, 0) as current_stock,
                'piece' as unit,
                COALESCE(avg_purchases.avg_buying_price, i.price) as buying_price,
                'N/A' as supplier,
                COALESCE(i.low_stock_threshold, 10) as low_stock_threshold,
                CASE 
                    WHEN i.stock = 0 OR i.stock IS NULL THEN 'Out of Stock'
                    WHEN i.stock <= COALESCE(i.low_stock_threshold, 10) THEN 'Low Stock'
                    WHEN i.stock > 100 THEN 'Overstocked'
                    ELSE 'Normal'
                END as status,
                COALESCE(avg_sales.avg_selling_price, i.price) as selling_price,
                COALESCE(avg_sales.total_sold, 0) as total_sold,
                COALESCE(avg_sales.sale_frequency, 0) as sale_frequency,
                COALESCE(avg_sales.avg_selling_price, i.price) - COALESCE(avg_purchases.avg_buying_price, i.price) as profit_margin,
                ROUND(((COALESCE(avg_sales.avg_selling_price, i.price) - COALESCE(avg_purchases.avg_buying_price, i.price)) / COALESCE(avg_purchases.avg_buying_price, i.price)) * 100, 2) as profit_margin_percentage,
                COALESCE(avg_sales.avg_selling_price, i.price) * COALESCE(i.stock, 0) as potential_revenue
            FROM items i
            LEFT JOIN (
                SELECT 
                    si.item_name,
                    AVG(si.unit_price) as avg_selling_price,
                    SUM(si.quantity) as total_sold,
                    COUNT(DISTINCT s.id) as sale_frequency
                FROM sales_items si
                JOIN sales s ON si.sale_id = s.id
                WHERE {sales_date_condition}
                GROUP BY si.item_name
            ) avg_sales ON i.name = avg_sales.item_name
            LEFT JOIN (
                SELECT 
                    st.item_id,
                    AVG(st.price_per_unit) as avg_buying_price
                FROM stock_transactions st
                WHERE st.action = 'stock_in' 
                AND st.price_per_unit IS NOT NULL 
                AND st.price_per_unit > 0
                AND {stock_date_condition}
                GROUP BY st.item_id
            ) avg_purchases ON i.id = avg_purchases.item_id
            WHERE i.status = 'active'
            ORDER BY profit_margin DESC, current_stock DESC
        """
        
        cursor.execute(stock_levels_query)
        stock_levels_results = cursor.fetchall()
        stock_levels = [{
            'id': row[0],
            'name': row[1], 
            'category': row[2],
            'currentStock': int(row[3]), 
            'unit': row[4],
            'buyingPrice': float(row[5]),
            'supplier': row[6],
            'lowStockThreshold': row[7],
            'status': row[8],
            'sellingPrice': float(row[9]),
            'totalSold': int(row[10]),
            'saleFrequency': int(row[11]),
            'profitMargin': float(row[12]),
            'profitMarginPercentage': float(row[13]),
            'potentialRevenue': float(row[14])
        } for row in stock_levels_results]
        
        # Get stock turnover (simplified)
        stock_turnover = [{'name': row[0], 'turnoverRate': 0.0, 'period': '30 days'} for row in stock_levels_results[:5]]
        
        # Get low stock alerts using actual thresholds from database
        low_stock_query = """
            SELECT 
                name,
                COALESCE(stock, 0) as current_stock,
                COALESCE(low_stock_threshold, 10) as min_stock,
                'N/A' as supplier
            FROM items
            WHERE stock <= COALESCE(low_stock_threshold, 10) AND stock > 0 AND status = 'active'
            ORDER BY stock ASC
            LIMIT 10
        """
        
        cursor.execute(low_stock_query)
        low_stock_results = cursor.fetchall()
        low_stock_alerts = [{
            'name': row[0], 
            'currentStock': row[1], 
            'minStock': row[2],
            'supplier': row[3]
        } for row in low_stock_results]
        
        # Get reorder recommendations using actual thresholds
        reorder_query = """
            SELECT 
                name,
                COALESCE(stock, 0) as current_stock,
                CASE 
                    WHEN stock = 0 OR stock IS NULL THEN COALESCE(low_stock_threshold, 10) * 5
                    WHEN stock <= COALESCE(low_stock_threshold, 10) THEN COALESCE(low_stock_threshold, 10) * 5 - stock
                    ELSE 0
                END as recommended_qty,
                'N/A' as supplier,
                'N/A' as last_ordered
            FROM items
            WHERE (stock = 0 OR stock IS NULL OR stock <= COALESCE(low_stock_threshold, 10)) AND status = 'active'
            ORDER BY recommended_qty DESC
            LIMIT 10
        """
        
        cursor.execute(reorder_query)
        reorder_results = cursor.fetchall()
        reorder_recommendations = [{
            'name': row[0], 
            'currentStock': int(row[1]),
            'avgUsage': 0,  # Placeholder
            'recommendedQty': int(row[2]),
            'supplier': row[3],
            'lastOrdered': row[4]
        } for row in reorder_results if int(row[2]) > 0]
        
        # Get top moving items from actual sales data
        top_moving_query = f"""
            SELECT 
                si.item_name,
                SUM(si.quantity) as total_usage,
                COUNT(DISTINCT s.id) as order_frequency,
                COALESCE(i.stock, 0) as current_stock
            FROM sales_items si
            JOIN sales s ON si.sale_id = s.id
            LEFT JOIN items i ON si.item_name = i.name
            WHERE s.sale_date >= DATE_SUB(CURDATE(), INTERVAL {date_range} DAY)
            GROUP BY si.item_name
            ORDER BY total_usage DESC
            LIMIT 10
        """
        
        cursor.execute(top_moving_query)
        top_moving_results = cursor.fetchall()
        top_moving_items = [{
            'name': row[0], 
            'totalUsage': int(row[1]),
            'orderFrequency': int(row[2]),
            'currentStock': int(row[3])
        } for row in top_moving_results]
        
        # Calculate total stock value - convert to float to avoid decimal issues
        total_stock_value = sum(float(item['currentStock']) * float(item['buyingPrice']) for item in stock_levels)
        
        # Calculate monthly usage from sales data
        monthly_usage_query = f"""
            SELECT SUM(si.quantity) as total_usage
            FROM sales_items si
            JOIN sales s ON si.sale_id = s.id
            WHERE s.sale_date >= DATE_SUB(CURDATE(), INTERVAL {date_range} DAY)
        """
        
        cursor.execute(monthly_usage_query)
        monthly_usage_result = cursor.fetchone()
        monthly_usage = monthly_usage_result[0] if monthly_usage_result[0] else 0
        
        # Update summary with basic calculated values first
        summary['totalStockValue'] = total_stock_value
        summary['monthlyUsage'] = monthly_usage
        
        # Get real usage trends from sales data
        usage_trends_query = f"""
            SELECT 
                DATE(s.sale_date) as date,
                SUM(si.quantity) as total_usage
            FROM sales s
            JOIN sales_items si ON s.id = si.sale_id
            WHERE {sales_date_condition}
            GROUP BY DATE(s.sale_date)
            ORDER BY date ASC
        """
        
        cursor.execute(usage_trends_query)
        usage_trends_results = cursor.fetchall()
        
        # Process usage trends data
        usage_trends = {
            'labels': [row[0].strftime('%m/%d') for row in usage_trends_results],
            'data': [int(row[1]) for row in usage_trends_results]
        }
        
        # Reorder Frequency Chart - based on actual reorder needs
        reorder_frequency = {
            'labels': [item['name'] for item in reorder_recommendations[:5]],
            'data': [item['recommendedQty'] for item in reorder_recommendations[:5]]
        }
        
        # Price vs Stock Analysis with sales data for viability assessment
        price_stock_analysis = {
            'labels': [item['name'] for item in stock_levels[:10]],
            'stockData': [int(item['currentStock']) for item in stock_levels[:10]],
            'priceData': [float(item['buyingPrice']) for item in stock_levels[:10]],
            'salesData': [],  # Will be populated below
            'profitabilityData': []  # Will be populated below
        }
        
        # Get sales data for the top 10 items to assess viability
        item_names = [item['name'] for item in stock_levels[:10]]
        if item_names:
            placeholders = ','.join(['%s'] * len(item_names))
            sales_viability_query = f"""
                SELECT 
                    si.item_name,
                    AVG(si.unit_price) as avg_sale_price,
                    SUM(si.quantity) as total_sold,
                    COUNT(DISTINCT s.id) as sale_frequency,
                    AVG(si.unit_price) - i.price as profit_margin
                FROM sales_items si
                JOIN sales s ON si.sale_id = s.id
                JOIN items i ON si.item_name = i.name
                WHERE si.item_name IN ({placeholders})
                AND s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                GROUP BY si.item_name
                ORDER BY total_sold DESC
            """
            
            cursor.execute(sales_viability_query, item_names)
            sales_viability_results = cursor.fetchall()
            
            # Create lookup for sales data
            sales_lookup = {}
            for row in sales_viability_results:
                sales_lookup[row[0]] = {
                    'avgSalePrice': float(row[1]),
                    'totalSold': int(row[2]),
                    'saleFrequency': int(row[3]),
                    'profitMargin': float(row[4])
                }
            
            # Populate sales and profitability data
            for item in stock_levels[:10]:
                if item['name'] in sales_lookup:
                    sales_data = sales_lookup[item['name']]
                    price_stock_analysis['salesData'].append(sales_data['avgSalePrice'])
                    price_stock_analysis['profitabilityData'].append(sales_data['profitMargin'])
                else:
                    price_stock_analysis['salesData'].append(0)
                    price_stock_analysis['profitabilityData'].append(0)
        
        # Stock In vs Stock Out Analysis with sales revenue for viability assessment
        stock_in_out_query = f"""
            SELECT 
                DATE(s.sale_date) as date,
                SUM(si.quantity) as stock_out,
                SUM(si.total_price) as daily_revenue,
                COUNT(DISTINCT si.item_name) as items_sold
            FROM sales s
            JOIN sales_items si ON s.id = si.sale_id
            WHERE {sales_date_condition}
            GROUP BY DATE(s.sale_date)
            ORDER BY date ASC
        """
        
        cursor.execute(stock_in_out_query)
        stock_in_out_results = cursor.fetchall()
        
        # Process stock out data with revenue information
        stock_out_data = [int(row[1]) for row in stock_in_out_results]
        stock_out_labels = [row[0].strftime('%m/%d') for row in stock_in_out_results]
        daily_revenue = [float(row[2]) for row in stock_in_out_results]
        items_sold = [int(row[3]) for row in stock_in_out_results]
        
        # For stock in, we'll use a simplified approach based on reorder patterns
        stock_in_data = [max(0, float(out) * 1.2) for out in stock_out_data]  # Stock in is typically 20% more than stock out
        
        stock_in_out_analysis = {
            'labels': stock_out_labels,
            'stockIn': stock_in_data,
            'stockOut': stock_out_data,
            'revenue': daily_revenue,
            'itemsSold': items_sold
        }
        
        # Calculate additional metrics for KPI cards after price_stock_analysis is defined
        # Calculate average profit margin
        avg_profit_margin = 0
        profitable_items = 0
        if price_stock_analysis['profitabilityData']:
            positive_margins = [m for m in price_stock_analysis['profitabilityData'] if m > 0]
            avg_profit_margin = sum(positive_margins) / len(positive_margins) if positive_margins else 0
            profitable_items = len(positive_margins)
        
        # Calculate total revenue from sales data
        total_revenue = sum(daily_revenue) if daily_revenue else 0
        
        # Calculate stock turnover rate
        stock_turnover_rate = 0
        if total_stock_value > 0:
            stock_turnover_rate = (total_revenue / total_stock_value) * 100
        
        # Calculate average items sold per day
        avg_items_per_day = sum(items_sold) / len(items_sold) if items_sold else 0
        
        # Update summary with additional calculated values
        summary['avgProfitMargin'] = round(avg_profit_margin, 2)
        summary['profitableItems'] = profitable_items
        summary['totalRevenue'] = round(total_revenue, 2)
        summary['stockTurnoverRate'] = round(stock_turnover_rate, 1)
        summary['avgItemsPerDay'] = round(avg_items_per_day, 1)
        
        # Chart data - only include what we need
        chart_data = {
            'usageTrends': usage_trends,
            'reorderFrequency': reorder_frequency,
            'priceStockAnalysis': price_stock_analysis,
            'stockInOutAnalysis': stock_in_out_analysis
        }
        
        connection.close()
        
        analytics_data = {
            'summary': summary,
            'stockLevels': stock_levels,
            'stockTurnover': stock_turnover,
            'lowStockAlerts': low_stock_alerts,
            'reorderRecommendations': reorder_recommendations,
            'mostUsedItems': top_moving_items,  # Fixed: Changed from 'topMovingItems' to 'mostUsedItems'
            'inventoryOverview': stock_levels,  # Use the detailed stock levels as inventory overview
            'chartData': chart_data
        }
        
        return jsonify({
            'success': True,
            'analytics': analytics_data
        })
        
    except Exception as e:
        print(f"Error in stock analytics API: {e}")
        return jsonify({'success': False, 'message': 'Error processing stock analytics data'}), 500

@app.route('/api/stock/mark-alerts-read', methods=['POST'])
def mark_stock_alerts_read():
    """Mark all stock alerts as read"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        # In a real implementation, you would update a database table to mark alerts as read
        # For now, we'll just return success
        return jsonify({'success': True, 'message': 'All alerts marked as read'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/stock/auto-reorder', methods=['POST'])
def auto_reorder_stock():
    """Perform automatic reordering based on recommendations"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'})
        
        cursor = connection.cursor()
        
        # Get items that need reordering using actual thresholds
        reorder_query = """
            SELECT 
                id,
                name,
                COALESCE(stock, 0) as current_stock,
                CASE 
                    WHEN stock = 0 OR stock IS NULL THEN COALESCE(low_stock_threshold, 10) * 5
                    WHEN stock <= COALESCE(low_stock_threshold, 10) THEN COALESCE(low_stock_threshold, 10) * 5 - stock
                    ELSE 0
                END as recommended_qty
            FROM items
            WHERE (stock = 0 OR stock IS NULL OR stock <= COALESCE(low_stock_threshold, 10)) AND status = 'active'
        """
        
        cursor.execute(reorder_query)
        reorder_items = cursor.fetchall()
        
        orders_created = 0
        
        # Create purchase orders for items that need reordering
        for item in reorder_items:
            if item[3] > 0:  # If recommended quantity > 0
                # In a real implementation, you would create actual purchase orders
                # For now, we'll just simulate the process
                orders_created += 1
        
        connection.close()
        
        return jsonify({
            'success': True, 
            'message': f'Auto reorder completed',
            'ordersCreated': orders_created
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
@app.route('/api/analytics/periods', methods=['POST'])
def api_analytics_periods():
    """API endpoint for period analytics data"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        data_type = data.get('dataType', 'general')  # 'general' or 'verified'
        filter_type = data.get('filterType', 'single')  # 'single', 'range', 'month', 'year'
        
        # Build WHERE clause based on data type
        if data_type == 'verified':
            status_condition = "s.status = 'confirmed'"
        else:
            status_condition = "s.status IN ('pending', 'confirmed', 'cancelled')"
        
        # Build date filter conditions
        where_conditions = [status_condition]
        params = []
        
        if filter_type == 'single':
            single_date = data.get('singleDate')
            if single_date:
                where_conditions.append("DATE(s.sale_date) = %s")
                params.append(single_date)
        elif filter_type == 'range':
            from_date = data.get('fromDate')
            to_date = data.get('toDate')
            if from_date and to_date:
                where_conditions.append("DATE(s.sale_date) BETWEEN %s AND %s")
                params.extend([from_date, to_date])
        elif filter_type == 'month':
            month = data.get('month')
            if month:
                where_conditions.append("DATE_FORMAT(s.sale_date, '%%Y-%%m') = %s")
                params.append(month)
        elif filter_type == 'year':
            year = data.get('year')
            if year:
                where_conditions.append("YEAR(s.sale_date) = %s")
                params.append(year)
        
        where_clause = " AND ".join(where_conditions)
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Summary statistics
        summary_query = f"""
            SELECT 
                COUNT(DISTINCT s.id) as total_transactions,
                COALESCE(SUM(s.total_amount), 0) as total_revenue,
                COALESCE(SUM(si.quantity), 0) as total_items_sold,
                COUNT(DISTINCT s.employee_id) as active_employees,
                COALESCE(AVG(s.total_amount), 0) as avg_transaction_value
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            WHERE {where_clause}
        """
        
        cursor.execute(summary_query, params)
        summary_result = cursor.fetchone()
        
        summary = {
            'totalTransactions': summary_result[0] or 0,
            'totalRevenue': float(summary_result[1] or 0),
            'totalItemsSold': summary_result[2] or 0,
            'activeEmployees': summary_result[3] or 0,
            'avgTransactionValue': float(summary_result[4] or 0)
        }
        
        # All items with comprehensive sales data
        all_items_query = f"""
            SELECT 
                i.id,
                i.name,
                i.price,
                i.category,
                SUM(si.quantity) as total_quantity,
                COUNT(DISTINCT s.id) as times_sold,
                COALESCE(SUM(si.total_price), 0) as total_revenue,
                COALESCE(AVG(si.quantity), 0) as avg_quantity_per_sale,
                MIN(s.sale_date) as first_sold,
                MAX(s.sale_date) as last_sold
            FROM items i
            LEFT JOIN sales_items si ON i.id = si.item_id
            LEFT JOIN sales s ON si.sale_id = s.id AND {where_clause.replace('s.', 's.')}
            GROUP BY i.id, i.name, i.price, i.category
            ORDER BY total_quantity DESC
        """
        
        cursor.execute(all_items_query, params)
        all_items_results = cursor.fetchall()
        all_items = []
        for row in all_items_results:
            all_items.append({
                'id': row[0],
                'name': row[1],
                'price': float(row[2]) if row[2] else 0,
                'category': row[3],
                'total_quantity': row[4] or 0,
                'times_sold': row[5] or 0,
                'total_revenue': float(row[6]) if row[6] else 0,
                'avg_quantity_per_sale': float(row[7]) if row[7] else 0,
                'first_sold': row[8].strftime('%Y-%m-%d %H:%M') if row[8] else None,
                'last_sold': row[9].strftime('%Y-%m-%d %H:%M') if row[9] else None
            })
        
        # Best selling items (top 10)
        best_items = all_items[:10] if all_items else []
        
        # Worst selling items (bottom 10, excluding items with 0 sales)
        worst_items = [item for item in all_items if item['total_quantity'] > 0][-10:] if all_items else []
        
        # Items with peak sales days and times analysis
        items_peak_analysis_query = f"""
            SELECT 
                i.name,
                DAYNAME(s.sale_date) as day_name,
                HOUR(s.sale_date) as hour_of_day,
                SUM(si.quantity) as day_quantity,
                COUNT(DISTINCT s.id) as day_transactions,
                e.full_name as employee_name,
                COUNT(DISTINCT s.employee_id) as employee_count
            FROM items i
            JOIN sales_items si ON i.id = si.item_id
            JOIN sales s ON si.sale_id = s.id
            JOIN employees e ON s.employee_id = e.id
            WHERE {where_clause}
            GROUP BY i.name, DAYNAME(s.sale_date), HOUR(s.sale_date), e.full_name
            ORDER BY i.name, day_quantity DESC
        """
        
        cursor.execute(items_peak_analysis_query, params)
        items_peak_analysis_results = cursor.fetchall()
        
        # Group by item to find peak day, time, and best employee for each
        items_peak_data = {}
        for row in items_peak_analysis_results:
            item_name = row[0]
            if item_name not in items_peak_data:
                items_peak_data[item_name] = {
                    'days': {},
                    'times': {},
                    'employees': {}
                }
            
            day_name = row[1]
            hour = row[2]
            quantity = row[3]
            transactions = row[4]
            employee_name = row[5]
            
            # Track peak day
            if day_name not in items_peak_data[item_name]['days']:
                items_peak_data[item_name]['days'][day_name] = 0
            items_peak_data[item_name]['days'][day_name] += quantity
            
            # Track peak time
            if hour not in items_peak_data[item_name]['times']:
                items_peak_data[item_name]['times'][hour] = 0
            items_peak_data[item_name]['times'][hour] += quantity
            
            # Track best employee
            if employee_name not in items_peak_data[item_name]['employees']:
                items_peak_data[item_name]['employees'][employee_name] = 0
            items_peak_data[item_name]['employees'][employee_name] += quantity
        
        # Find peak day, time, and best employee for each item
        for item_name, data in items_peak_data.items():
            peak_day = max(data['days'].items(), key=lambda x: x[1]) if data['days'] else (None, 0)
            peak_time = max(data['times'].items(), key=lambda x: x[1]) if data['times'] else (None, 0)
            best_employee = max(data['employees'].items(), key=lambda x: x[1]) if data['employees'] else (None, 0)
            
            # Update the all_items data with peak info
            for item in all_items:
                if item['name'] == item_name:
                    item['peak_day'] = peak_day[0] if peak_day[0] else 'N/A'
                    item['peak_day_quantity'] = peak_day[1]
                    item['peak_time'] = f"{peak_time[0]}:00" if peak_time[0] is not None else 'N/A'
                    item['peak_time_quantity'] = peak_time[1]
                    item['best_employee'] = best_employee[0] if best_employee[0] else 'N/A'
                    item['best_employee_quantity'] = best_employee[1]
                    break
        
        # Best item combinations
        combinations_query = f"""
            SELECT 
                i1.name as item1,
                i2.name as item2,
                COUNT(*) as combination_count
            FROM sales s
            JOIN sales_items si1 ON s.id = si1.sale_id
            JOIN sales_items si2 ON s.id = si2.sale_id
            JOIN items i1 ON si1.item_id = i1.id
            JOIN items i2 ON si2.item_id = i2.id
            WHERE {where_clause}
            AND si1.item_id < si2.item_id
            GROUP BY i1.name, i2.name
            ORDER BY combination_count DESC
            LIMIT 10
        """
        
        cursor.execute(combinations_query, params)
        combinations_results = cursor.fetchall()
        best_combinations = [{'item1': row[0], 'item2': row[1], 'count': row[2]} for row in combinations_results]
        
        # Most active employees
        most_active_query = f"""
            SELECT 
                e.full_name,
                COUNT(s.id) as sales_count,
                COALESCE(SUM(s.total_amount), 0) as total_sales
            FROM sales s
            JOIN employees e ON s.employee_id = e.id
            WHERE {where_clause}
            GROUP BY e.full_name
            ORDER BY sales_count DESC
            LIMIT 10
        """
        
        cursor.execute(most_active_query, params)
        most_active_results = cursor.fetchall()
        most_active_employees = [{'name': row[0], 'sales': row[1], 'revenue': float(row[2])} for row in most_active_results]
        
        # Least active employees
        least_active_query = f"""
            SELECT 
                e.full_name,
                COUNT(s.id) as sales_count,
                COALESCE(SUM(s.total_amount), 0) as total_sales
            FROM sales s
            JOIN employees e ON s.employee_id = e.id
            WHERE {where_clause}
            GROUP BY e.full_name
            ORDER BY sales_count ASC
            LIMIT 10
        """
        
        cursor.execute(least_active_query, params)
        least_active_results = cursor.fetchall()
        least_active_employees = [{'name': row[0], 'sales': row[1], 'revenue': float(row[2])} for row in least_active_results]
        
        # Peak hour analysis
        peak_hour_query = f"""
            SELECT 
                HOUR(s.sale_date) as hour,
                COUNT(*) as transaction_count,
                COALESCE(SUM(s.total_amount), 0) as revenue
            FROM sales s
            WHERE {where_clause}
            GROUP BY HOUR(s.sale_date)
            ORDER BY transaction_count DESC
            LIMIT 1
        """
        
        cursor.execute(peak_hour_query, params)
        peak_hour_result = cursor.fetchone()
        peak_hour = f"{peak_hour_result[0]}:00" if peak_hour_result else "N/A"
        
        # Busiest day analysis
        busiest_day_query = f"""
            SELECT 
                DAYNAME(s.created_at) as day_name,
                COUNT(*) as transaction_count,
                COALESCE(SUM(s.total_amount), 0) as revenue
            FROM sales s
            WHERE {where_clause}
            GROUP BY DAYNAME(s.created_at)
            ORDER BY transaction_count DESC
            LIMIT 1
        """
        
        cursor.execute(busiest_day_query, params)
        busiest_day_result = cursor.fetchone()
        busiest_day = busiest_day_result[0] if busiest_day_result else "N/A"
        
        # Period summary
        period_summary = {
            'avgTransactionValue': summary['avgTransactionValue'],
            'peakHour': peak_hour,
            'busiestDay': busiest_day
        }
        
        # Performance insights
        performance_insights = []
        if summary['totalTransactions'] > 0:
            if summary['avgTransactionValue'] > 1000:
                performance_insights.append("High average transaction value indicates good upselling")
            if len(most_active_employees) > 0 and len(least_active_employees) > 0:
                if most_active_employees[0]['sales'] > least_active_employees[0]['sales'] * 2:
                    performance_insights.append("Significant performance gap between top and bottom employees")
            if len(best_combinations) > 0:
                performance_insights.append("Strong item combinations suggest effective cross-selling")
        
        # Chart data
        chart_data = {'labels': [], 'revenue': []}
        
        if filter_type == 'single':
            # Hourly breakdown for single day
            hourly_query = f"""
                SELECT 
                    HOUR(s.sale_date) as hour,
                    COALESCE(SUM(s.total_amount), 0) as revenue
                FROM sales s
                WHERE {where_clause}
                GROUP BY HOUR(s.sale_date)
                ORDER BY hour
            """
            cursor.execute(hourly_query, params)
            hourly_results = cursor.fetchall()
            chart_data['labels'] = [f"{row[0]}:00" for row in hourly_results]
            chart_data['revenue'] = [float(row[1]) for row in hourly_results]
        else:
            # Daily breakdown for multi-day periods
            daily_query = f"""
                SELECT 
                    DATE(s.sale_date) as date,
                    COALESCE(SUM(s.total_amount), 0) as revenue
                FROM sales s
                WHERE {where_clause}
                GROUP BY DATE(s.sale_date)
                ORDER BY date
            """
            cursor.execute(daily_query, params)
            daily_results = cursor.fetchall()
            chart_data['labels'] = [row[0].strftime('%m/%d') for row in daily_results]
            chart_data['revenue'] = [float(row[1]) for row in daily_results]
        
        analytics_data = {
            'summary': summary,
            'allItems': all_items,
            'bestItems': best_items,
            'worstItems': worst_items,
            'bestCombinations': best_combinations,
            'mostActiveEmployees': most_active_employees,
            'leastActiveEmployees': least_active_employees,
            'periodSummary': period_summary,
            'performanceInsights': performance_insights,
            'chartData': chart_data
        }
        
        return jsonify({
            'success': True,
            'analytics': analytics_data
        })
    except Exception as e:
        print(f"Error in period analytics API: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        })
    finally:
        if connection:
            connection.close()

@app.route('/api/analytics/employees', methods=['POST'])
def api_analytics_employees():
    """API endpoint for employee analytics data"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        data_type = data.get('dataType', 'general')  # 'general' or 'verified'
        filter_type = data.get('filterType', 'single')  # 'single', 'range', 'month', 'year'
        
        # Build WHERE clause based on data type
        if data_type == 'verified':
            status_condition = "s.status = 'confirmed'"
        else:
            status_condition = "s.status IN ('pending', 'confirmed', 'cancelled')"
        
        # Build date filter conditions
        where_conditions = [status_condition]
        params = []
        
        if filter_type == 'single':
            single_date = data.get('singleDate')
            if single_date:
                where_conditions.append("DATE(s.sale_date) = %s")
                params.append(single_date)
        elif filter_type == 'range':
            from_date = data.get('fromDate')
            to_date = data.get('toDate')
            if from_date and to_date:
                where_conditions.append("DATE(s.sale_date) BETWEEN %s AND %s")
                params.extend([from_date, to_date])
        elif filter_type == 'month':
            month = data.get('month')
            if month:
                where_conditions.append("DATE_FORMAT(s.sale_date, '%%Y-%%m') = %s")
                params.append(month)
        elif filter_type == 'year':
            year = data.get('year')
            if year:
                where_conditions.append("YEAR(s.sale_date) = %s")
                params.append(year)
        
        where_clause = " AND ".join(where_conditions)
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Summary statistics
        summary_query = f"""
            SELECT 
                COUNT(DISTINCT e.id) as total_employees,
                COUNT(DISTINCT CASE WHEN e.status = 'active' THEN e.id END) as active_employees,
                COUNT(DISTINCT s.id) as total_transactions,
                COALESCE(AVG(s.total_amount), 0) as avg_sales_per_employee
            FROM employees e
            LEFT JOIN sales s ON e.id = s.employee_id AND {where_clause}
        """
        
        cursor.execute(summary_query, params)
        summary_result = cursor.fetchone()
        
        summary = {
            'totalEmployees': summary_result[0] or 0,
            'activeEmployees': summary_result[1] or 0,
            'totalTransactions': summary_result[2] or 0,
            'avgSalesPerEmployee': float(summary_result[3] or 0)
        }
        
        # Top performers (by sales count)
        top_performers_query = f"""
            SELECT 
                e.full_name,
                e.role,
                COUNT(s.id) as sales_count,
                COALESCE(SUM(s.total_amount), 0) as total_revenue
            FROM employees e
            LEFT JOIN sales s ON e.id = s.employee_id AND {where_clause}
            WHERE e.status = 'active'
            GROUP BY e.id, e.full_name, e.role
            ORDER BY sales_count DESC
            LIMIT 10
        """
        
        cursor.execute(top_performers_query, params)
        top_performers_results = cursor.fetchall()
        top_performers = [{'name': row[0], 'role': row[1], 'sales': row[2], 'revenue': float(row[3])} for row in top_performers_results]
        
        # Sales leaders (by revenue)
        sales_leaders_query = f"""
            SELECT 
                e.full_name,
                e.role,
                COUNT(s.id) as sales_count,
                COALESCE(SUM(s.total_amount), 0) as total_revenue
            FROM employees e
            LEFT JOIN sales s ON e.id = s.employee_id AND {where_clause}
            WHERE e.status = 'active'
            GROUP BY e.id, e.full_name, e.role
            ORDER BY total_revenue DESC
            LIMIT 10
        """
        
        cursor.execute(sales_leaders_query, params)
        sales_leaders_results = cursor.fetchall()
        sales_leaders = [{'name': row[0], 'role': row[1], 'sales': row[2], 'revenue': float(row[3])} for row in sales_leaders_results]
        
        # Most active employees
        most_active_query = f"""
            SELECT 
                e.full_name,
                COUNT(s.id) as sales_count,
                COALESCE(SUM(s.total_amount), 0) as total_revenue
            FROM employees e
            LEFT JOIN sales s ON e.id = s.employee_id AND {where_clause}
            WHERE e.status = 'active'
            GROUP BY e.id, e.full_name
            ORDER BY sales_count DESC
            LIMIT 10
        """
        
        cursor.execute(most_active_query, params)
        most_active_results = cursor.fetchall()
        most_active_employees = [{'name': row[0], 'sales': row[1], 'revenue': float(row[2])} for row in most_active_results]
        
        # Least active employees
        least_active_query = f"""
            SELECT 
                e.full_name,
                COUNT(s.id) as sales_count,
                COALESCE(SUM(s.total_amount), 0) as total_revenue
            FROM employees e
            LEFT JOIN sales s ON e.id = s.employee_id AND {where_clause}
            WHERE e.status = 'active'
            GROUP BY e.id, e.full_name
            ORDER BY sales_count ASC
            LIMIT 10
        """
        
        cursor.execute(least_active_query, params)
        least_active_results = cursor.fetchall()
        least_active_employees = [{'name': row[0], 'sales': row[1], 'revenue': float(row[2])} for row in least_active_results]
        
        # Employee roles distribution
        roles_query = f"""
            SELECT 
                e.role,
                COUNT(DISTINCT e.id) as role_count
            FROM employees e
            WHERE e.status = 'active'
            GROUP BY e.role
            ORDER BY role_count DESC
        """
        
        cursor.execute(roles_query)
        roles_results = cursor.fetchall()
        employee_roles = [{'role': row[0], 'count': row[1]} for row in roles_results]
        
        # Performance insights
        performance_insights = []
        if summary['totalEmployees'] > 0:
            if summary['avgSalesPerEmployee'] > 1000:
                performance_insights.append("High average sales per employee indicates good performance")
            if len(top_performers) > 0 and len(least_active_employees) > 0:
                if top_performers[0]['sales'] > least_active_employees[0]['sales'] * 2:
                    performance_insights.append("Significant performance gap between top and bottom performers")
            if len(employee_roles) > 1:
                performance_insights.append("Diverse role distribution suggests good team structure")
        
        # Performance trends
        performance_trends = []
        if summary['totalTransactions'] > 0:
            performance_trends.append("Active sales activity detected in the selected period")
            if summary['avgSalesPerEmployee'] > 500:
                performance_trends.append("Above-average sales performance across the team")
        
        # Chart data
        chart_data = {'labels': [], 'revenue': []}
        
        if filter_type == 'single':
            # Hourly breakdown for single day
            hourly_query = f"""
                SELECT 
                    HOUR(s.sale_date) as hour,
                    COALESCE(SUM(s.total_amount), 0) as revenue
                FROM sales s
                WHERE {where_clause}
                GROUP BY HOUR(s.sale_date)
                ORDER BY hour
            """
            cursor.execute(hourly_query, params)
            hourly_results = cursor.fetchall()
            chart_data['labels'] = [f"{row[0]}:00" for row in hourly_results]
            chart_data['revenue'] = [float(row[1]) for row in hourly_results]
        else:
            # Daily breakdown for multi-day periods
            daily_query = f"""
                SELECT 
                    DATE(s.sale_date) as date,
                    COALESCE(SUM(s.total_amount), 0) as revenue
                FROM sales s
                WHERE {where_clause}
                GROUP BY DATE(s.sale_date)
                ORDER BY date
            """
            cursor.execute(daily_query, params)
            daily_results = cursor.fetchall()
            chart_data['labels'] = [row[0].strftime('%m/%d') for row in daily_results]
            chart_data['revenue'] = [float(row[1]) for row in daily_results]
        
        analytics_data = {
            'summary': summary,
            'topPerformers': top_performers,
            'salesLeaders': sales_leaders,
            'mostActiveEmployees': most_active_employees,
            'leastActiveEmployees': least_active_employees,
            'employeeRoles': employee_roles,
            'performanceInsights': performance_insights,
            'performanceTrends': performance_trends,
            'chartData': chart_data
        }
        
        return jsonify({
            'success': True,
            'analytics': analytics_data
        })
    except Exception as e:
        print(f"Error in employee analytics API: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        })
    finally:
        if connection:
            connection.close()

@app.route('/api/analytics/sales', methods=['POST'])
def api_analytics_sales():
    """API endpoint for sales analytics data"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        data_type = data.get('dataType', 'general')  # 'general' or 'verified'
        filter_type = data.get('filterType', 'single')  # 'single', 'range', 'month', 'year'
        
        # Build WHERE clause based on data type
        if data_type == 'verified':
            status_condition = "s.status = 'confirmed'"
        else:
            status_condition = "s.status IN ('pending', 'confirmed', 'cancelled')"
        
        # Build date filter conditions
        where_conditions = [status_condition]
        params = []
        
        if filter_type == 'single':
            single_date = data.get('singleDate')
            if single_date:
                where_conditions.append("DATE(s.sale_date) = %s")
                params.append(single_date)
        elif filter_type == 'range':
            from_date = data.get('fromDate')
            to_date = data.get('toDate')
            if from_date and to_date:
                where_conditions.append("DATE(s.sale_date) BETWEEN %s AND %s")
                params.extend([from_date, to_date])
        elif filter_type == 'month':
            month = data.get('month')
            if month:
                where_conditions.append("DATE_FORMAT(s.sale_date, '%%Y-%%m') = %s")
                params.append(month)
        elif filter_type == 'year':
            year = data.get('year')
            if year:
                where_conditions.append("YEAR(s.sale_date) = %s")
                params.append(year)
        
        where_clause = " AND ".join(where_conditions)
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Summary statistics
        summary_query = f"""
            SELECT 
                COUNT(DISTINCT s.id) as total_transactions,
                COALESCE(SUM(s.total_amount), 0) as total_revenue,
                COALESCE(AVG(s.total_amount), 0) as avg_order_value,
                COALESCE(SUM(si.quantity), 0) as total_items_sold
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            WHERE {where_clause}
        """
        
        cursor.execute(summary_query, params)
        summary_result = cursor.fetchone()
        
        summary = {
            'totalTransactions': summary_result[0] or 0,
            'totalRevenue': float(summary_result[1] or 0),
            'avgOrderValue': float(summary_result[2] or 0),
            'totalItemsSold': summary_result[3] or 0
        }
        
        # Peak sale period analysis
        peak_period_query = f"""
            SELECT 
                HOUR(s.sale_date) as hour,
                COUNT(*) as transaction_count,
                COALESCE(SUM(s.total_amount), 0) as revenue
            FROM sales s
            WHERE {where_clause}
            GROUP BY HOUR(s.sale_date)
            ORDER BY transaction_count DESC, revenue DESC
            LIMIT 1
        """
        
        cursor.execute(peak_period_query, params)
        peak_result = cursor.fetchone()
        peak_period = "No data available"
        if peak_result:
            hour = peak_result[0]
            start_time = f"{hour:02d}:00"
            end_time = f"{hour+1:02d}:00"
            peak_period = f"{start_time} - {end_time}"
        
        # Items with revenue and employee who sold them
        items_with_employee_query = f"""
            SELECT 
                i.name,
                i.category,
                SUM(si.quantity) as total_quantity,
                COALESCE(SUM(si.total_price), 0) as total_revenue,
                e.full_name as employee_name
            FROM sales s
            JOIN sales_items si ON s.id = si.sale_id
            JOIN items i ON si.item_id = i.id
            JOIN employees e ON s.employee_id = e.id
            WHERE {where_clause}
            GROUP BY i.id, i.name, i.category, e.full_name
            ORDER BY total_revenue DESC
            LIMIT 20
        """
        
        cursor.execute(items_with_employee_query, params)
        items_with_employee_results = cursor.fetchall()
        items_with_employee = [{'name': row[0], 'category': row[1], 'quantity': row[2], 'revenue': float(row[3]), 'employee': row[4]} for row in items_with_employee_results]
        
        # Employee revenue analysis
        employee_revenue_query = f"""
            SELECT 
                e.full_name as employee_name,
                e.role as employee_role,
                COUNT(DISTINCT s.id) as total_transactions,
                COALESCE(SUM(s.total_amount), 0) as total_revenue
            FROM sales s
            JOIN employees e ON s.employee_id = e.id
            WHERE {where_clause}
            GROUP BY e.id, e.full_name, e.role
            ORDER BY total_revenue DESC
        """
        
        cursor.execute(employee_revenue_query, params)
        employee_revenue_results = cursor.fetchall()
        employee_revenue = [{'name': row[0], 'role': row[1], 'transactions': row[2], 'revenue': float(row[3])} for row in employee_revenue_results]
        
        # Chart data
        chart_data = {'labels': [], 'revenue': []}
        
        if filter_type == 'single':
            # Hourly breakdown for single day
            hourly_query = f"""
                SELECT 
                    HOUR(s.sale_date) as hour,
                    COALESCE(SUM(s.total_amount), 0) as revenue
                FROM sales s
                WHERE {where_clause}
                GROUP BY HOUR(s.sale_date)
                ORDER BY hour
            """
            cursor.execute(hourly_query, params)
            hourly_results = cursor.fetchall()
            chart_data['labels'] = [f"{row[0]}:00" for row in hourly_results]
            chart_data['revenue'] = [float(row[1]) for row in hourly_results]
        else:
            # Daily breakdown for multi-day periods
            daily_query = f"""
                SELECT 
                    DATE(s.sale_date) as date,
                    COALESCE(SUM(s.total_amount), 0) as revenue
                FROM sales s
                WHERE {where_clause}
                GROUP BY DATE(s.sale_date)
                ORDER BY date
            """
            cursor.execute(daily_query, params)
            daily_results = cursor.fetchall()
            chart_data['labels'] = [row[0].strftime('%m/%d') for row in daily_results]
            chart_data['revenue'] = [float(row[1]) for row in daily_results]
        
        analytics_data = {
            'summary': summary,
            'peakPeriod': peak_period,
            'itemsWithEmployee': items_with_employee,
            'employeeRevenue': employee_revenue,
            'chartData': chart_data
        }
        
        return jsonify({
            'success': True,
            'analytics': analytics_data
        })
    except Exception as e:
        print(f"Error in sales analytics API: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        })
    finally:
        if connection:
            connection.close()
@app.route('/receipts')
def receipts():
    """Receipts management page"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return redirect(url_for('index'))
    
    try:
        connection = get_db_connection()
        if not connection:
            return render_template('receipts.html', receipts=[], error="Database connection failed")
        
        cursor = connection.cursor()
        
        # Fetch all sales with employee information
        cursor.execute("""
            SELECT s.id, s.receipt_number, s.employee_name, s.subtotal, s.tax_amount, 
                   s.total_amount, s.sale_date, s.created_at, s.status,
                   COUNT(si.id) as item_count
            FROM sales s
            LEFT JOIN sales_items si ON s.id = si.sale_id
            GROUP BY s.id, s.receipt_number, s.employee_name, s.subtotal, s.tax_amount, 
                     s.total_amount, s.sale_date, s.created_at, s.status
            ORDER BY 
                CASE s.status 
                    WHEN 'pending' THEN 1 
                    WHEN 'cancelled' THEN 2 
                    WHEN 'confirmed' THEN 3 
                    ELSE 4 
                END,
                s.created_at DESC
        """)
        
        receipts = cursor.fetchall()
        
        # Convert to list of dictionaries for easier template handling
        receipts_list = []
        today_receipts_count = 0
        total_revenue = 0.0
        today = datetime.now().date()
        
        for receipt in receipts:
            receipt_data = {
                'id': receipt[0],
                'receipt_number': receipt[1],
                'employee_name': receipt[2],
                'subtotal': float(receipt[3]),
                'tax_amount': float(receipt[4]),
                'total_amount': float(receipt[5]),
                'sale_date': receipt[6],
                'created_at': receipt[7],
                'status': receipt[8] or 'pending',  # Default to pending if null
                'item_count': receipt[9]
            }
            receipts_list.append(receipt_data)
            
            # Calculate statistics
            total_revenue += receipt_data['total_amount']
            if receipt[6] and receipt[6].date() == today:
                today_receipts_count += 1
        
        connection.close()
        
        # Get employee information for the template
        hotel_settings = get_hotel_settings()
        employee_profile_photo = get_employee_profile_photo(session.get('employee_id'))
        
        return render_template('receipts.html', 
                             receipts=receipts_list, 
                             total_receipts=len(receipts_list),
                             total_revenue=total_revenue,
                             today_receipts=today_receipts_count,
                             hotel_settings=hotel_settings,
                             employee_name=session.get('employee_name'),
                             employee_role=session.get('employee_role'),
                             employee_profile_photo=employee_profile_photo)
        
    except Exception as e:
        print(f"Error fetching receipts: {e}")
        return render_template('receipts.html', receipts=[], error="Error loading receipts")

@app.route('/api/hotel-settings', methods=['GET'])
def get_hotel_settings():
    """Get hotel settings"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM hotel_settings ORDER BY id DESC LIMIT 1")
            settings = cursor.fetchone()
            
            if settings:
                return jsonify({
                    'success': True,
                    'hotel_name': settings[1],
                    'company_email': settings[2],
                    'company_phone': settings[3],
                    'hotel_address': settings[4],
                    'business_type': settings[5] if len(settings) > 5 else '',
                    'payment_method': settings[6] if len(settings) > 6 else 'buy_goods',
                    'till_number': settings[7] if len(settings) > 7 else '',
                    'business_number': settings[8] if len(settings) > 8 else '',
                    'account_number': settings[9] if len(settings) > 9 else ''
                })
            else:
                return jsonify({
                    'success': True,
                    'hotel_name': '',
                    'company_email': '',
                    'company_phone': '',
                    'hotel_address': '',
                    'business_type': '',
                    'payment_method': 'buy_goods',
                    'till_number': '',
                    'business_number': '',
                    'account_number': ''
                })
    except Exception as e:
        print(f"Error fetching hotel settings: {e}")
        return jsonify({'success': False, 'message': 'Error fetching settings'}), 500
    finally:
        connection.close()

@app.route('/api/pos/hotel-settings', methods=['GET'])
def get_pos_hotel_settings():
    """Get hotel settings for POS (public endpoint)"""
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM hotel_settings ORDER BY id DESC LIMIT 1")
            settings = cursor.fetchone()
            
            if settings:
                return jsonify({
                    'success': True,
                    'hotel_name': settings[1],
                    'company_email': settings[2],
                    'company_phone': settings[3],
                    'hotel_address': settings[4],
                    'business_type': settings[5] if len(settings) > 5 else '',
                    'payment_method': settings[6] if len(settings) > 6 else 'buy_goods',
                    'till_number': settings[7] if len(settings) > 7 else '',
                    'business_number': settings[8] if len(settings) > 8 else '',
                    'account_number': settings[9] if len(settings) > 9 else ''
                })
            else:
                return jsonify({
                    'success': True,
                    'hotel_name': 'Hotel POS',
                    'company_email': '',
                    'company_phone': '',
                    'hotel_address': '',
                    'business_type': '',
                    'payment_method': 'buy_goods',
                    'till_number': '',
                    'business_number': '',
                    'account_number': ''
                })
    except Exception as e:
        print(f"Error fetching hotel settings for POS: {e}")
        return jsonify({'success': False, 'message': 'Error fetching settings'}), 500
    finally:
        connection.close()

@app.route('/api/manager/dashboard-data', methods=['POST'])
def api_manager_dashboard_data():
    """API endpoint for manager dashboard data"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        data_type = data.get('dataType', 'general')  # 'general' or 'verified'
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        with connection.cursor() as cursor:
            # Build WHERE clause based on data type
            where_conditions = []
            if data_type == 'verified':
                where_conditions.append("s.status = 'confirmed'")
            # 'general' includes all statuses (pending, confirmed, cancelled)
            
            # Always add the date condition for last 12 months
            where_conditions.append("s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)")
            where_clause = "WHERE " + " AND ".join(where_conditions)
            
            # Get total sales count
            sales_query = f"SELECT COUNT(*) FROM sales s {where_clause}"
            cursor.execute(sales_query)
            total_sales = cursor.fetchone()[0]
            
            # Get total quantity sold
            quantity_query = f"""
                SELECT COALESCE(SUM(si.quantity), 0) 
                FROM sales s 
                LEFT JOIN sales_items si ON s.id = si.sale_id 
                {where_clause}
            """
            cursor.execute(quantity_query)
            total_quantity = cursor.fetchone()[0]
            
            # Get total revenue
            revenue_query = f"""
                SELECT COALESCE(SUM(si.total_price), 0) 
                FROM sales s 
                LEFT JOIN sales_items si ON s.id = si.sale_id 
                {where_clause}
            """
            cursor.execute(revenue_query)
            total_revenue = cursor.fetchone()[0]
            
            # Get active employees count
            employees_query = "SELECT COUNT(*) FROM employees WHERE status = 'active'"
            cursor.execute(employees_query)
            active_employees = cursor.fetchone()[0]
            
            # Get total items count
            items_query = "SELECT COUNT(*) FROM items WHERE status = 'active'"
            cursor.execute(items_query)
            total_items = cursor.fetchone()[0]
            
            # Get today's summary data
            today = datetime.now().strftime('%Y-%m-%d')
            today_where = where_clause.replace("s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)", f"s.sale_date >= '{today}'")
            
            # Today's transactions
            today_sales_query = f"SELECT COUNT(*) FROM sales s {today_where}"
            cursor.execute(today_sales_query)
            today_transactions = cursor.fetchone()[0]
            
            # Today's quantity sold
            today_quantity_query = f"""
                SELECT COALESCE(SUM(si.quantity), 0) 
                FROM sales s 
                LEFT JOIN sales_items si ON s.id = si.sale_id 
                {today_where}
            """
            cursor.execute(today_quantity_query)
            today_quantity = cursor.fetchone()[0]
            
            # Today's revenue
            today_revenue_query = f"""
                SELECT COALESCE(SUM(si.total_price), 0) 
                FROM sales s 
                LEFT JOIN sales_items si ON s.id = si.sale_id 
                {today_where}
            """
            cursor.execute(today_revenue_query)
            today_revenue = cursor.fetchone()[0]
            
            return jsonify({
                'success': True,
                'data': {
                    'totalSales': total_sales,
                    'totalQuantity': total_quantity,
                    'totalRevenue': total_revenue,
                    'activeEmployees': active_employees,
                    'totalItems': total_items,
                    'todayTransactions': today_transactions,
                    'todayQuantity': today_quantity,
                    'todayRevenue': today_revenue,
                    'dataType': data_type
                }
            })
            
    except Exception as e:
        print(f"Error fetching dashboard data: {e}")
        return jsonify({'success': False, 'message': 'Error fetching dashboard data'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/manager/today-time-trend', methods=['POST'])
def api_manager_today_time_trend():
    """API endpoint for today's hourly sales trend data"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        data_type = data.get('dataType', 'general')  # 'general' or 'verified'
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        with connection.cursor() as cursor:
            # Build WHERE clause based on data type
            where_conditions = []
            if data_type == 'verified':
                where_conditions.append("s.status = 'confirmed'")
            # 'general' includes all statuses (pending, confirmed, cancelled)
            
            # Always add today's date condition
            today = datetime.now().strftime('%Y-%m-%d')
            where_conditions.append(f"DATE(s.sale_date) = '{today}'")
            where_clause = "WHERE " + " AND ".join(where_conditions)
            
            # Get hourly sales data for today
            hourly_query = f"""
                SELECT 
                    HOUR(s.sale_date) as hour,
                    COALESCE(SUM(si.quantity), 0) as total_quantity,
                    COUNT(DISTINCT s.id) as transaction_count
                FROM sales s
                LEFT JOIN sales_items si ON s.id = si.sale_id
                {where_clause}
                GROUP BY HOUR(s.sale_date)
                ORDER BY hour ASC
            """
            
            print(f"Executing hourly query: {hourly_query}")
            cursor.execute(hourly_query)
            hourly_data = cursor.fetchall()
            print(f"Hourly data fetched: {hourly_data}")
            
            # Create a complete 24-hour dataset
            chart_data = {
                'labels': [],
                'quantities': [],
                'transactions': []
            }
            
            # Create hourly data for all 24 hours
            hourly_dict = {row[0]: {'quantity': row[1], 'transactions': row[2]} for row in hourly_data}
            
            for hour in range(24):
                hour_label = f"{hour:02d}:00"
                chart_data['labels'].append(hour_label)
                chart_data['quantities'].append(hourly_dict.get(hour, {'quantity': 0})['quantity'])
                chart_data['transactions'].append(hourly_dict.get(hour, {'transactions': 0})['transactions'])
            
            # Get today's summary statistics using simple approach
            try:
                # Get total quantity and transactions from the hourly data we already have
                total_quantity = sum(row[1] for row in hourly_data)
                total_transactions = sum(row[2] for row in hourly_data)
                
                # Calculate averages from hourly data
                if hourly_data:
                    avg_hourly_quantity = total_quantity / len(hourly_data) if hourly_data else 0
                    peak_hourly_quantity = max(row[1] for row in hourly_data) if hourly_data else 0
                    
                    # Find peak hour
                    peak_hour_row = max(hourly_data, key=lambda x: x[1])
                    peak_hour = peak_hour_row[0]
                else:
                    avg_hourly_quantity = 0
                    peak_hourly_quantity = 0
                    peak_hour = None
                
                summary_data = (total_quantity, total_transactions, avg_hourly_quantity, peak_hourly_quantity)
                peak_hour_data = (peak_hour, peak_hourly_quantity) if peak_hour is not None else None
                
                print(f"Summary data calculated from hourly data: {summary_data}")
                print(f"Peak hour data: {peak_hour_data}")
                
            except Exception as e:
                print(f"Error calculating summary from hourly data: {e}")
                summary_data = (0, 0, 0, 0)
                peak_hour_data = None
            
            # Prepare summary data
            summary = {
                'totalQuantity': summary_data[0] if summary_data and summary_data[0] else 0,
                'totalTransactions': summary_data[1] if summary_data and summary_data[1] else 0,
                'averageHourly': round(summary_data[2] if summary_data and summary_data[2] else 0, 1),
                'peakHourly': summary_data[3] if summary_data and summary_data[3] else 0,
                'peakHour': f"{peak_hour_data[0]:02d}:00" if peak_hour_data and peak_hour_data[0] is not None else "No data"
            }
            
            return jsonify({
                'success': True,
                'chartData': chart_data,
                'summary': summary
            })
            
    except Exception as e:
        print(f"Error fetching today's time trend data: {e}")
        return jsonify({'success': False, 'message': 'Error fetching time trend data'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/manager/monthly-trend', methods=['POST'])
def api_manager_monthly_trend():
    """API endpoint for monthly sales trend data"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        data_type = data.get('dataType', 'general')  # 'general' or 'verified'
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        with connection.cursor() as cursor:
            # First, let's check if we have any sales data
            cursor.execute("SELECT COUNT(*) FROM sales")
            sales_count = cursor.fetchone()[0]
            print(f"Total sales in database: {sales_count}")
            
            cursor.execute("SELECT COUNT(*) FROM sales_items")
            sales_items_count = cursor.fetchone()[0]
            print(f"Total sales_items in database: {sales_items_count}")
            
            # Test the exact column reference that's failing
            try:
                cursor.execute("SELECT si.quantity FROM sales_items si LIMIT 1")
                test_result = cursor.fetchone()
                print(f"Test si.quantity query successful: {test_result}")
            except Exception as e:
                print(f"Test si.quantity query failed: {e}")
            
            # Check current database
            cursor.execute("SELECT DATABASE()")
            current_db = cursor.fetchone()[0]
            print(f"Current database: {current_db}")
            
            # Check if sales_items table exists in current database
            cursor.execute("SHOW TABLES LIKE 'sales_items'")
            table_exists = cursor.fetchone()
            print(f"sales_items table exists: {table_exists is not None}")
            
            # Build WHERE clause based on data type
            where_conditions = []
            if data_type == 'verified':
                where_conditions.append("s.status = 'confirmed'")
            # 'general' includes all statuses (pending, confirmed, cancelled)
            
            # Always add the date condition
            where_conditions.append("s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)")
            
            where_clause = "WHERE " + " AND ".join(where_conditions)
            print(f"WHERE clause: {where_clause}")
            
            # Get monthly sales data for the last 12 months - simplified to match working test query
            monthly_query = f"""
                SELECT 
                    DATE_FORMAT(s.sale_date, '%Y-%m') as month,
                    SUM(si.quantity) as total_quantity
                FROM sales s
                LEFT JOIN sales_items si ON s.id = si.sale_id
                {where_clause}
                GROUP BY DATE_FORMAT(s.sale_date, '%Y-%m')
                ORDER BY month ASC
            """
            
            print(f"Executing monthly query: {monthly_query}")
            
            # Let's test the connection and tables first
            cursor.execute("SELECT DATABASE()")
            current_db = cursor.fetchone()[0]
            print(f"Current database in Flask: {current_db}")
            
            cursor.execute("SHOW TABLES LIKE 'sales_items'")
            table_exists = cursor.fetchone()
            print(f"sales_items table exists in Flask: {table_exists is not None}")
            
            # Test a simple join first
            try:
                cursor.execute("SELECT s.id, si.quantity FROM sales s LEFT JOIN sales_items si ON s.id = si.sale_id LIMIT 1")
                simple_join_result = cursor.fetchone()
                print(f"Simple join test in Flask: {simple_join_result}")
            except Exception as e:
                print(f"Simple join test failed in Flask: {e}")
            
            # Test the exact same structure but with aggregation
            try:
                test_agg_query = """
                    SELECT 
                        DATE_FORMAT(s.sale_date, '%Y-%m') as month,
                        SUM(si.quantity) as total_quantity
                    FROM sales s
                    LEFT JOIN sales_items si ON s.id = si.sale_id
                    WHERE s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
                    GROUP BY DATE_FORMAT(s.sale_date, '%Y-%m')
                    LIMIT 1
                """
                cursor.execute(test_agg_query)
                test_agg_result = cursor.fetchone()
                print(f"Test aggregation query result: {test_agg_result}")
            except Exception as e:
                print(f"Test aggregation query failed: {e}")
            
            # Use the exact same query that works in the test
            working_query = """
                SELECT 
                    DATE_FORMAT(s.sale_date, '%Y-%m') as month,
                    SUM(si.quantity) as total_quantity
                FROM sales s
                LEFT JOIN sales_items si ON s.id = si.sale_id
                WHERE s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
                GROUP BY DATE_FORMAT(s.sale_date, '%Y-%m')
                ORDER BY month ASC
            """
            print(f"Using working query instead of monthly_query")
            cursor.execute(working_query)
            monthly_data = cursor.fetchall()
            print(f"Monthly data fetched successfully: {monthly_data}")
            
            # Get summary statistics using simple approach
            try:
                summary_query = """
                    SELECT 
                        SUM(si.quantity) as total_quantity
                    FROM sales s
                    LEFT JOIN sales_items si ON s.id = si.sale_id
                    WHERE s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
                """
                cursor.execute(summary_query)
                total_result = cursor.fetchone()
                total_quantity = total_result[0] if total_result and total_result[0] else 0
                
                # Calculate average from the monthly data we already have
                if monthly_data:
                    monthly_quantities = [row[1] for row in monthly_data if row[1]]
                    average_monthly = sum(monthly_quantities) / len(monthly_quantities) if monthly_quantities else 0
                    max_monthly = max(monthly_quantities) if monthly_quantities else 0
                else:
                    average_monthly = 0
                    max_monthly = 0
                
                summary_data = (total_quantity, average_monthly, max_monthly)
                print(f"Summary data calculated: {summary_data}")
            except Exception as e:
                print(f"Error calculating summary: {e}")
                summary_data = (0, 0, 0)
            
            # Get the best month name from the monthly data we already have
            try:
                if monthly_data:
                    # Find the month with the highest quantity
                    best_month_row = max(monthly_data, key=lambda x: x[1])
                    best_month_key = best_month_row[0]  # e.g., '2025-09'
                    best_quantity = best_month_row[1]
                    
                    # Convert month key to readable format
                    from datetime import datetime, timedelta
                    month_date = datetime.strptime(best_month_key, '%Y-%m')
                    best_month_name = month_date.strftime('%B %Y')  # e.g., 'September 2025'
                    best_month_data = (best_month_name, best_quantity)
                else:
                    best_month_data = None
                print(f"Best month data: {best_month_data}")
            except Exception as e:
                print(f"Error calculating best month: {e}")
                best_month_data = None
            
            # Prepare chart data
            chart_data = {
                'labels': [],
                'quantities': []
            }
            
            # Create a complete 12-month dataset
            from datetime import datetime, timedelta, timedelta
            import calendar
            
            current_date = datetime.now()
            for i in range(12):
                month_date = current_date - timedelta(days=30*i)
                month_key = month_date.strftime('%Y-%m')
                month_name = calendar.month_name[month_date.month][:3]  # Short month name
                
                # Find data for this month (simplified query returns only 2 columns: month, total_quantity)
                month_quantity = 0
                for row in monthly_data:
                    if row[0] == month_key:
                        month_quantity = row[1]  # Changed from row[3] to row[1]
                        break
                
                chart_data['labels'].insert(0, f"{month_name} {month_date.year}")
                chart_data['quantities'].insert(0, month_quantity)
            
            # Prepare summary data
            summary = {
                'totalQuantity': summary_data[0] if summary_data and summary_data[0] else 0,
                'averageMonthly': round(summary_data[1] if summary_data and summary_data[1] else 0, 1),
                'bestMonth': f"{best_month_data[0]} {best_month_data[1]}" if best_month_data and best_month_data[0] else "No data"
            }
            
            return jsonify({
                'success': True,
                'chartData': chart_data,
                'summary': summary
            })
            
    except Exception as e:
        print(f"Error fetching monthly trend data: {e}")
        return jsonify({'success': False, 'message': 'Error fetching monthly trend data'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/hotel-settings', methods=['POST'])
def save_hotel_settings():
    """Save hotel settings"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    
    # Validate required fields
    required_fields = ['hotel_name', 'company_email', 'company_phone', 'payment_method']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'success': False, 'message': f'{field} is required'}), 400
    
    # Validate payment method specific fields
    if data['payment_method'] == 'buy_goods' and not data.get('till_number'):
        return jsonify({'success': False, 'message': 'Till number is required for buy goods payment method'}), 400
    
    if data['payment_method'] == 'paybill':
        if not data.get('business_number') or not data.get('account_number'):
            return jsonify({'success': False, 'message': 'Business number and account number are required for paybill payment method'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if settings already exist
            cursor.execute("SELECT id FROM hotel_settings ORDER BY id DESC LIMIT 1")
            existing_settings = cursor.fetchone()
            
            if existing_settings:
                # Update existing settings
                cursor.execute("""
                    UPDATE hotel_settings SET
                        hotel_name = %s,
                        company_email = %s,
                        company_phone = %s,
                        hotel_address = %s,
                        business_type = %s,
                        payment_method = %s,
                        till_number = %s,
                        business_number = %s,
                        account_number = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    data['hotel_name'],
                    data['company_email'],
                    data['company_phone'],
                    data.get('hotel_address', ''),
                    data.get('business_type', ''),
                    data['payment_method'],
                    data.get('till_number', ''),
                    data.get('business_number', ''),
                    data.get('account_number', ''),
                    existing_settings[0]
                ))
            else:
                # Insert new settings
                cursor.execute("""
                    INSERT INTO hotel_settings (
                        hotel_name, company_email, company_phone, hotel_address,
                        business_type, payment_method, till_number, business_number, account_number
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    data['hotel_name'],
                    data['company_email'],
                    data['company_phone'],
                    data.get('hotel_address', ''),
                    data.get('business_type', ''),
                    data['payment_method'],
                    data.get('till_number', ''),
                    data.get('business_number', ''),
                    data.get('account_number', '')
                ))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Hotel settings saved successfully'})
            
    except Exception as e:
        print(f"Error saving hotel settings: {e}")
        return jsonify({'success': False, 'message': 'Error saving settings'}), 500
    finally:
        connection.close()

# Printing Settings API Endpoints
@app.route('/api/printing-settings', methods=['GET'])
def get_printing_settings():
    """Get printing settings from hotel_settings table"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT double_print, show_till, include_tax, show_images 
                FROM hotel_settings 
                ORDER BY id DESC 
                LIMIT 1
            """)
            result = cursor.fetchone()
            
            if result:
                settings = {
                    'double_print': bool(result[0]),
                    'show_till': bool(result[1]),
                    'include_tax': bool(result[2]),
                    'show_images': bool(result[3])
                }
            else:
                # Default values if no settings found
                settings = {
                    'double_print': False,
                    'show_till': True,
                    'include_tax': True,
                    'show_images': True
                }
            
            return jsonify({'success': True, 'settings': settings})
            
    except Exception as e:
        print(f"Error getting printing settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to get printing settings'}), 500
    finally:
        connection.close()

@app.route('/api/printing-settings', methods=['POST'])
def save_printing_settings():
    """Save printing settings"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if hotel_settings record exists
            cursor.execute("SELECT id FROM hotel_settings ORDER BY id DESC LIMIT 1")
            result = cursor.fetchone()
            
            if result:
                # Update existing record
                cursor.execute("""
                    UPDATE hotel_settings 
                    SET double_print = %s, show_till = %s, include_tax = %s, show_images = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    bool(data.get('double_print', False)),
                    bool(data.get('show_till', True)),
                    bool(data.get('include_tax', True)),
                    bool(data.get('show_images', True)),
                    result[0]
                ))
            else:
                # Insert new record with default values
                cursor.execute("""
                    INSERT INTO hotel_settings (
                        hotel_name, company_email, company_phone, hotel_address,
                        double_print, show_till, include_tax, show_images
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    'Hotel POS', '', '', '',
                    bool(data.get('double_print', False)),
                    bool(data.get('show_till', True)),
                    bool(data.get('include_tax', True)),
                    bool(data.get('show_images', True))
                ))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Printing settings saved successfully'})
            
    except Exception as e:
        print(f"Error saving printing settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to save printing settings'}), 500
    finally:
        connection.close()
# Permissions Settings API Endpoints
@app.route('/api/permissions-settings', methods=['GET'])
def get_permissions_settings():
    """Get permissions settings from hotel_settings table"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT enable_receipt_status_update
                FROM hotel_settings 
                ORDER BY id DESC 
                LIMIT 1
            """)
            result = cursor.fetchone()
            
            settings = {
                'enable_receipt_status_update': result[0] if result else True
            }
            
            return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        print(f"Error getting permissions settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to get permissions settings'}), 500
    finally:
        connection.close()

@app.route('/api/permissions-settings', methods=['POST'])
def save_permissions_settings():
    """Save permissions settings"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if hotel_settings record exists
            cursor.execute("SELECT id FROM hotel_settings ORDER BY id DESC LIMIT 1")
            result = cursor.fetchone()
            
            if result:
                # Update existing record
                cursor.execute("""
                    UPDATE hotel_settings 
                    SET enable_receipt_status_update = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    bool(data.get('enable_receipt_status_update', True)),
                    result[0]
                ))
            else:
                # Insert new record with default values
                cursor.execute("""
                    INSERT INTO hotel_settings (
                        hotel_name, company_email, company_phone, hotel_address,
                        enable_receipt_status_update
                    ) VALUES (%s, %s, %s, %s, %s)
                """, (
                    'Hotel POS', '', '', '',
                    bool(data.get('enable_receipt_status_update', True))
                ))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Permissions settings saved successfully'})
            
    except Exception as e:
        print(f"Error saving permissions settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to save permissions settings'}), 500
    finally:
        connection.close()

# Receipt Reset by Date API Endpoints
@app.route('/api/receipts/count-by-date', methods=['GET'])
def count_receipts_by_date():
    """Get count of receipts for a specific date"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    selected_date = request.args.get('date')
    if not selected_date:
        return jsonify({'success': False, 'message': 'Date is required'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Get total count and status breakdown for the selected date
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_count,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_count,
                    SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END) as confirmed_count,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled_count
                FROM sales 
                WHERE DATE(sale_date) = %s
            """, (selected_date,))
            
            result = cursor.fetchone()
            total_count = result[0] if result else 0
            pending_count = result[1] if result and result[1] else 0
            confirmed_count = result[2] if result and result[2] else 0
            cancelled_count = result[3] if result and result[3] else 0
            
            return jsonify({
                'success': True,
                'total_count': total_count,
                'pending_count': int(pending_count),
                'confirmed_count': int(confirmed_count),
                'cancelled_count': int(cancelled_count)
            })
    except Exception as e:
        print(f"Error counting receipts by date: {e}")
        return jsonify({'success': False, 'message': 'Failed to count receipts'}), 500
    finally:
        connection.close()

@app.route('/api/receipts/reset-status-by-date', methods=['POST'])
def reset_receipt_status_by_date():
    """Reset receipt status to 'pending' for all receipts on a specific date"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data or 'date' not in data:
        return jsonify({'success': False, 'message': 'Date is required'}), 400
    
    selected_date = data.get('date')
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Update all receipts for the selected date to 'pending' status
            cursor.execute("""
                UPDATE sales 
                SET status = 'pending'
                WHERE DATE(sale_date) = %s
            """, (selected_date,))
            
            updated_count = cursor.rowcount
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': f'Successfully reset {updated_count} receipt(s) status to "Pending" for {selected_date}',
                'updated_count': updated_count
            })
    except Exception as e:
        print(f"Error resetting receipt status by date: {e}")
        connection.rollback()
        return jsonify({'success': False, 'message': 'Failed to reset receipt status'}), 500
    finally:
        connection.close()

@app.route('/api/receipts/reset-cashier-confirmation-by-date', methods=['POST'])
def reset_cashier_confirmation_by_date():
    """Reset cashier_confirmed to 0 for all receipts on a specific date"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data or 'date' not in data:
        return jsonify({'success': False, 'message': 'Date is required'}), 400
    
    selected_date = data.get('date')
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Update all receipts for the selected date to reset cashier_confirmed to 0
            cursor.execute("""
                UPDATE sales 
                SET cashier_confirmed = 0
                WHERE DATE(sale_date) = %s
            """, (selected_date,))
            
            updated_count = cursor.rowcount
            connection.commit()
            
            return jsonify({
                'success': True,
                'message': f'Successfully reset cashier confirmation for {updated_count} receipt(s) for {selected_date}',
                'updated_count': updated_count
            })
    except Exception as e:
        print(f"Error resetting cashier confirmation by date: {e}")
        connection.rollback()
        return jsonify({'success': False, 'message': 'Failed to reset cashier confirmation'}), 500
    finally:
        connection.close()

# Display Settings API Endpoints
@app.route('/api/display-settings', methods=['GET'])
def get_display_settings():
    """Get display settings from hotel_settings table"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT show_till, include_tax, show_images, double_print 
                FROM hotel_settings 
                ORDER BY id DESC 
                LIMIT 1
            """)
            result = cursor.fetchone()
            
            if result:
                settings = {
                    'show_till': bool(result[0]),
                    'include_tax': bool(result[1]),
                    'show_images': bool(result[2]),
                    'double_print': bool(result[3])
                }
            else:
                # Default values if no settings found
                settings = {
                    'show_till': True,
                    'include_tax': True,
                    'show_images': True,
                    'double_print': False
                }
            
            return jsonify({'success': True, 'settings': settings})
            
    except Exception as e:
        print(f"Error getting display settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to get display settings'}), 500
    finally:
        connection.close()

@app.route('/api/display-settings', methods=['POST'])
def save_display_settings():
    """Save display settings"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if hotel_settings record exists
            cursor.execute("SELECT id FROM hotel_settings ORDER BY id DESC LIMIT 1")
            result = cursor.fetchone()
            
            if result:
                # Update existing record
                cursor.execute("""
                    UPDATE hotel_settings 
                    SET show_till = %s, include_tax = %s, show_images = %s, double_print = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    bool(data.get('show_till', True)),
                    bool(data.get('include_tax', True)),
                    bool(data.get('show_images', True)),
                    bool(data.get('double_print', False)),
                    result[0]
                ))
            else:
                # Insert new record with default values
                cursor.execute("""
                    INSERT INTO hotel_settings (
                        hotel_name, company_email, company_phone, hotel_address,
                        show_till, include_tax, show_images, double_print
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    'Hotel POS', '', '', '',
                    bool(data.get('show_till', True)),
                    bool(data.get('include_tax', True)),
                    bool(data.get('show_images', True)),
                    bool(data.get('double_print', False))
                ))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Display settings saved successfully'})
            
    except Exception as e:
        print(f"Error saving display settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to save display settings'}), 500
    finally:
        connection.close()

@app.route('/api/receipt-settings', methods=['GET'])
def get_receipt_settings():
    """Get receipt settings from hotel_settings table"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT receipt_width, receipt_font_size, receipt_bold_headers, receipt_number_format,
                       receipt_number_prefix, receipt_starting_number, receipt_header_title,
                       receipt_header_subtitle, receipt_header_message, receipt_show_logo,
                       receipt_show_address, receipt_show_contact, receipt_footer_message,
                       receipt_show_datetime, receipt_show_cashier, 
                       receipt_show_qr, receipt_address, receipt_phone, receipt_email, receipt_logo_url
                FROM hotel_settings 
                ORDER BY id DESC 
                LIMIT 1
            """)
            result = cursor.fetchone()
            
            if result:
                settings = {
                    'receipt_width': result[0] or '58mm',
                    'receipt_font_size': result[1] or 'medium',
                    'receipt_bold_headers': bool(result[2]),
                    'receipt_number_format': result[3] or 'sequential',
                    'receipt_number_prefix': result[4] or 'POS',
                    'receipt_starting_number': result[5] or 1001,
                    'receipt_header_title': result[6] or '',
                    'receipt_header_subtitle': result[7] or '',
                    'receipt_header_message': result[8] or '',
                    'receipt_show_logo': bool(result[9]),
                    'receipt_show_address': bool(result[10]),
                    'receipt_show_contact': bool(result[11]),
                    'receipt_footer_message': result[12] or '',
                    'receipt_show_datetime': bool(result[13]),
                    'receipt_show_cashier': bool(result[14]),
                    'receipt_show_qr': bool(result[15]) if len(result) > 15 else False,
                    'receipt_address': result[16] or '' if len(result) > 16 else '',
                    'receipt_phone': result[17] or '' if len(result) > 17 else '',
                    'receipt_email': result[18] or '' if len(result) > 18 else '',
                    'receipt_logo_url': result[19] or '' if len(result) > 19 else ''
                }
            else:
                # Default values if no settings found
                settings = {
                    'receipt_width': '58mm',
                    'receipt_font_size': 'medium',
                    'receipt_bold_headers': True,
                    'receipt_number_format': 'sequential',
                    'receipt_number_prefix': 'POS',
                    'receipt_starting_number': 1001,
                    'receipt_header_title': '',
                    'receipt_header_subtitle': '',
                    'receipt_header_message': '',
                    'receipt_show_logo': False,
                    'receipt_show_address': True,
                    'receipt_show_contact': True,
                    'receipt_footer_message': '',
                    'receipt_show_datetime': True,
                    'receipt_show_cashier': True,
                    'receipt_qr_text': '',
                    'receipt_show_qr': False,
                    'receipt_address': '',
                    'receipt_phone': '',
                    'receipt_email': '',
                    'receipt_logo_url': ''
                }
            
            return jsonify({'success': True, 'settings': settings})
            
    except Exception as e:
        print(f"Error getting receipt settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to get receipt settings'}), 500
    finally:
        connection.close()

@app.route('/api/receipt-settings', methods=['POST'])
def save_receipt_settings():
    """Save receipt settings"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'No data provided'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Check if hotel_settings record exists
            cursor.execute("SELECT id FROM hotel_settings ORDER BY id DESC LIMIT 1")
            result = cursor.fetchone()
            
            if result:
                # Update existing record
                cursor.execute("""
                    UPDATE hotel_settings 
                    SET receipt_width = %s, receipt_font_size = %s, receipt_bold_headers = %s,
                        receipt_number_format = %s, receipt_number_prefix = %s, receipt_starting_number = %s,
                        receipt_header_title = %s, receipt_header_subtitle = %s, receipt_header_message = %s,
                        receipt_show_logo = %s, receipt_show_address = %s, receipt_show_contact = %s,
                        receipt_footer_message = %s, receipt_show_datetime = %s, receipt_show_cashier = %s,
                        receipt_qr_text = %s, receipt_show_qr = %s, receipt_address = %s,
                        receipt_phone = %s, receipt_email = %s, receipt_logo_url = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    data.get('receipt_width', '58mm'),
                    data.get('receipt_font_size', 'medium'),
                    bool(data.get('receipt_bold_headers', True)),
                    data.get('receipt_number_format', 'sequential'),
                    data.get('receipt_number_prefix', 'POS'),
                    int(data.get('receipt_starting_number', 1001)),
                    data.get('receipt_header_title', ''),
                    data.get('receipt_header_subtitle', ''),
                    data.get('receipt_header_message', ''),
                    bool(data.get('receipt_show_logo', False)),
                    bool(data.get('receipt_show_address', True)),
                    bool(data.get('receipt_show_contact', True)),
                    data.get('receipt_footer_message', ''),
                    bool(data.get('receipt_show_datetime', True)),
                    bool(data.get('receipt_show_cashier', True)),
                    data.get('receipt_qr_text', ''),
                    bool(data.get('receipt_show_qr', False)),
                    data.get('receipt_address', ''),
                    data.get('receipt_phone', ''),
                    data.get('receipt_email', ''),
                    data.get('receipt_logo_url', ''),
                    result[0]
                ))
            else:
                # Insert new record with default values
                cursor.execute("""
                    INSERT INTO hotel_settings (
                        hotel_name, company_email, company_phone, hotel_address,
                        receipt_width, receipt_font_size, receipt_bold_headers, receipt_number_format,
                        receipt_number_prefix, receipt_starting_number, receipt_header_title,
                        receipt_header_subtitle, receipt_header_message, receipt_show_logo,
                        receipt_show_address, receipt_show_contact, receipt_footer_message,
                        receipt_show_datetime, receipt_show_cashier, receipt_qr_text, 
                        receipt_show_qr, receipt_address, receipt_phone, receipt_email, receipt_logo_url
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    'Hotel POS', '', '', '',
                    data.get('receipt_width', '58mm'),
                    data.get('receipt_font_size', 'medium'),
                    bool(data.get('receipt_bold_headers', True)),
                    data.get('receipt_number_format', 'sequential'),
                    data.get('receipt_number_prefix', 'POS'),
                    int(data.get('receipt_starting_number', 1001)),
                    data.get('receipt_header_title', ''),
                    data.get('receipt_header_subtitle', ''),
                    data.get('receipt_header_message', ''),
                    bool(data.get('receipt_show_logo', False)),
                    bool(data.get('receipt_show_address', True)),
                    bool(data.get('receipt_show_contact', True)),
                    data.get('receipt_footer_message', ''),
                    bool(data.get('receipt_show_datetime', True)),
                    bool(data.get('receipt_show_cashier', True)),
                    data.get('receipt_qr_text', ''),
                    bool(data.get('receipt_show_qr', False)),
                    data.get('receipt_address', ''),
                    data.get('receipt_phone', ''),
                    data.get('receipt_email', ''),
                    data.get('receipt_logo_url', '')
                ))
            
            connection.commit()
            return jsonify({'success': True, 'message': 'Receipt settings saved successfully'})
            
    except Exception as e:
        print(f"Error saving receipt settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to save receipt settings'}), 500
    finally:
        connection.close()

@app.route('/api/receipt-logo/upload', methods=['POST'])
def upload_receipt_logo():
    """Upload receipt logo"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    if 'logo' not in request.files:
        return jsonify({'success': False, 'message': 'No logo file provided'}), 400
    
    file = request.files['logo']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        try:
            # Generate unique filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
            filename = secure_filename(f"receipt_logo_{timestamp}{file.filename}")
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Return the URL path
            logo_url = f'/static/uploads/{filename}'
            return jsonify({'success': True, 'logo_url': logo_url})
            
        except Exception as e:
            print(f"Error uploading logo: {e}")
            return jsonify({'success': False, 'message': 'Failed to upload logo'}), 500
    else:
        return jsonify({'success': False, 'message': 'Invalid file type. Please upload an image file.'}), 400

@app.route('/api/receipt-logo/remove', methods=['POST'])
def remove_receipt_logo():
    """Remove receipt logo"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    data = request.get_json()
    if not data or not data.get('remove'):
        return jsonify({'success': False, 'message': 'Invalid request'}), 400
    
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            # Clear logo URL from database
            cursor.execute("""
                UPDATE hotel_settings 
                SET receipt_logo_url = NULL 
                WHERE id = (SELECT id FROM hotel_settings ORDER BY id DESC LIMIT 1)
            """)
            connection.commit()
            return jsonify({'success': True, 'message': 'Logo removed successfully'})
    except Exception as e:
        print(f"Error removing logo: {e}")
        return jsonify({'success': False, 'message': 'Failed to remove logo'}), 500
    finally:
        connection.close()

@app.route('/api/pos/receipt-settings', methods=['GET'])
def get_pos_receipt_settings():
    """Get receipt settings for POS (public endpoint)"""
    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection failed'}), 500
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT receipt_width, receipt_font_size, receipt_bold_headers, receipt_number_format,
                       receipt_number_prefix, receipt_starting_number, receipt_header_title,
                       receipt_header_subtitle, receipt_header_message, receipt_show_logo,
                       receipt_show_address, receipt_show_contact, receipt_footer_message,
                       receipt_show_datetime, receipt_show_cashier, 
                       receipt_show_qr, receipt_address, receipt_phone, receipt_email, receipt_logo_url
                FROM hotel_settings 
                ORDER BY id DESC 
                LIMIT 1
            """)
            result = cursor.fetchone()
            
            if result:
                settings = {
                    'receipt_width': result[0] or '58mm',
                    'receipt_font_size': result[1] or 'medium',
                    'receipt_bold_headers': bool(result[2]),
                    'receipt_number_format': result[3] or 'sequential',
                    'receipt_number_prefix': result[4] or 'POS',
                    'receipt_starting_number': result[5] or 1001,
                    'receipt_header_title': result[6] or '',
                    'receipt_header_subtitle': result[7] or '',
                    'receipt_header_message': result[8] or '',
                    'receipt_show_logo': bool(result[9]),
                    'receipt_show_address': bool(result[10]),
                    'receipt_show_contact': bool(result[11]),
                    'receipt_footer_message': result[12] or '',
                    'receipt_show_datetime': bool(result[13]),
                    'receipt_show_cashier': bool(result[14]),
                    'receipt_show_qr': bool(result[15]) if len(result) > 15 else False,
                    'receipt_address': result[16] or '' if len(result) > 16 else '',
                    'receipt_phone': result[17] or '' if len(result) > 17 else '',
                    'receipt_email': result[18] or '' if len(result) > 18 else '',
                    'receipt_logo_url': result[19] or '' if len(result) > 19 else ''
                }
            else:
                # Default values if no settings found
                settings = {
                    'receipt_width': '58mm',
                    'receipt_font_size': 'medium',
                    'receipt_bold_headers': True,
                    'receipt_number_format': 'sequential',
                    'receipt_number_prefix': 'POS',
                    'receipt_starting_number': 1001,
                    'receipt_header_title': '',
                    'receipt_header_subtitle': '',
                    'receipt_header_message': '',
                    'receipt_show_logo': False,
                    'receipt_show_address': True,
                    'receipt_show_contact': True,
                    'receipt_footer_message': '',
                    'receipt_show_datetime': True,
                    'receipt_show_cashier': True,
                    'receipt_qr_text': '',
                    'receipt_show_qr': False,
                    'receipt_address': '',
                    'receipt_phone': '',
                    'receipt_email': '',
                    'receipt_logo_url': ''
                }
            
            return jsonify({'success': True, 'settings': settings})
            
    except Exception as e:
        print(f"Error getting POS receipt settings: {e}")
        return jsonify({'success': False, 'message': 'Failed to get receipt settings'}), 500
    finally:
        connection.close()

# Live Analytics API Endpoints
@app.route('/api/admin/live-analytics', methods=['GET'])
def api_admin_live_analytics():
    """API endpoint for live analytics data"""
    # Temporarily disable authentication for testing
    # if 'employee_id' not in session or session.get('employee_role') != 'admin':
    #     return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    # Get data type from query parameter (default to 'general')
    data_type = request.args.get('dataType', 'general')
    
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        with connection.cursor() as cursor:
            # Build WHERE clause based on data type
            status_condition = "s.status = 'confirmed'" if data_type == 'verified' else "s.status IN ('pending', 'confirmed', 'cancelled')"
            
            # Get live revenue (today's total)
            cursor.execute(f"""
                SELECT COALESCE(SUM(si.total_price), 0) as live_revenue
                FROM sales s 
                LEFT JOIN sales_items si ON s.id = si.sale_id 
                WHERE DATE(s.sale_date) = CURDATE() AND {status_condition}
            """)
            live_revenue = cursor.fetchone()[0]
            
            # Get live transaction count (today's transactions)
            cursor.execute(f"""
                SELECT COUNT(*) as live_transactions
                FROM sales 
                WHERE DATE(sale_date) = CURDATE() AND {status_condition.replace('s.', '')}
            """)
            live_transactions = cursor.fetchone()[0]
            
            # Get live quantity sold (today's quantity)
            cursor.execute(f"""
                SELECT COALESCE(SUM(si.quantity), 0) as live_quantity
                FROM sales s 
                LEFT JOIN sales_items si ON s.id = si.sale_id 
                WHERE DATE(s.sale_date) = CURDATE() AND {status_condition}
            """)
            live_quantity = cursor.fetchone()[0]
            
            # Get best employee (highest sales today)
            cursor.execute(f"""
                SELECT e.full_name, COALESCE(SUM(si.total_price), 0) as total_sales
                FROM employees e
                LEFT JOIN sales s ON e.id = s.employee_id AND DATE(s.sale_date) = CURDATE() AND {status_condition}
                LEFT JOIN sales_items si ON s.id = si.sale_id
                WHERE e.status = 'active'
                GROUP BY e.id, e.full_name
                ORDER BY total_sales DESC
                LIMIT 1
            """)
            best_employee_result = cursor.fetchone()
            best_employee = {
                'name': best_employee_result[0] if best_employee_result and best_employee_result[0] else 'No sales today',
                'sales': float(best_employee_result[1]) if best_employee_result and best_employee_result[1] else 0
            }
            
            # Get worst employee (lowest sales today)
            cursor.execute(f"""
                SELECT e.full_name, COALESCE(SUM(si.total_price), 0) as total_sales
                FROM employees e
                LEFT JOIN sales s ON e.id = s.employee_id AND DATE(s.sale_date) = CURDATE() AND {status_condition}
                LEFT JOIN sales_items si ON s.id = si.sale_id
                WHERE e.status = 'active'
                GROUP BY e.id, e.full_name
                ORDER BY total_sales ASC
                LIMIT 1
            """)
            worst_employee_result = cursor.fetchone()
            worst_employee = {
                'name': worst_employee_result[0] if worst_employee_result and worst_employee_result[0] else 'No sales today',
                'sales': float(worst_employee_result[1]) if worst_employee_result and worst_employee_result[1] else 0
            }
            
            # Get low stock alerts
            cursor.execute("""
                SELECT name, stock, category
                FROM items 
                WHERE stock <= 10 AND status = 'active'
                ORDER BY stock ASC
            """)
            low_stock_items = []
            for row in cursor.fetchall():
                low_stock_items.append({
                    'name': row[0],
                    'stock': row[1],
                    'category': row[2]
                })
            
            # Get active employees count
            cursor.execute("""
                SELECT COUNT(DISTINCT e.id) as active_employees
                FROM employees e
                WHERE e.status = 'active'
            """)
            active_employees = cursor.fetchone()[0]
            
            # Get hourly sales trend for today
            cursor.execute(f"""
                SELECT HOUR(s.sale_date) as hour, 
                       COALESCE(SUM(si.quantity), 0) as quantity,
                       COUNT(DISTINCT s.id) as transactions
                FROM sales s 
                LEFT JOIN sales_items si ON s.id = si.sale_id 
                WHERE DATE(s.sale_date) = CURDATE() AND {status_condition}
                GROUP BY HOUR(s.sale_date)
                ORDER BY hour
            """)
            
            hourly_data = {}
            for row in cursor.fetchall():
                hourly_data[row[0]] = {
                    'quantity': row[1],
                    'transactions': row[2]
                }
            
            # Fill in missing hours with 0
            hourly_trend = []
            for hour in range(24):
                if hour in hourly_data:
                    hourly_trend.append({
                        'hour': hour,
                        'quantity': hourly_data[hour]['quantity'],
                        'transactions': hourly_data[hour]['transactions']
                    })
                else:
                    hourly_trend.append({
                        'hour': hour,
                        'quantity': 0,
                        'transactions': 0
                    })
            
            return jsonify({
                'success': True,
                'data': {
                    'live_revenue': float(live_revenue),
                    'live_transactions': live_transactions,
                    'live_quantity': live_quantity,
                    'active_employees': active_employees,
                    'best_employee': best_employee,
                    'worst_employee': worst_employee,
                    'low_stock_alerts': low_stock_items,
                    'hourly_trend': hourly_trend,
                    'timestamp': datetime.now().isoformat()
                }
            })
            
    except Exception as e:
        print(f"Error fetching live analytics: {e}")
        return jsonify({'success': False, 'message': 'Error fetching live analytics'}), 500
    finally:
        if connection:
            connection.close()
@app.route('/api/admin/live-sales-trend', methods=['GET'])
def api_admin_live_sales_trend():
    """API endpoint for live sales trend data"""
    # Temporarily disable authentication for testing
    # if 'employee_id' not in session or session.get('employee_role') != 'admin':
    #     return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        
        with connection.cursor() as cursor:
            # Get last 7 days sales trend
            cursor.execute("""
                SELECT DATE(s.sale_date) as date,
                       COALESCE(SUM(si.total_price), 0) as revenue,
                       COALESCE(SUM(si.quantity), 0) as quantity,
                       COUNT(DISTINCT s.id) as transactions
                FROM sales s 
                LEFT JOIN sales_items si ON s.id = si.sale_id 
                WHERE s.sale_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) 
                AND s.status = 'confirmed'
                GROUP BY DATE(s.sale_date)
                ORDER BY date
            """)
            
            daily_trend = []
            for row in cursor.fetchall():
                daily_trend.append({
                    'date': row[0].strftime('%Y-%m-%d'),
                    'revenue': float(row[1]),
                    'quantity': row[2],
                    'transactions': row[3]
                })
            
            return jsonify({
                'success': True,
                'data': {
                    'daily_trend': daily_trend,
                    'timestamp': datetime.now().isoformat()
                }
            })
            
    except Exception as e:
        print(f"Error fetching live sales trend: {e}")
        return jsonify({'success': False, 'message': 'Error fetching live sales trend'}), 500
    finally:
        if connection:
            connection.close()

# ==================== BLUETOOTH PRINTER API ROUTES ====================

@app.route('/api/bluetooth/scan', methods=['POST'])
def scan_bluetooth_printers():
    """Scan for available Bluetooth printers"""
    try:
        print("[SCAN] Starting Bluetooth printer scan...")
        
        # This is a placeholder for actual Bluetooth scanning
        # In a real implementation, you would use Web Bluetooth API on the frontend
        # and send the results to this endpoint
        
        return jsonify({
            'success': True,
            'message': 'Bluetooth scanning initiated. Use Web Bluetooth API on frontend.',
            'printers': []
        })
        
    except Exception as e:
        print(f"[ERROR] Bluetooth scan error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/bluetooth/connect', methods=['POST'])
def connect_bluetooth_printer():
    """Connect to a Bluetooth printer"""
    try:
        data = request.get_json()
        device_id = data.get('deviceId')
        device_name = data.get('deviceName', 'Bluetooth Printer')
        
        if not device_id:
            return jsonify({'success': False, 'error': 'Device ID required'}), 400
        
        print(f"[CONNECT] Connecting to Bluetooth printer: {device_name} ({device_id})")
        
        # Store connection info in session or database
        # In a real implementation, you would handle the actual Bluetooth connection
        
        return jsonify({
            'success': True,
            'message': f'Connected to {device_name}',
            'printer': {
                'id': device_id,
                'name': device_name,
                'type': 'bluetooth',
                'status': 'connected'
            }
        })
        
    except Exception as e:
        print(f"[ERROR] Bluetooth connection error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/bluetooth/disconnect', methods=['POST'])
def disconnect_bluetooth_printer():
    """Disconnect from Bluetooth printer"""
    try:
        data = request.get_json()
        device_id = data.get('deviceId')
        
        if not device_id:
            return jsonify({'success': False, 'error': 'Device ID required'}), 400
        
        print(f"[DISCONNECT] Disconnecting Bluetooth printer: {device_id}")
        
        # Handle disconnection logic here
        
        return jsonify({
            'success': True,
            'message': 'Bluetooth printer disconnected'
        })
        
    except Exception as e:
        print(f"[ERROR] Bluetooth disconnection error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/bluetooth/print', methods=['POST'])
def print_bluetooth():
    """Print to a Bluetooth printer"""
    try:
        data = request.get_json()
        device_id = data.get('deviceId')
        content = data.get('content', '')
        printer_name = data.get('printerName', 'Bluetooth Printer')
        
        print(f"=== Bluetooth Print Request ===")
        print(f"Printer: {printer_name} ({device_id})")
        print(f"Content length: {len(content)} characters")
        print(f"Content preview: {content[:200]}..." if len(content) > 200 else f"Content: {content}")
        
        if not device_id or not content:
            print("Error: Missing device ID or content")
            return jsonify({'success': False, 'error': 'Device ID and content required'}), 400
        
        # In a real implementation, you would send the print job to the Bluetooth device
        # This is handled by the frontend Web Bluetooth API
        
        print(f"[SUCCESS] Print job queued for Bluetooth printer: {printer_name}")
        
        return jsonify({
            'success': True,
            'message': f'Print job sent to {printer_name}',
            'bytes_sent': len(content)
        })
        
    except Exception as e:
        print(f"[ERROR] Bluetooth print error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/bluetooth/status', methods=['GET'])
def bluetooth_printer_status():
    """Get Bluetooth printer connection status"""
    try:
        # In a real implementation, you would check actual Bluetooth connections
        return jsonify({
            'success': True,
            'connected': False,
            'printers': [],
            'message': 'No Bluetooth printers connected'
        })
        
    except Exception as e:
        print(f"[ERROR] Bluetooth status error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ==================== WIFI THERMAL PRINTER API ROUTES ====================

@app.route('/api/wifi-thermal/scan', methods=['POST'])
def scan_wifi_thermal_printers():
    """Advanced WiFi thermal printer discovery - dedicated endpoint"""
    try:
        import socket
        import subprocess
        import re
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        print("[THERMAL SCAN] Starting advanced WiFi thermal printer discovery...")
        
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        network_range = data.get('networkRange', '192.168.1')
        scan_methods = data.get('scanMethods', ['network', 'arp'])
        timeout = data.get('timeout', 15)  # Reduced default timeout
        max_workers = data.get('maxWorkers', 10)  # Reduced default workers
        
        discovered_printers = []
        scan_methods_used = []
        
        print(f"🔍 Scanning network: {network_range}")
        print(f"🔧 Methods: {', '.join(scan_methods)}")
        
        # Check if we're in a hosted environment (common indicators)
        is_hosted = any([
            'heroku' in os.environ.get('DYNO', ''),
            'railway' in os.environ.get('RAILWAY_ENVIRONMENT', ''),
            'vercel' in os.environ.get('VERCEL', ''),
            'render' in os.environ.get('RENDER', ''),
            'aws' in os.environ.get('AWS_REGION', ''),
            'azure' in os.environ.get('AZURE_REGION', ''),
            'gcp' in os.environ.get('GOOGLE_CLOUD_PROJECT', ''),
            'digitalocean' in os.environ.get('DIGITALOCEAN_REGION', ''),
            'localhost' not in request.host and '127.0.0.1' not in request.host
        ])
        
        if is_hosted:
            print("🌐 Detected hosted environment - using limited scanning")
            # In hosted environments, we can't scan the local network
            # Return empty results but suggest manual setup
            return jsonify({
                'success': True,
                'printers': [],
                'scan_methods': ['Hosted Environment'],
                'total_found': 0,
                'message': 'Hosted environment detected. Use manual printer setup.',
                'manual_setup_available': True
            })
        
        # Method 1: Network Range Scan for Thermal Printers
        if 'network' in scan_methods:
            print("📡 Method 1: Network range scan for thermal printers...")
            thermal_ports = [9100, 9101, 9102, 515, 631]  # Common thermal printer ports
            
            def test_thermal_printer(ip, port):
                sock = None
                test_sock = None
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)  # Reduced timeout
                    result = sock.connect_ex((ip, port))
                    
                    if result == 0:
                        # Test if it responds to ESC/POS commands (thermal printer test)
                        try:
                            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            test_sock.settimeout(0.5)  # Very short timeout
                            test_sock.connect((ip, port))
                            
                            # Send ESC/POS initialization command
                            test_sock.send(b'\x1B\x40')  # ESC @ - Initialize printer
                            
                            return {
                                'name': f'Thermal Printer at {ip}:{port}',
                                'ip': ip,
                                'port': port,
                                'model': 'ESC/POS Thermal Printer',
                                'type': 'thermal',
                                'discovery_method': 'Network Scan',
                                'status': 'available'
                            }
                        except:
                            # Still might be a printer, but not responding to ESC/POS
                            return {
                                'name': f'Printer at {ip}:{port}',
                                'ip': ip,
                                'port': port,
                                'model': 'Unknown Printer',
                                'type': 'unknown',
                                'discovery_method': 'Network Scan',
                                'status': 'available'
                            }
                except Exception as e:
                    # Skip this IP/port combination
                    pass
                finally:
                    # Ensure sockets are properly closed
                    try:
                        if sock:
                            sock.close()
                    except:
                        pass
                    try:
                        if test_sock:
                            test_sock.close()
                    except:
                        pass
                return None
            
            # Scan the network range
            base_ip = network_range.split('.')
            if len(base_ip) == 3:
                print(f"🔍 Scanning {network_range}.1-254 for thermal printers...")
                
            # Use threading for faster scanning with proper timeout handling
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                
                # Limit IP range to prevent server overload
                ip_range = list(range(1, 51)) + list(range(100, 201))  # Common printer IP ranges
                for i in ip_range:
                    ip = f"{network_range}.{i}"
                    for port in thermal_ports:
                        futures.append(executor.submit(test_thermal_printer, ip, port))
                
                try:
                    for future in as_completed(futures, timeout=timeout):
                        try:
                            result = future.result(timeout=1)  # Individual future timeout
                            if result:
                                discovered_printers.append(result)
                                print(f"✅ Found thermal printer: {result['name']}")
                        except Exception as e:
                            # Skip failed futures
                            continue
                except Exception as e:
                    print(f"[WARNING] Some futures didn't complete in time: {e}")
                    # Cancel remaining futures
                    for future in futures:
                        if not future.done():
                            future.cancel()
            
            scan_methods_used.append('Network Scan')
        
        # Method 2: ARP Table Scan for Active Devices
        if 'arp' in scan_methods:
            print("📡 Method 2: ARP table scan for active devices...")
            try:
                # Get ARP table
                if os.name == 'nt':  # Windows
                    result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=10)
                else:  # Linux/Mac
                    result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    arp_output = result.stdout
                    print(f"📋 ARP table entries: {len(arp_output.splitlines())}")
                    
                    # Parse ARP entries and test for thermal printers
                    for line in arp_output.splitlines():
                        # Extract IP addresses from ARP output
                        ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
                        if ip_match:
                            ip = ip_match.group(1)
                            if not ip.startswith('127.') and not ip.startswith('169.254.'):
                                # Test this IP for thermal printer ports
                                for port in thermal_ports:
                                    try:
                                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                        sock.settimeout(1)
                                        result = sock.connect_ex((ip, port))
                                        sock.close()
                                        
                                        if result == 0:
                                            # Check if it's already in discovered_printers
                                            if not any(p['ip'] == ip and p['port'] == port for p in discovered_printers):
                                                printer_info = {
                                                    'name': f'Thermal Printer at {ip}:{port}',
                                                    'ip': ip,
                                                    'port': port,
                                                    'model': 'ESC/POS Thermal Printer',
                                                    'type': 'thermal',
                                                    'discovery_method': 'ARP Table',
                                                    'status': 'available'
                                                }
                                                discovered_printers.append(printer_info)
                                                print(f"✅ Found thermal printer via ARP: {printer_info['name']}")
                                    except:
                                        continue
                
                scan_methods_used.append('ARP Table')
            except Exception as e:
                print(f"⚠️ ARP scan failed: {e}")
        
        # Method 3: mDNS/Bonjour Discovery
        if 'mdns' in scan_methods:
            print("📡 Method 3: mDNS/Bonjour discovery...")
            try:
                # Try using avahi-browse (Linux) or dns-sd (macOS/Windows)
                commands = [
                    ['avahi-browse', '-t', '-r', '_printer._tcp'],
                    ['avahi-browse', '-t', '-r', '_ipp._tcp'],
                    ['dns-sd', '-B', '_printer._tcp'],
                    ['dns-sd', '-B', '_ipp._tcp']
                ]
                
                for cmd in commands:
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                        if result.returncode == 0:
                            print(f"📋 mDNS discovery found devices")
                            # Parse mDNS output and test for thermal printers
                            # This would need more sophisticated parsing in a real implementation
                            scan_methods_used.append('mDNS/Bonjour')
                            break
                    except:
                        continue
            except Exception as e:
                print(f"⚠️ mDNS discovery failed: {e}")
        
        print(f"🎯 Scan completed: {len(discovered_printers)} thermal printers found")
        print(f"📊 Methods used: {', '.join(scan_methods_used)}")
        
        return jsonify({
            'success': True,
            'printers': discovered_printers,
            'scan_methods': scan_methods_used,
            'total_found': len(discovered_printers)
        })
        
    except Exception as e:
        print(f"[ERROR] WiFi thermal printer scan error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/wifi-thermal/connect', methods=['POST'])
def connect_wifi_thermal_printer():
    """Connect to a WiFi thermal printer"""
    try:
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        ip = data.get('ip')
        port = data.get('port', 9100)
        name = data.get('name', f'Thermal Printer at {ip}')
        model = data.get('model', 'ESC/POS Thermal Printer')
        
        if not ip:
            return jsonify({'success': False, 'error': 'IP address required'}), 400
        
        print(f"[THERMAL CONNECT] Connecting to thermal printer: {name} ({ip}:{port})")
        
        # Test connection and ESC/POS compatibility
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        
        try:
            result = sock.connect_ex((ip, port))
            sock.close()
            
            if result == 0:
                # Test ESC/POS commands
                test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_sock.settimeout(3)
                test_sock.connect((ip, port))
                
                # Send ESC/POS initialization command
                test_sock.send(b'\x1B\x40')  # ESC @ - Initialize printer
                test_sock.close()
                
                return jsonify({
                    'success': True,
                    'message': f'Connected to thermal printer {name}',
                    'printer': {
                        'ip': ip,
                        'port': port,
                        'name': name,
                        'model': model,
                        'type': 'thermal',
                        'status': 'connected'
                    }
                })
            else:
                return jsonify({'success': False, 'error': 'Thermal printer not reachable'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
            
    except Exception as e:
        print(f"[ERROR] WiFi thermal printer connection error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/wifi-thermal/test', methods=['POST'])
def test_wifi_thermal_printer():
    """Test WiFi thermal printer connection"""
    try:
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        ip = data.get('ip')
        port = data.get('port', 9100)
        
        if not ip:
            return jsonify({'success': False, 'error': 'IP address required'}), 400
        
        print(f"[THERMAL TEST] Testing thermal printer: {ip}:{port}")
        
        # Test connection and ESC/POS compatibility
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        
        try:
            result = sock.connect_ex((ip, port))
            sock.close()
            
            if result == 0:
                # Test ESC/POS commands
                test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_sock.settimeout(2)
                test_sock.connect((ip, port))
                
                # Send ESC/POS initialization command
                test_sock.send(b'\x1B\x40')  # ESC @ - Initialize printer
                test_sock.close()
                
                return jsonify({
                    'success': True,
                    'message': 'Thermal printer test successful'
                })
            else:
                return jsonify({'success': False, 'error': 'Thermal printer not reachable'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
            
    except Exception as e:
        print(f"[ERROR] WiFi thermal printer test error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/wifi-thermal/print', methods=['POST'])
def print_wifi_thermal():
    """Print to a WiFi thermal printer with ESC/POS commands"""
    try:
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        ip = data.get('ip')
        port = data.get('port', 9100)
        content = data.get('content', '')
        printer_name = data.get('printerName', f'Thermal Printer at {ip}')
        options = data.get('options', {})
        
        print(f"=== WiFi Thermal Print Request ===")
        print(f"Printer: {printer_name} ({ip}:{port})")
        print(f"Content length: {len(content)} characters")
        print(f"Content preview: {content[:200]}..." if len(content) > 200 else f"Content: {content}")
        
        if not ip or not content:
            print("Error: Missing IP or content")
            return jsonify({'success': False, 'error': 'IP address and content required'}), 400
        
        # Check if this looks like a thermal printer port
        thermal_ports = [9100, 9101, 9102, 515, 631]
        if port not in thermal_ports:
            print(f"Warning: Port {port} is not a typical thermal printer port")
        
        # Send print job to thermal printer
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        
        try:
            print(f"Connecting to thermal printer {ip}:{port}...")
            sock.connect((ip, port))
            print("Connected successfully")
            
            # Convert content to bytes if it's a string
            if isinstance(content, str):
                content = content.encode('utf-8')
            
            print(f"Sending {len(content)} bytes to thermal printer...")
            
            # Send data in chunks for better reliability
            chunk_size = 1024
            bytes_sent = 0
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                sock.send(chunk)
                bytes_sent += len(chunk)
                # Small delay between chunks for thermal printers
                import time
                time.sleep(0.01)
            
            print(f"Successfully sent {bytes_sent} bytes to thermal printer")
            sock.close()
            
            return jsonify({
                'success': True,
                'message': f'Print job sent to thermal printer {printer_name}',
                'bytes_sent': bytes_sent
            })
            
        except Exception as e:
            print(f"[ERROR] WiFi thermal print error: {e}")
            sock.close()
            return jsonify({'success': False, 'error': str(e)})
            
    except Exception as e:
        print(f"[ERROR] WiFi thermal print error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/wifi-thermal/disconnect', methods=['POST'])
def disconnect_wifi_thermal_printer():
    """Disconnect from WiFi thermal printer"""
    try:
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        ip = data.get('ip')
        
        if not ip:
            return jsonify({'success': False, 'error': 'IP address required'}), 400
        
        print(f"[THERMAL DISCONNECT] Disconnecting thermal printer: {ip}")
        
        return jsonify({
            'success': True,
            'message': f'WiFi thermal printer at {ip} disconnected'
        })
        
    except Exception as e:
        print(f"[ERROR] WiFi thermal printer disconnection error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/wifi-thermal/status', methods=['GET'])
def wifi_thermal_printer_status():
    """Get WiFi thermal printer connection status"""
    try:
        # In a real implementation, you would check actual thermal printer connections
        return jsonify({
            'success': True,
            'connected': False,
            'printers': [],
            'message': 'No WiFi thermal printers connected'
        })
        
    except Exception as e:
        print(f"[ERROR] WiFi thermal printer status error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/wifi-thermal/manual-setup', methods=['POST'])
def manual_wifi_printer_setup():
    """Manual WiFi thermal printer setup for hosted environments"""
    try:
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        ip = data.get('ip')
        port = data.get('port', 9100)
        name = data.get('name', f'Manual Printer at {ip}')
        
        if not ip:
            return jsonify({'success': False, 'error': 'IP address required'}), 400
        
        print(f"[MANUAL SETUP] Testing manual printer: {name} ({ip}:{port})")
        
        # Test connection
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        
        try:
            result = sock.connect_ex((ip, port))
            sock.close()
            
            if result == 0:
                # Test ESC/POS commands
                test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_sock.settimeout(3)
                test_sock.connect((ip, port))
                
                # Send ESC/POS initialization command
                test_sock.send(b'\x1B\x40')  # ESC @ - Initialize printer
                test_sock.close()
                
                return jsonify({
                    'success': True,
                    'message': f'Manual printer setup successful',
                    'printer': {
                        'ip': ip,
                        'port': port,
                        'name': name,
                        'model': 'ESC/POS Thermal Printer',
                        'type': 'thermal',
                        'status': 'available',
                        'setup_method': 'manual'
                    }
                })
            else:
                return jsonify({'success': False, 'error': 'Printer not reachable at specified IP and port'})
        except Exception as e:
            return jsonify({'success': False, 'error': f'Connection test failed: {str(e)}'})
            
    except Exception as e:
        print(f"[ERROR] Manual printer setup error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ==================== LEGACY WIFI PRINTER API ROUTES ====================

@app.route('/api/wifi/scan', methods=['POST'])
def scan_wifi_printers_new():
    """Scan for available WiFi printers - Legacy endpoint"""
    try:
        print("[SCAN] Starting WiFi printer scan...")
        
        # Use the existing WiFi scanning logic
        return scan_thermal_printers()
        
    except Exception as e:
        print(f"[ERROR] WiFi scan error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
def connect_wifi_printer():
    """Connect to a WiFi printer"""
    try:
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        ip = data.get('ip')
        port = data.get('port', 9100)
        printer_name = data.get('printerName', f'WiFi Printer at {ip}')
        
        if not ip:
            return jsonify({'success': False, 'error': 'IP address required'}), 400
        
        print(f"[CONNECT] Connecting to WiFi printer: {printer_name} ({ip}:{port})")
        
        # Test connection
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        
        try:
            result = sock.connect_ex((ip, port))
            sock.close()
            
            if result == 0:
                return jsonify({
                    'success': True,
                    'message': f'Connected to {printer_name}',
                    'printer': {
                        'ip': ip,
                        'port': port,
                        'name': printer_name,
                        'type': 'wifi',
                        'status': 'connected'
                    }
                })
            else:
                return jsonify({'success': False, 'error': 'Printer not reachable'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
            
    except Exception as e:
        print(f"[ERROR] WiFi connection error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/wifi/disconnect', methods=['POST'])
def disconnect_wifi_printer():
    """Disconnect from WiFi printer"""
    try:
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        ip = data.get('ip')
        
        if not ip:
            return jsonify({'success': False, 'error': 'IP address required'}), 400
        
        print(f"[DISCONNECT] Disconnecting WiFi printer: {ip}")
        
        return jsonify({
            'success': True,
            'message': f'WiFi printer at {ip} disconnected'
        })
        
    except Exception as e:
        print(f"[ERROR] WiFi disconnection error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/wifi/print', methods=['POST'])
def print_wifi_new():
    """Print to a WiFi printer - New separate endpoint"""
    try:
        try:
            data = request.get_json()
            if data is None:
                data = {}
        except Exception as e:
            print(f"[WARNING] JSON decode error: {e}")
            data = {}
        
        ip = data.get('ip')
        port = data.get('port', 9100)
        content = data.get('content', '')
        printer_name = data.get('printerName', f'WiFi Printer at {ip}')
        
        print(f"=== WiFi Print Request ===")
        print(f"Printer: {printer_name} ({ip}:{port})")
        print(f"Content length: {len(content)} characters")
        print(f"Content preview: {content[:200]}..." if len(content) > 200 else f"Content: {content}")
        
        if not ip or not content:
            print("Error: Missing IP or content")
            return jsonify({'success': False, 'error': 'IP address and content required'}), 400
        
        # Check if this looks like a thermal printer port
        thermal_ports = [9100, 9101, 9102, 515, 631]
        if port not in thermal_ports:
            print(f"Warning: Port {port} is not a typical thermal printer port")
        
        # Send print job to printer
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        
        try:
            print(f"Connecting to {ip}:{port}...")
            sock.connect((ip, port))
            print("Connected successfully")
            
            # Convert content to bytes if it's a string
            if isinstance(content, str):
                content = content.encode('utf-8')
            
            print(f"Sending {len(content)} bytes to printer...")
            
            # Send data in chunks
            chunk_size = 1024
            bytes_sent = 0
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                sock.send(chunk)
                bytes_sent += len(chunk)
            
            print(f"Successfully sent {bytes_sent} bytes to printer")
            sock.close()
            
            return jsonify({
                'success': True,
                'message': f'Print job sent to {printer_name}',
                'bytes_sent': bytes_sent
            })
            
        except Exception as e:
            print(f"[ERROR] WiFi print error: {e}")
            sock.close()
            return jsonify({'success': False, 'error': str(e)})
            
    except Exception as e:
        print(f"[ERROR] WiFi print error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/wifi/status', methods=['GET'])
def wifi_printer_status():
    """Get WiFi printer connection status"""
    try:
        # In a real implementation, you would check actual WiFi connections
        return jsonify({
            'success': True,
            'connected': False,
            'printers': [],
            'message': 'No WiFi printers connected'
        })
        
    except Exception as e:
        print(f"[ERROR] WiFi status error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/separate-printers')
def separate_printer_management():
    """Separate printer management page for Bluetooth and WiFi"""
    return render_template('separate_printer_management.html')

@app.route('/wifi-thermal-printers')
def wifi_thermal_printer_management():
    """WiFi thermal printer management page - dedicated WiFi only"""
    return render_template('wifi_thermal_printer_management.html')

@app.route('/api/payroll/all', methods=['GET'])
def get_all_payrolls():
    """Fetch all payroll profiles with employee details (active only, admin/manager only)"""
    if 'employee_id' not in session or session.get('employee_role') not in ['admin', 'manager']:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    connection = get_db_connection()
    if not connection:
        return jsonify({'success': False, 'message': 'Database connection error'}), 500

    try:
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute('''
                SELECT p.*, e.full_name, e.employee_code, e.role, e.email, e.status, e.profile_photo
                FROM payroll_profiles p
                JOIN employees e ON p.employee_id = e.id
                WHERE e.status = 'active'
                ORDER BY p.created_at DESC
            ''')
            rows = cursor.fetchall()
        return jsonify({'success': True, 'payrolls': rows})
    except Exception as e:
        print(f"Error fetching payrolls: {e}")
        return jsonify({'success': False, 'message': 'Error fetching payrolls'}), 500
    finally:
        connection.close()

if __name__ == '__main__':
    init_database()
    create_sample_data()
    app.run(debug=True, host='0.0.0.0', port=5000)