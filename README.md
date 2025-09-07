# Kwetu Deliveries - POS System with Thermal Printer Support

A comprehensive Point of Sale (POS) system for delivery businesses with integrated thermal printer support for Bluetooth, WiFi, and USB connections.

## Features

### Core POS Features
- **Employee Management**: Role-based access control (Admin, Manager, Cashier, Butchery, Employee)
- **Inventory Management**: Track items, stock levels, and transactions
- **Point of Sale**: Modern, responsive POS interface
- **Stock Tracking**: Automatic stock updates with transaction logging
- **Off Days Management**: Employee leave management with calendar view

### Thermal Printer Integration
- **Multi-Connection Support**: Bluetooth, WiFi, and USB thermal printers
- **Auto-Discovery**: Scan and automatically detect available printers
- **Receipt Printing**: Automatic receipt printing after transactions
- **Manual Printing**: Print receipts on demand from POS interface
- **Printer Management**: Admin interface for printer configuration and testing

## Installation

### Prerequisites
- Python 3.7 or higher
- MySQL database
- Bluetooth adapter (for Bluetooth printers)
- Network access (for WiFi printers)

### Setup Instructions

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd KWETU_DELIVERIES
   ```

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   Create a `.env` file in the root directory:
   ```env
   DB_HOST=localhost
   DB_USER=your_mysql_username
   DB_PASSWORD=your_mysql_password
   DB_NAME=hotel_pos
   SECRET_KEY=your-secret-key-here
   ```

4. **Set up MySQL database**
   - Create a MySQL database named `hotel_pos`
   - The application will automatically create the required tables on first run

5. **Run the application**
   ```bash
   python app.py
   ```

6. **Access the application**
   - Open your browser and go to `http://localhost:5000`
   - The system will create sample data on first run

## Printer Setup

### Bluetooth Printers
1. Ensure your Bluetooth printer is powered on and in pairing mode
2. Go to Admin Dashboard → Printer Management
3. Click "Scan for Printers"
4. Select your Bluetooth printer from the list
5. Test the connection

### WiFi Printers
1. Connect your thermal printer to your WiFi network
2. Note the printer's IP address
3. Go to Admin Dashboard → Printer Management
4. Click "Add Printer" and select WiFi
5. Enter the printer's IP address
6. Test the connection

### USB Printers
1. Connect your thermal printer via USB
2. Go to Admin Dashboard → Printer Management
3. Click "Scan for Printers" or manually add the printer
4. Select the appropriate USB port
5. Test the connection

## Default Login Credentials

The system creates a default admin account on first run:
- **Employee Code**: 0001
- **Password**: admin123

## Usage

### Admin Functions
- **Employee Management**: Add, edit, approve, and manage employees
- **Printer Management**: Configure and test thermal printers
- **Item Management**: Add and manage inventory items
- **Off Days Management**: Manage employee leave requests

### POS Operations
1. **Login**: Use your employee credentials to access the POS
2. **Add Items**: Click on menu items to add them to the cart
3. **Process Payment**: Complete transactions and automatically print receipts
4. **Manual Printing**: Use the "Print Receipt" button for additional copies

### Printer Operations
- **Automatic Printing**: Receipts are automatically printed after successful transactions
- **Manual Printing**: Use the print button in the POS interface
- **Test Printing**: Test printer connectivity from the admin panel

## Supported Printer Types

### Thermal Printers
- **58mm**: Standard receipt width (default)
- **80mm**: Wide receipt format
- **ESC/POS**: Most common thermal printer protocol

### Connection Types
- **Bluetooth**: For wireless Bluetooth thermal printers
- **WiFi**: For network-connected thermal printers
- **USB**: For direct USB connection thermal printers

## Troubleshooting

### Common Issues

1. **Printer Not Found**
   - Ensure printer is powered on and connected
   - Check network connectivity for WiFi printers
   - Verify Bluetooth pairing for Bluetooth printers

2. **Print Job Failed**
   - Check printer status in Admin → Printer Management
   - Test printer connection
   - Verify printer has paper and is not jammed

3. **Database Connection Error**
   - Verify MySQL is running
   - Check database credentials in `.env` file
   - Ensure database exists

### Bluetooth Issues (Windows)
- Install PyBluez: `pip install pybluez`
- Ensure Bluetooth adapter is enabled
- Run as administrator if needed

### Network Printer Issues
- Verify printer IP address
- Check firewall settings
- Ensure printer is on the same network

## API Endpoints

### Printer Management
- `GET /api/printers` - List all configured printers
- `POST /api/printers/scan` - Scan for available printers
- `POST /api/printers` - Add new printer
- `POST /api/printers/{id}/test` - Test printer connection
- `DELETE /api/printers/{id}` - Delete printer
- `POST /api/print/receipt` - Print receipt

### POS Operations
- `GET /api/pos/items` - Get active items for POS
- `POST /api/pos/process-sale` - Process sale and update stock

## Development

### Adding New Printer Types
1. Extend the `print_to_printer()` function in `app.py`
2. Add new connection type to the database enum
3. Update the printer management UI

### Customizing Receipt Format
Modify the `generate_receipt_content()` function in `app.py` to customize receipt layout and content.

## License

This project is licensed under the MIT License.

## Support

For support and questions, please contact the development team.