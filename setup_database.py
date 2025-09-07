#!/usr/bin/env python3
"""
Database Setup Script for Point of Sale System
This script creates the database and all necessary tables if they don't exist.
"""

import mysql.connector
from mysql.connector import Error
import os
import sys

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',  # Update this if you have a password
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci'
}

DATABASE_NAME = 'hotel_pos'

def create_database_connection():
    """Create a connection to MySQL server"""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        if connection.is_connected():
            print("✅ Successfully connected to MySQL server")
            return connection
    except Error as e:
        print(f"❌ Error connecting to MySQL: {e}")
        return None

def create_database(connection):
    """Create the database if it doesn't exist"""
    try:
        cursor = connection.cursor()
        
        # Create database
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        print(f"✅ Database '{DATABASE_NAME}' created or already exists")
        
        # Use the database
        cursor.execute(f"USE {DATABASE_NAME}")
        print(f"✅ Using database '{DATABASE_NAME}'")
        
        return True
    except Error as e:
        print(f"❌ Error creating database: {e}")
        return False

def create_tables(connection):
    """Create all necessary tables"""
    try:
        cursor = connection.cursor()
        
        # Create employees table
        employees_table = """
        CREATE TABLE IF NOT EXISTS employees (
            id INT AUTO_INCREMENT PRIMARY KEY,
            full_name VARCHAR(255) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            phone_number VARCHAR(20),
            employee_code VARCHAR(4) UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            profile_photo VARCHAR(255),
            role ENUM('admin', 'manager', 'cashier', 'butchery', 'employee') NOT NULL,
            status ENUM('waiting_approval', 'active', 'suspended') DEFAULT 'waiting_approval',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # Create items table
        items_table = """
        CREATE TABLE IF NOT EXISTS items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            price DECIMAL(10, 2) NOT NULL,
            category VARCHAR(100),
            stock INT DEFAULT 0,
            status ENUM('active', 'inactive') DEFAULT 'active',
            image_url VARCHAR(500),
            sku VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            stock_update_enabled BOOLEAN DEFAULT TRUE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # Create sales table
        sales_table = """
        CREATE TABLE IF NOT EXISTS sales (
            id INT AUTO_INCREMENT PRIMARY KEY,
            receipt_number VARCHAR(20) UNIQUE NOT NULL,
            employee_id INT NOT NULL,
            total_amount DECIMAL(10, 2) NOT NULL,
            payment_method ENUM('cash', 'mpesa') NOT NULL,
            status ENUM('pending', 'confirmed', 'cancelled') DEFAULT 'pending',
            sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # Create sales_items table
        sales_items_table = """
        CREATE TABLE IF NOT EXISTS sales_items (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sale_id INT NOT NULL,
            item_id INT NOT NULL,
            quantity INT NOT NULL,
            unit_price DECIMAL(10, 2) NOT NULL,
            total_price DECIMAL(10, 2) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # Create hotel_settings table
        hotel_settings_table = """
        CREATE TABLE IF NOT EXISTS hotel_settings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            hotel_name VARCHAR(255) NOT NULL,
            company_email VARCHAR(255) NOT NULL,
            company_phone VARCHAR(50) NOT NULL,
            hotel_address TEXT,
            payment_method ENUM('buy_goods', 'paybill') NOT NULL,
            till_number VARCHAR(20),
            business_number VARCHAR(20),
            account_number VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # Create off_days table
        off_days_table = """
        CREATE TABLE IF NOT EXISTS off_days (
            id INT AUTO_INCREMENT PRIMARY KEY,
            employee_id INT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            reason TEXT,
            status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending',
            approved_by INT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE,
            FOREIGN KEY (approved_by) REFERENCES employees(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # Create sessions table for user authentication
        sessions_table = """
        CREATE TABLE IF NOT EXISTS sessions (
            id VARCHAR(128) PRIMARY KEY,
            user_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NULL,
            FOREIGN KEY (user_id) REFERENCES employees(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # Create audit_logs table for tracking changes
        audit_logs_table = """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT,
            action VARCHAR(100) NOT NULL,
            table_name VARCHAR(50),
            record_id INT,
            old_values JSON,
            new_values JSON,
            ip_address VARCHAR(45),
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES employees(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # Execute table creation queries
        tables = [
            ("employees", employees_table),
            ("items", items_table),
            ("sales", sales_table),
            ("sales_items", sales_items_table),
            ("hotel_settings", hotel_settings_table),
            ("off_days", off_days_table),
            ("sessions", sessions_table),
            ("audit_logs", audit_logs_table)
        ]
        
        for table_name, table_query in tables:
            cursor.execute(table_query)
            print(f"✅ Table '{table_name}' created or already exists")
        
        return True
        
    except Error as e:
        print(f"❌ Error creating tables: {e}")
        return False

def create_indexes(connection):
    """Create indexes for better performance"""
    try:
        cursor = connection.cursor()
        
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_employees_role ON employees(role)",
            "CREATE INDEX IF NOT EXISTS idx_employees_status ON employees(status)",
            "CREATE INDEX IF NOT EXISTS idx_items_category ON items(category)",
            "CREATE INDEX IF NOT EXISTS idx_items_status ON items(status)",
            "CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date)",
            "CREATE INDEX IF NOT EXISTS idx_sales_status ON sales(status)",
            "CREATE INDEX IF NOT EXISTS idx_sales_employee ON sales(employee_id)",
            "CREATE INDEX IF NOT EXISTS idx_sales_items_sale ON sales_items(sale_id)",
            "CREATE INDEX IF NOT EXISTS idx_sales_items_item ON sales_items(item_id)",
            "CREATE INDEX IF NOT EXISTS idx_off_days_employee ON off_days(employee_id)",
            "CREATE INDEX IF NOT EXISTS idx_off_days_status ON off_days(status)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at)"
        ]
        
        for index_query in indexes:
            cursor.execute(index_query)
        
        print("✅ Database indexes created or already exist")
        return True
        
    except Error as e:
        print(f"❌ Error creating indexes: {e}")
        return False

def insert_default_data(connection):
    """Insert default data if tables are empty"""
    try:
        cursor = connection.cursor()
        
        # Check if hotel_settings is empty and insert default data
        cursor.execute("SELECT COUNT(*) FROM hotel_settings")
        hotel_settings_count = cursor.fetchone()[0]
        
        if hotel_settings_count == 0:
            default_hotel_settings = """
            INSERT INTO hotel_settings (hotel_name, company_email, company_phone, hotel_address, payment_method, till_number, business_number, account_number)
            VALUES ('Kwetu Hotel', 'info@kwetuhotel.com', '+254700000000', 'Nairobi, Kenya', 'paybill', '123456', '123456', 'KWETU001')
            """
            cursor.execute(default_hotel_settings)
            print("✅ Default hotel settings inserted")
        
        # Check if items table is empty and insert sample data
        cursor.execute("SELECT COUNT(*) FROM items")
        items_count = cursor.fetchone()[0]
        
        if items_count == 0:
            sample_items = [
                ("Ugali", "Traditional Kenyan maize meal", 50.00, "Food", 100, "active", None, "UGL001"),
                ("Chapati", "Kenyan flatbread", 30.00, "Food", 50, "active", None, "CHP001"),
                ("Chips", "French fries", 80.00, "Food", 30, "active", None, "CHS001"),
                ("Coca Cola", "Soft drink", 60.00, "Beverage", 50, "active", None, "COC001"),
                ("Fillet", "Fish fillet", 200.00, "Meat", 20, "active", None, "FIL001"),
                ("Chops", "Meat chops", 250.00, "Meat", 15, "active", None, "CHP002")
            ]
            
            insert_items_query = """
            INSERT INTO items (name, description, price, category, stock, status, image_url, sku)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            cursor.executemany(insert_items_query, sample_items)
            print("✅ Sample items inserted")
        
        connection.commit()
        return True
        
    except Error as e:
        print(f"❌ Error inserting default data: {e}")
        return False

def create_admin_user(connection):
    """Create a default admin user if no admin exists"""
    try:
        cursor = connection.cursor()
        
        # Check if any admin user exists
        cursor.execute("SELECT COUNT(*) FROM employees WHERE role = 'admin'")
        admin_count = cursor.fetchone()[0]
        
        if admin_count == 0:
            # Create default admin user (password: admin123)
            import hashlib
            password_hash = hashlib.sha256("admin123".encode()).hexdigest()
            
            admin_user = """
            INSERT INTO employees (full_name, email, phone_number, employee_code, role, password_hash, status)
            VALUES ('System Administrator', 'admin@kwetuhotel.com', '+254700000001', 'ADM1', 'admin', %s, 'active')
            """
            
            cursor.execute(admin_user, (password_hash,))
            print("✅ Default admin user created (Username: admin@kwetuhotel.com, Password: admin123)")
        
        connection.commit()
        return True
        
    except Error as e:
        print(f"❌ Error creating admin user: {e}")
        return False

def verify_setup(connection):
    """Verify that all tables were created successfully"""
    try:
        cursor = connection.cursor()
        
        # Get list of all tables
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        expected_tables = [
            'employees', 'items', 'sales', 'sales_items', 
            'hotel_settings', 'off_days', 'sessions', 'audit_logs'
        ]
        
        # Check for additional tables that might exist
        additional_tables = [
            'employee_breaks', 'print_jobs', 'printers', 'shifts', 'stock_transactions'
        ]
        
        existing_tables = [table[0] for table in tables]
        
        print("\n📋 Database Setup Verification:")
        print("=" * 50)
        
        # Check core tables
        core_tables_found = 0
        for table in expected_tables:
            if table in existing_tables:
                # Get row count
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"✅ {table}: {count} records")
                core_tables_found += 1
            else:
                print(f"❌ {table}: Table not found")
        
        # Check additional tables
        print("\n📋 Additional Tables Found:")
        for table in additional_tables:
            if table in existing_tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"✅ {table}: {count} records")
        
        print("=" * 50)
        
        # Return True if all core tables are found
        return core_tables_found == len(expected_tables)
        
    except Error as e:
        print(f"❌ Error verifying setup: {e}")
        return False

def main():
    """Main function to set up the database"""
    print("🚀 Starting Database Setup for Point of Sale System")
    print("=" * 60)
    
    # Create connection to MySQL server
    connection = create_database_connection()
    if not connection:
        print("❌ Failed to connect to MySQL server. Please check your configuration.")
        sys.exit(1)
    
    try:
        # Create database
        if not create_database(connection):
            print("❌ Failed to create database")
            sys.exit(1)
        
        # Create tables
        if not create_tables(connection):
            print("❌ Failed to create tables")
            sys.exit(1)
        
        # Create indexes
        if not create_indexes(connection):
            print("❌ Failed to create indexes")
            sys.exit(1)
        
        # Insert default data
        if not insert_default_data(connection):
            print("❌ Failed to insert default data")
            sys.exit(1)
        
        # Create admin user
        if not create_admin_user(connection):
            print("❌ Failed to create admin user")
            sys.exit(1)
        
        # Verify setup
        if verify_setup(connection):
            print("\n🎉 Database setup completed successfully!")
            print("\n📝 Next Steps:")
            print("1. Run the Flask application: python app.py")
            print("2. Access the system at: http://localhost:5000")
            print("3. Login with admin credentials:")
            print("   - Email: admin@kwetuhotel.com")
            print("   - Password: admin123")
            print("\n⚠️  Remember to change the default admin password after first login!")
        else:
            print("❌ Database setup verification failed")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Unexpected error during setup: {e}")
        sys.exit(1)
        
    finally:
        if connection and connection.is_connected():
            connection.close()
            print("✅ Database connection closed")

if __name__ == "__main__":
    main()
