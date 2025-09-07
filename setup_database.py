#!/usr/bin/env python3
"""
Database setup script for Hotel POS System
Run this script to create the database and tables
"""

import pymysql
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'charset': 'utf8mb4'
}

DB_NAME = os.environ.get('DB_NAME', 'hotel_pos')

def create_database():
    """Create the database"""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
            connection.commit()
            print(f"✅ Database '{DB_NAME}' created or already exists")
        connection.close()
        return True
    except Exception as e:
        print(f"❌ Database creation error: {e}")
        return False

def create_tables():
    """Create the tables"""
    db_config_with_db = DB_CONFIG.copy()
    db_config_with_db['database'] = DB_NAME
    
    try:
        connection = pymysql.connect(**db_config_with_db)
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
            connection.commit()
            print("✅ Employees table created or already exists")
        connection.close()
        return True
    except Exception as e:
        print(f"❌ Table creation error: {e}")
        return False

def main():
    """Main setup function"""
    print("🚀 Setting up Hotel POS Database...")
    print(f"📊 Database: {DB_NAME}")
    print(f"🏠 Host: {DB_CONFIG['host']}")
    print(f"👤 User: {DB_CONFIG['user']}")
    print("-" * 50)
    
    # Create database
    if create_database():
        # Create tables
        if create_tables():
            print("-" * 50)
            print("🎉 Database setup completed successfully!")
            print("💡 You can now run the Flask application with: python app.py")
        else:
            print("❌ Failed to create tables")
    else:
        print("❌ Failed to create database")

if __name__ == "__main__":
    main()

