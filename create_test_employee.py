#!/usr/bin/env python3
"""
Test Employee Creation Script for Point of Sale System
This script creates test employees for different roles.
"""

import mysql.connector
from mysql.connector import Error
import hashlib
import sys
from datetime import date

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'database': 'hotel_pos',
    'user': 'root',
    'password': '',  # Update this if you have a password
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci'
}

def create_database_connection():
    """Create a connection to the database"""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        if connection.is_connected():
            print("✅ Successfully connected to database")
            return connection
    except Error as e:
        print(f"❌ Error connecting to database: {e}")
        return None

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def create_test_employees(connection):
    """Create test employees for different roles"""
    try:
        cursor = connection.cursor()
        
        # Test employees data
        test_employees = [
            {
                'full_name': 'John Manager',
                'email': 'manager@kwetuhotel.com',
                'phone_number': '+254700000002',
                'employee_code': 'MGR1',
                'role': 'manager',
                'password': 'manager123'
            },
            {
                'full_name': 'Jane Cashier',
                'email': 'cashier@kwetuhotel.com',
                'phone_number': '+254700000003',
                'employee_code': 'CSH1',
                'role': 'cashier',
                'password': 'cashier123'
            },
            {
                'full_name': 'Peter Employee',
                'email': 'employee@kwetuhotel.com',
                'phone_number': '+254700000004',
                'employee_code': 'EMP1',
                'role': 'employee',
                'password': 'employee123'
            },
            {
                'full_name': 'Mary Butchery',
                'email': 'butchery@kwetuhotel.com',
                'phone_number': '+254700000005',
                'employee_code': 'BUT1',
                'role': 'butchery',
                'password': 'butchery123'
            }
        ]
        
        # Check if employees already exist
        for employee in test_employees:
            cursor.execute("SELECT COUNT(*) FROM employees WHERE email = %s", (employee['email'],))
            if cursor.fetchone()[0] > 0:
                print(f"⚠️  Employee {employee['email']} already exists, skipping...")
                continue
            
            # Insert employee
            insert_query = """
            INSERT INTO employees (full_name, email, phone_number, employee_code, role, password_hash, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            
            values = (
                employee['full_name'],
                employee['email'],
                employee['phone_number'],
                employee['employee_code'],
                employee['role'],
                hash_password(employee['password']),
                'active'
            )
            
            cursor.execute(insert_query, values)
            print(f"✅ Created {employee['role']}: {employee['email']} (Password: {employee['password']})")
        
        connection.commit()
        return True
        
    except Error as e:
        print(f"❌ Error creating test employees: {e}")
        return False

def display_credentials(connection):
    """Display all user credentials"""
    try:
        cursor = connection.cursor()
        
        cursor.execute("""
            SELECT role, email, employee_code, full_name 
            FROM employees 
            WHERE status = 'active' 
            ORDER BY role, full_name
        """)
        
        employees = cursor.fetchall()
        
        print("\n📋 User Credentials:")
        print("=" * 80)
        print(f"{'Role':<12} {'Email':<25} {'Employee Code':<12} {'Name':<20}")
        print("-" * 80)
        
        for employee in employees:
            role, email, emp_code, full_name = employee
            print(f"{role.capitalize():<12} {email:<25} {emp_code:<12} {full_name}")
        
        print("-" * 80)
        print("\n🔐 Default Passwords:")
        print("Admin: admin123")
        print("Manager: manager123")
        print("Cashier: cashier123")
        print("Employee: employee123")
        print("Butchery: butchery123")
        print("\n⚠️  Remember to change these passwords after first login!")
        
    except Error as e:
        print(f"❌ Error displaying credentials: {e}")

def main():
    """Main function"""
    print("👥 Creating Test Employees for Point of Sale System")
    print("=" * 60)
    
    # Create connection to database
    connection = create_database_connection()
    if not connection:
        print("❌ Failed to connect to database. Please run setup_database.py first.")
        sys.exit(1)
    
    try:
        # Create test employees
        if create_test_employees(connection):
            print("\n✅ Test employees created successfully!")
            
            # Display credentials
            display_credentials(connection)
            
            print("\n🚀 You can now start the application with: python app.py")
        else:
            print("❌ Failed to create test employees")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)
        
    finally:
        if connection and connection.is_connected():
            connection.close()
            print("\n✅ Database connection closed")

if __name__ == "__main__":
    main()
