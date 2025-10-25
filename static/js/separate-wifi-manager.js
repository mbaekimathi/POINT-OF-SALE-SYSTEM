/**
 * Separate WiFi Printer Manager
 * Handles only WiFi printer connections and operations
 */

console.log('📶 Loading Separate WiFi Manager script...');

class SeparateWiFiManager {
    constructor() {
        this.connectedPrinters = new Map();
        this.connectionStatus = 'disconnected';
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 3;
        this.reconnectDelay = 2000;
        
        // Initialize from localStorage
        this.loadPersistedConnections();
        
        // Set up connection monitoring
        this.setupConnectionMonitoring();
        
        console.log('📶 Separate WiFi Manager initialized');
    }

    // Load persisted connections from localStorage
    loadPersistedConnections() {
        try {
            const stored = localStorage.getItem('wifi_printers');
            if (stored) {
                const printers = JSON.parse(stored);
                printers.forEach(printer => {
                    this.connectedPrinters.set(printer.id, {
                        ...printer,
                        status: 'disconnected' // Reset status on load
                    });
                });
                console.log(`📶 Loaded ${printers.length} persisted WiFi printers`);
            }
        } catch (error) {
            console.error('Error loading persisted WiFi connections:', error);
        }
    }

    // Save connections to localStorage
    savePersistedConnections() {
        try {
            const printers = Array.from(this.connectedPrinters.values()).map(printer => ({
                id: printer.id,
                name: printer.name,
                ip: printer.ip,
                port: printer.port,
                connectedAt: printer.connectedAt,
                lastUsed: printer.lastUsed
            }));
            localStorage.setItem('wifi_printers', JSON.stringify(printers));
        } catch (error) {
            console.error('Error saving WiFi connections:', error);
        }
    }

