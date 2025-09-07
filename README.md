# Point of Sale (POS) System

A comprehensive, multi-role Point of Sale system built with Flask and MySQL, designed for hotel and restaurant operations.

## 🚀 Features

### Multi-Role Authentication System
- **Admin**: Full system access and management
- **Manager**: Staff and operations management
- **Employee**: Basic operations access
- **Cashier**: Point of sale operations
- **Butchery**: Specialized meat department operations

### Core POS Functionality
- **Real-time Sales Processing**: Complete transaction management
- **Receipt Printing**: Bluetooth thermal printer support
- **Double Printing**: Customer and company copy options
- **Payment Methods**: Cash and M-Pesa integration
- **Inventory Management**: Real-time stock tracking

### Dashboard Analytics
- **Revenue Tracking**: Total revenue with currency formatting
- **Sales Trends**: Monthly and daily sales analytics
- **Employee Performance**: Staff analytics and reporting
- **Data Filtering**: General vs Verified data views
- **Interactive Charts**: Chart.js powered visualizations

### Management Features
- **Employee Management**: HR system with role assignments
- **Item Management**: Product catalog with image uploads
- **Receipt Management**: Status confirmation workflow
- **Off-days Management**: Employee leave tracking
- **Printer Management**: Thermal printer configuration

### Technical Features
- **Responsive Design**: Mobile-first approach for all screen sizes
- **Real-time Updates**: Live data synchronization
- **Database Integration**: MySQL with optimized queries
- **Security**: Role-based access control
- **Error Handling**: Comprehensive error management

## 🛠️ Technology Stack

- **Backend**: Flask (Python)
- **Database**: MySQL
- **Frontend**: HTML5, CSS3, JavaScript
- **Styling**: Tailwind CSS
- **Charts**: Chart.js
- **Printing**: ESC/POS commands for thermal printers

## 📋 Installation

### Prerequisites
- Python 3.8+
- MySQL 5.7+
- Git

### Setup Instructions

1. **Clone the repository**
   ```bash
   git clone https://github.com/mbaekimathi/POINT-OF-SALE-SYSTEM.git
   cd POINT-OF-SALE-SYSTEM
   ```

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Database Setup**
   - Create a MySQL database named `hotel_pos`
   - Update database credentials in `app.py` if needed
   - Run the database setup script:
   ```bash
   python setup_database.py
   ```

4. **Create Test Employee**
   ```bash
   python create_test_employee.py
   ```

5. **Run the Application**
   ```bash
   python app.py
   ```

6. **Access the System**
   - Open your browser and go to `http://localhost:5000`
   - Use the test employee credentials created in step 4

## 🗄️ Database Schema

### Core Tables
- **employees**: User accounts and role management
- **sales**: Transaction records with status tracking
- **sales_items**: Individual items in each sale
- **items**: Product catalog and inventory
- **hotel_settings**: System configuration
- **off_days**: Employee leave management

### Key Features
- **Status Workflow**: Sales start as 'pending' and require confirmation
- **Role-based Access**: Different permissions for each user role
- **Audit Trail**: Complete transaction history with timestamps

## 🎯 User Roles & Permissions

### Admin
- Full system access
- Employee management
- System settings
- Analytics and reporting
- Printer management

### Manager
- Staff management
- Item management
- Sales analytics
- Receipt confirmation
- Off-days approval

### Employee
- Basic dashboard access
- Off-days requests
- Limited analytics view

### Cashier
- Point of sale operations
- Receipt printing
- Transaction processing

### Butchery
- Specialized meat operations
- Department-specific analytics

## 📊 Analytics & Reporting

### Dashboard Features
- **Revenue Tracking**: Real-time total revenue display
- **Sales Trends**: Monthly quantity sold trends
- **Time Analytics**: Hourly sales patterns
- **Employee Performance**: Staff productivity metrics
- **Data Filtering**: Toggle between general and verified data

### Chart Types
- Line charts for sales trends
- Bar charts for performance metrics
- Pie charts for status distribution
- Dual-axis charts for time-based analysis

## 🖨️ Printing System

### Thermal Printer Support
- **Bluetooth Connection**: Wireless printer connectivity
- **ESC/POS Commands**: Standard thermal printer protocol
- **Double Printing**: Customer and company copies
- **Receipt Formatting**: Professional receipt layout
- **Hotel Branding**: Customizable header and footer

### Printer Management
- Multiple printer support
- Connection status monitoring
- Print job queuing
- Error handling and retry logic

## 🔧 Configuration

### Environment Setup
- Database connection settings in `app.py`
- Printer configuration in `static/js/bluetooth-printer-manager.js`
- Hotel settings via admin panel

### Customization
- Hotel name and branding
- Payment method configuration
- Receipt template customization
- Dashboard layout preferences

## 📱 Mobile Responsiveness

### Design Approach
- Mobile-first responsive design
- Touch-friendly interface
- Optimized for tablets and phones
- Adaptive layouts for all screen sizes

### Key Responsive Features
- Collapsible navigation
- Touch-optimized buttons
- Responsive charts and tables
- Mobile-friendly forms

## 🔒 Security Features

### Authentication
- Session-based authentication
- Role-based access control
- Password hashing
- Secure logout functionality

### Data Protection
- SQL injection prevention
- Input validation
- Error message sanitization
- Secure file uploads

## 🚀 Deployment

### Production Considerations
- Use a production WSGI server (Gunicorn)
- Configure reverse proxy (Nginx)
- Set up SSL certificates
- Database connection pooling
- Environment variable configuration

### Docker Support
- Dockerfile included for containerization
- Docker Compose for multi-service deployment
- Environment-based configuration

## 📈 Performance Optimization

### Database Optimization
- Indexed queries for fast lookups
- Optimized JOIN operations
- Connection pooling
- Query result caching

### Frontend Optimization
- Minified CSS and JavaScript
- Image optimization
- Lazy loading for charts
- Efficient DOM manipulation

## 🐛 Troubleshooting

### Common Issues
1. **Database Connection**: Check MySQL service and credentials
2. **Printer Issues**: Verify Bluetooth connection and printer status
3. **Permission Errors**: Ensure proper file permissions
4. **Port Conflicts**: Change port if 5000 is occupied

### Debug Mode
- Enable Flask debug mode for development
- Check browser console for JavaScript errors
- Monitor server logs for backend issues

## 🤝 Contributing

### Development Setup
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

### Code Standards
- Follow PEP 8 for Python code
- Use meaningful variable names
- Add comments for complex logic
- Maintain consistent formatting

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 👥 Support

For support and questions:
- Create an issue on GitHub
- Check the documentation
- Review the troubleshooting section

## 🔄 Version History

### v1.0.0 (Current)
- Complete POS system implementation
- Multi-role authentication
- Dashboard analytics
- Receipt printing system
- Mobile responsive design
- Database integration
- Employee management
- Item management
- Receipt confirmation workflow

---

**Built with ❤️ for modern hospitality businesses**