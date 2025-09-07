#!/usr/bin/env python3
"""
Create a test employee for testing the login system
Run this script to create a test employee with active status
"""

import pymysql
import hashlib
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'hotel_pos'),
    'charset': 'utf8mb4'
}

def hash_password(password):
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def create_test_employee():
    """Create a test employee with active status"""
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            # Check if test employee already exists
            cursor.execute("SELECT id FROM employees WHERE employee_code = '1234'")
            if cursor.fetchone():
                print("❌ Test employee already exists!")
                return False
            
            # Create test employee
            cursor.execute("""
                INSERT INTO employees (full_name, email, phone_number, employee_code, password_hash, role, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                'Test Admin',
                'admin@hotel.com',
                '+1234567890',
                '1234',
                hash_password('password123'),
                'admin',
                'active'
            ))
            
            connection.commit()
            print("✅ Test employee created successfully!")
            print("📋 Login Details:")
            print("   Employee Code: 1234")
            print("   Password: password123")
            print("   Role: Admin")
            print("   Status: Active")
            print("\n💡 You can now test the login system with these credentials.")
            return True
            
    except Exception as e:
        print(f"❌ Error creating test employee: {e}")
        return False
    finally:
        if 'connection' in locals():
            connection.close()

def create_multiple_test_employees():
    """Create multiple test employees with different roles"""
    test_employees = [
        {
            'full_name': 'Test Admin',
            'email': 'admin@hotel.com',
            'phone_number': '+1234567890',
            'employee_code': '1234',
            'password': 'password123',
            'role': 'admin',
            'status': 'active'
        },
        {
            'full_name': 'Test Manager',
            'email': 'manager@hotel.com',
            'phone_number': '+1234567891',
            'employee_code': '2345',
            'password': 'password123',
            'role': 'manager',
            'status': 'active'
        },
        {
            'full_name': 'Test Cashier',
            'email': 'cashier@hotel.com',
            'phone_number': '+1234567892',
            'employee_code': '3456',
            'password': 'password123',
            'role': 'cashier',
            'status': 'active'
        },
        {
            'full_name': 'Test Butchery',
            'email': 'butchery@hotel.com',
            'phone_number': '+1234567893',
            'employee_code': '4567',
            'password': 'password123',
            'role': 'butchery',
            'status': 'active'
        },
        {
            'full_name': 'Test Employee',
            'email': 'employee@hotel.com',
            'phone_number': '+1234567894',
            'employee_code': '5678',
            'password': 'password123',
            'role': 'employee',
            'status': 'active'
        },
        {
            'full_name': 'Pending Employee',
            'email': 'pending@hotel.com',
            'phone_number': '+1234567895',
            'employee_code': '6789',
            'password': 'password123',
            'role': 'employee',
            'status': 'waiting_approval'
        }
    ]
    
    try:
        connection = pymysql.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            created_count = 0
            for employee in test_employees:
                # Check if employee already exists
                cursor.execute("SELECT id FROM employees WHERE employee_code = %s", (employee['employee_code'],))
                if cursor.fetchone():
                    print(f"⚠️  Employee {employee['employee_code']} already exists, skipping...")
                    continue
                
                # Create employee
                cursor.execute("""
                    INSERT INTO employees (full_name, email, phone_number, employee_code, password_hash, role, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    employee['full_name'],
                    employee['email'],
                    employee['phone_number'],
                    employee['employee_code'],
                    hash_password(employee['password']),
                    employee['role'],
                    employee['status']
                ))
                created_count += 1
            
            connection.commit()
            print(f"✅ Created {created_count} test employees successfully!")
            print("\n📋 Test Login Credentials:")
            print("   Admin: 1234 / password123")
            print("   Manager: 2345 / password123")
            print("   Cashier: 3456 / password123")
            print("   Butchery: 4567 / password123")
            print("   Employee: 5678 / password123")
            print("   Pending: 6789 / password123 (will show approval message)")
            print("\n💡 Try logging in with different roles to see different dashboards!")
            return True
            
    except Exception as e:
        print(f"❌ Error creating test employees: {e}")
        return False
    finally:
        if 'connection' in locals():
            connection.close()

def main():
    """Main function"""
    print("🚀 Creating Test Employees for Hotel POS System...")
    print("-" * 50)
    
    choice = input("Create (1) single admin or (2) multiple test employees? [1/2]: ").strip()
    
    if choice == '1':
        create_test_employee()
    elif choice == '2':
        create_multiple_test_employees()
    else:
        print("❌ Invalid choice. Please run the script again and choose 1 or 2.")

if __name__ == "__main__":
    main()