    // Scan for WiFi printers
    async scanForPrinters() {
        try {
            console.log('🔍 Scanning for WiFi printers...');
            
            const response = await fetch('/api/wifi/scan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    networkRange: null, // Auto-detect
                    scanMethods: ['network', 'arp', 'ping'],
                    timeout: 30,
                    maxWorkers: 15
                })
            });

            const result = await response.json();
            
            if (result.success) {
                console.log(`📶 Found ${result.printers.length} WiFi printers`);
                console.log(`📊 Scan methods used: ${result.scan_methods.join(', ')}`);
                
                // Check if we're in a hosted environment
                if (result.manual_setup_available) {
                    console.log('🌐 Hosted environment detected - manual setup available');
                    return {
                        printers: result.printers,
                        manualSetupAvailable: true,
                        message: result.message
                    };
                }
                
                return result.printers;
            } else {
                throw new Error(result.error || 'Scan failed');
            }

        } catch (error) {
            console.error('❌ WiFi scan error:', error);
            throw error;
        }
    }

    // Manual printer setup for hosted environments
    async setupManualPrinter(ip, port = 9100, name = null) {
        try {
            console.log(`🔧 Setting up manual printer: ${name || 'Unnamed'} (${ip}:${port})`);
            
            const response = await fetch('/api/wifi-thermal/manual-setup', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    ip: ip,
                    port: port,
                    name: name || `Manual Printer at ${ip}`
                })
            });

            const result = await response.json();
            
            if (result.success) {
                console.log('✅ Manual printer setup successful');
                return result.printer;
            } else {
                throw new Error(result.error || 'Manual setup failed');
            }

        } catch (error) {
            console.error('❌ Manual printer setup error:', error);
            throw error;
        }
    }

    // Connect to a WiFi printer
    async connectToPrinter(ip, port = 9100, printerName = null) {
        try {
            const name = printerName || `WiFi Printer at ${ip}`;
            console.log(`🔗 Connecting to WiFi printer: ${name} (${ip}:${port})`);
            
            const response = await fetch('/api/wifi/connect', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    ip: ip,
                    port: port,
                    printerName: name
                })
            });

            const result = await response.json();
            
            if (result.success) {
                // Add printer to connected list
                const printerId = await this.addPrinter(result.printer);
                this.connectionStatus = 'connected';
                this.reconnectAttempts = 0;
                
                console.log(`✅ Successfully connected to ${name}`);
                return printerId;
            } else {
                throw new Error(result.error || 'Connection failed');
            }

        } catch (error) {
            console.error('❌ WiFi connection error:', error);
            this.connectionStatus = 'disconnected';
            throw error;
        }
    }

    // Add a new WiFi printer connection
    async addPrinter(printerData) {
        try {
            const printerId = `wifi_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
            
            const printer = {
                id: printerId,
                name: printerData.name,
                ip: printerData.ip,
                port: printerData.port,
                status: 'connected',
                connectedAt: new Date().toISOString(),
                lastUsed: new Date().toISOString()
            };

            this.connectedPrinters.set(printerId, printer);
            this.savePersistedConnections();
            
            console.log(`📶 Added printer: ${printer.name} (${printer.ip}:${printer.port})`);
            return printerId;
            
        } catch (error) {
            console.error('Error adding WiFi printer:', error);
            throw error;
        }
    }

    // Test connection to a WiFi printer
    async testConnection(ip, port = 9100) {
        try {
            console.log(`🧪 Testing connection to ${ip}:${port}`);
            
            const response = await fetch('/api/wifi/connect', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    ip: ip,
                    port: port,
                    printerName: `Test Printer at ${ip}`
                })
            });

            const result = await response.json();
            return result.success;

        } catch (error) {
            console.error('❌ WiFi connection test error:', error);
            return false;
        }
    }

    // Print to WiFi printer
    async printToWiFiPrinter(printerId, content) {
        try {
            const printer = this.connectedPrinters.get(printerId);
            if (!printer) {
                throw new Error('Printer not found');
            }

            if (printer.status !== 'connected') {
                throw new Error('Printer not connected');
            }

            console.log(`🖨️ Printing to WiFi printer: ${printer.name} (${printer.ip}:${printer.port})`);
            
            // Send print request to backend
            const response = await fetch('/api/wifi/print', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    ip: printer.ip,
                    port: printer.port,
                    content: content,
                    printerName: printer.name
                })
            });

            const result = await response.json();
            
            if (result.success) {
                printer.lastUsed = new Date().toISOString();
                this.savePersistedConnections();
                console.log(`✅ Print job sent to ${printer.name}`);
                return result;
            } else {
                throw new Error(result.error || 'Print failed');
            }

        } catch (error) {
            console.error('❌ WiFi print error:', error);
            throw error;
        }
    }

    // Disconnect from a printer
    async disconnectPrinter(printerId) {
        try {
            const printer = this.connectedPrinters.get(printerId);
            if (!printer) {
                throw new Error('Printer not found');
            }

            console.log(`🔌 Disconnecting from ${printer.name} (${printer.ip})`);

            // Send disconnect request to backend
            const response = await fetch('/api/wifi/disconnect', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    ip: printer.ip
                })
            });

            const result = await response.json();
            
            if (result.success) {
                // Remove from connected printers
                this.connectedPrinters.delete(printerId);
                this.savePersistedConnections();
                
                console.log(`✅ Disconnected from ${printer.name}`);
                return result;
            } else {
                throw new Error(result.error || 'Disconnect failed');
            }

        } catch (error) {
            console.error('❌ WiFi disconnect error:', error);
            throw error;
        }
    }

    // Get connected printers
    getConnectedPrinters() {
        return Array.from(this.connectedPrinters.values()).filter(printer => 
            printer.status === 'connected'
        );
    }

    // Get all printers (connected and disconnected)
    getAllPrinters() {
        return Array.from(this.connectedPrinters.values());
    }

    // Check if any printer is connected
    isConnected() {
        return this.getConnectedPrinters().length > 0;
    }

    // Get connection status
    getConnectionStatus() {
        return {
            status: this.connectionStatus,
            connectedCount: this.getConnectedPrinters().length,
            totalCount: this.connectedPrinters.size,
            printers: this.getAllPrinters()
        };
    }

    // Set up connection monitoring
    setupConnectionMonitoring() {
        // Monitor connection status every 30 seconds
        setInterval(() => {
            this.checkConnectionStatus();
        }, 30000);
    }

    // Check connection status
    async checkConnectionStatus() {
        for (const [printerId, printer] of this.connectedPrinters) {
            if (printer.status === 'connected') {
                try {
                    const isConnected = await this.testConnection(printer.ip, printer.port);
                    if (!isConnected) {
                        this.handleDisconnection(printerId);
                    }
                } catch (error) {
                    console.error(`Connection check failed for ${printerId}:`, error);
                    this.handleDisconnection(printerId);
                }
            }
        }
    }

    // Handle printer disconnection
    handleDisconnection(printerId) {
        const printer = this.connectedPrinters.get(printerId);
        if (printer) {
            printer.status = 'disconnected';
            this.connectionStatus = 'disconnected';
            this.attemptReconnection(printerId);
        }
    }

    // Attempt to reconnect to a printer
    async attemptReconnection(printerId) {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log(`❌ Max reconnection attempts reached for ${printerId}`);
            return;
        }

        this.reconnectAttempts++;
        console.log(`🔄 Attempting reconnection ${this.reconnectAttempts}/${this.maxReconnectAttempts} for ${printerId}`);

        setTimeout(async () => {
            try {
                const printer = this.connectedPrinters.get(printerId);
                if (printer) {
                    const isConnected = await this.testConnection(printer.ip, printer.port);
                    if (isConnected) {
                        printer.status = 'connected';
                        this.connectionStatus = 'connected';
                        this.reconnectAttempts = 0;
                        console.log(`✅ Reconnected to ${printer.name}`);
                    } else {
                        this.attemptReconnection(printerId);
                    }
                }
            } catch (error) {
                console.error(`❌ Reconnection failed for ${printerId}:`, error);
                this.attemptReconnection(printerId);
            }
        }, this.reconnectDelay);
    }

    // Add printer manually
    async addPrinterManually(ip, port = 9100, name = null) {
        try {
            const printerName = name || `WiFi Printer at ${ip}`;
            
            // Test connection first
            const isConnected = await this.testConnection(ip, port);
            if (!isConnected) {
                throw new Error('Cannot connect to printer at specified IP and port');
            }

            // Add to connected printers
            const printerId = await this.addPrinter({
                name: printerName,
                ip: ip,
                port: port,
                status: 'connected'
            });

            console.log(`📶 Manually added printer: ${printerName} (${ip}:${port})`);
            return printerId;

        } catch (error) {
            console.error('❌ Error adding printer manually:', error);
            throw error;
        }
    }

    // Clear all connections
    clearAllConnections() {
        this.connectedPrinters.clear();
        this.savePersistedConnections();
        this.connectionStatus = 'disconnected';
        console.log('📶 Cleared all WiFi connections');
    }
}

// Initialize the separate WiFi manager
console.log('🔍 Attempting to initialize Separate WiFi Manager...');
console.log('SeparateWiFiManager class available:', typeof SeparateWiFiManager);

try {
    window.separateWiFiManager = new SeparateWiFiManager();
    console.log('✅ Separate WiFi Manager initialized successfully');
    console.log('Manager instance:', window.separateWiFiManager);
} catch (error) {
    console.error('❌ Error initializing Separate WiFi Manager:', error);
    console.error('Error details:', error.message, error.stack);
    window.separateWiFiManager = null;
}