/**
 * WiFi Thermal Printer Manager - Dedicated WiFi Only
 * Advanced thermal printer discovery and printing for WiFi networks
 * Supports both wired and wireless thermal printers
 */

class WiFiThermalPrinterManager {
    constructor() {
        this.connectedPrinters = new Map();
        this.scanResults = new Map();
        this.connectionStatus = 'disconnected';
        this.scanInProgress = false;
        
        // Thermal printer specific settings
        this.thermalPorts = [9100, 9101, 9102, 515, 631];
        this.escPosCommands = {
            init: '\x1B\x40',           // ESC @ - Initialize printer
            cut: '\x1D\x56\x00',        // GS V - Full cut
            feed: '\x0A',               // LF - Line feed
            center: '\x1B\x61\x01',     // ESC a 1 - Center alignment
            left: '\x1B\x61\x00',       // ESC a 0 - Left alignment
            bold: '\x1B\x45\x01',        // ESC E 1 - Bold on
            normal: '\x1B\x45\x00',      // ESC E 0 - Bold off
            doubleHeight: '\x1B\x21\x10', // ESC ! 16 - Double height
            normalSize: '\x1B\x21\x00'    // ESC ! 0 - Normal size
        };
        
        // Performance metrics
        this.metrics = {
            scanTimes: [],
            connectionTimes: [],
            printTimes: [],
            errorCount: 0,
            successfulPrints: 0
        };
        
        // Load persisted connections
        this.loadPersistedConnections();
        
        console.log('🔥 WiFi Thermal Printer Manager initialized');
    }

    // Load persisted connections from localStorage
    loadPersistedConnections() {
        try {
            const savedConnections = localStorage.getItem('wifi-thermal-connections');
            if (savedConnections) {
                const connections = JSON.parse(savedConnections);
                console.log(`📶 Loading ${connections.length} persisted WiFi thermal printer connections`);
                
                connections.forEach(connection => {
                    this.connectedPrinters.set(connection.id, {
                        ...connection,
                        status: 'disconnected' // Reset status on load
                    });
                });
            }
        } catch (error) {
            console.error('❌ Error loading persisted WiFi connections:', error);
        }
    }

    // Save connections to localStorage
    savePersistedConnections() {
        try {
            const connections = Array.from(this.connectedPrinters.values()).map(printer => ({
                id: printer.id,
                name: printer.name,
                ip: printer.ip,
                port: printer.port,
                status: printer.status,
                model: printer.model,
                type: printer.type,
                lastUsed: printer.lastUsed,
                connectedAt: printer.connectedAt
            }));
            localStorage.setItem('wifi-thermal-connections', JSON.stringify(connections));
        } catch (error) {
            console.error('❌ Error saving WiFi connections:', error);
        }
    }

    // Advanced WiFi thermal printer scanning
    async scanForThermalPrinters(options = {}) {
        if (this.scanInProgress) {
            throw new Error('Scan already in progress');
        }

        this.scanInProgress = true;
        const startTime = Date.now();
        
        try {
            console.log('🔍 Starting advanced WiFi thermal printer scan...');
            
            const scanOptions = {
                networkRange: options.networkRange || this.detectNetworkRange(),
                scanMethods: options.scanMethods || ['network', 'arp', 'mdns'],
                timeout: options.timeout || 30,
                maxWorkers: options.maxWorkers || 20
            };

            console.log(`📡 Scanning network: ${scanOptions.networkRange}`);
            console.log(`🔧 Methods: ${scanOptions.scanMethods.join(', ')}`);

            // Call backend scanning API
            const response = await fetch('/api/wifi-thermal/scan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(scanOptions)
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const result = await response.json();
            
            if (result.success) {
                console.log(`✅ Found ${result.printers.length} thermal printers`);
                
                // Store scan results
                result.printers.forEach(printer => {
                    this.scanResults.set(printer.ip, printer);
                });

                const scanTime = Date.now() - startTime;
                this.metrics.scanTimes.push(scanTime);
                
                console.log(`⏱️ Scan completed in ${scanTime}ms`);
                return result.printers;
            } else {
                throw new Error(result.error || 'Scan failed');
            }

        } catch (error) {
            this.metrics.errorCount++;
            console.error('❌ WiFi thermal printer scan error:', error);
            throw error;
        } finally {
            this.scanInProgress = false;
        }
    }

    // Detect local network range
    detectNetworkRange() {
        // This would ideally be done on the backend
        // For now, return common ranges
        return '192.168.1';
    }

    // Connect to a thermal printer
    async connectToThermalPrinter(printerData) {
        const startTime = Date.now();
        
        try {
            console.log(`🔗 Connecting to thermal printer: ${printerData.name} (${printerData.ip}:${printerData.port})`);
            
            // Test connection via backend
            const response = await fetch('/api/wifi-thermal/connect', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    ip: printerData.ip,
                    port: printerData.port,
                    name: printerData.name,
                    model: printerData.model
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const result = await response.json();
            
            if (result.success) {
                // Add to connected printers
                const printerId = await this.addThermalPrinter(result.printer);
                
                const connectionTime = Date.now() - startTime;
                this.metrics.connectionTimes.push(connectionTime);
                
                console.log(`✅ Connected to thermal printer in ${connectionTime}ms`);
                this.notifyConnectionStatus('connected', result.printer);
                
                return printerId;
            } else {
                throw new Error(result.error || 'Connection failed');
            }

        } catch (error) {
            this.metrics.errorCount++;
            console.error('❌ Thermal printer connection error:', error);
            throw error;
        }
    }

    // Add thermal printer to connected list
    async addThermalPrinter(printerData) {
        try {
            const printerId = `thermal_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
            
            const printer = {
                id: printerId,
                name: printerData.name,
                ip: printerData.ip,
                port: printerData.port,
                model: printerData.model || 'Thermal Printer',
                type: 'thermal',
                status: 'connected',
                connectedAt: new Date().toISOString(),
                lastUsed: new Date().toISOString()
            };

            this.connectedPrinters.set(printerId, printer);
            this.savePersistedConnections();
            
            console.log(`📶 Added thermal printer: ${printer.name} (${printer.ip}:${printer.port})`);
            return printerId;
            
        } catch (error) {
            console.error('❌ Error adding thermal printer:', error);
            throw error;
        }
    }

    // Test thermal printer connection
    async testThermalPrinter(ip, port = 9100) {
        try {
            console.log(`🧪 Testing thermal printer connection: ${ip}:${port}`);
            
            const response = await fetch('/api/wifi-thermal/test', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    ip: ip,
                    port: port
                })
            });

            const result = await response.json();
            return result.success;

        } catch (error) {
            console.error('❌ Thermal printer test error:', error);
            return false;
        }
    }

    // Print to thermal printer with ESC/POS commands
    async printToThermalPrinter(printerId, content, options = {}) {
        const startTime = Date.now();
        
        try {
            const printer = this.connectedPrinters.get(printerId);
            if (!printer) {
                throw new Error('Thermal printer not found');
            }

            if (printer.status !== 'connected') {
                throw new Error('Thermal printer not connected');
            }

            console.log(`🖨️ Printing to thermal printer: ${printer.name} (${printer.ip}:${printer.port})`);
            
            // Format content with ESC/POS commands
            const formattedContent = this.formatThermalContent(content, options);
            
            // Send print request to backend
            const response = await fetch('/api/wifi-thermal/print', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    ip: printer.ip,
                    port: printer.port,
                    content: formattedContent,
                    printerName: printer.name,
                    options: options
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const result = await response.json();
            
            if (result.success) {
                printer.lastUsed = new Date().toISOString();
                this.savePersistedConnections();
                
                const printTime = Date.now() - startTime;
                this.metrics.printTimes.push(printTime);
                this.metrics.successfulPrints++;
                
                console.log(`✅ Thermal print completed in ${printTime}ms`);
                return result;
            } else {
                throw new Error(result.error || 'Print failed');
            }

        } catch (error) {
            this.metrics.errorCount++;
            console.error('❌ Thermal print error:', error);
            throw error;
        }
    }

    // Format content for thermal printing with ESC/POS commands
    formatThermalContent(content, options = {}) {
        let formatted = '';
        
        // Initialize printer
        formatted += this.escPosCommands.init;
        
        // Set alignment
        if (options.align === 'center') {
            formatted += this.escPosCommands.center;
        } else {
            formatted += this.escPosCommands.left;
        }
        
        // Set text size
        if (options.doubleHeight) {
            formatted += this.escPosCommands.doubleHeight;
        } else {
            formatted += this.escPosCommands.normalSize;
        }
        
        // Set bold
        if (options.bold) {
            formatted += this.escPosCommands.bold;
        } else {
            formatted += this.escPosCommands.normal;
        }
        
        // Add content
        formatted += content;
        
        // Add line feeds
        if (options.lineFeeds) {
            for (let i = 0; i < options.lineFeeds; i++) {
                formatted += this.escPosCommands.feed;
            }
        } else {
            formatted += this.escPosCommands.feed;
        }
        
        // Cut paper if requested
        if (options.cutPaper) {
            formatted += this.escPosCommands.cut;
        }
        
        return formatted;
    }

    // Print receipt with thermal formatting
    async printReceipt(printerId, receiptData) {
        try {
            const receiptContent = this.generateReceiptContent(receiptData);
            
            const printOptions = {
                align: 'center',
                bold: true,
                doubleHeight: true,
                lineFeeds: 3,
                cutPaper: true
            };
            
            return await this.printToThermalPrinter(printerId, receiptContent, printOptions);
            
        } catch (error) {
            console.error('❌ Receipt print error:', error);
            throw error;
        }
    }

    // Generate receipt content
    generateReceiptContent(receiptData) {
        const now = new Date();
        const timestamp = now.toLocaleString();
        
        let content = '';
        
        // Header
        content += '================================\n';
        content += '        RECEIPT\n';
        content += '================================\n\n';
        
        // Company info
        if (receiptData.company) {
            content += `${receiptData.company}\n`;
        }
        
        // Date and time
        content += `Date: ${timestamp}\n`;
        content += `Receipt #: ${receiptData.receiptNumber || 'N/A'}\n\n`;
        
        // Items
        if (receiptData.items && receiptData.items.length > 0) {
            content += 'Items:\n';
            content += '--------------------------------\n';
            
            receiptData.items.forEach(item => {
                content += `${item.name}\n`;
                content += `Qty: ${item.quantity} x $${item.price} = $${item.total}\n\n`;
            });
            
            content += '--------------------------------\n';
        }
        
        // Total
        if (receiptData.total) {
            content += `TOTAL: $${receiptData.total}\n\n`;
        }
        
        // Footer
        content += 'Thank you for your business!\n';
        content += '================================\n';
        
        return content;
    }

    // Disconnect thermal printer
    async disconnectThermalPrinter(printerId) {
        try {
            const printer = this.connectedPrinters.get(printerId);
            if (!printer) {
                throw new Error('Thermal printer not found');
            }

            console.log(`🔌 Disconnecting thermal printer: ${printer.name} (${printer.ip})`);

            // Send disconnect request to backend
            const response = await fetch('/api/wifi-thermal/disconnect', {
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
                this.notifyConnectionStatus('disconnected', printer);
                return result;
            } else {
                throw new Error(result.error || 'Disconnect failed');
            }

        } catch (error) {
            console.error('❌ Thermal printer disconnect error:', error);
            throw error;
        }
    }

    // Get connected thermal printers
    getConnectedPrinters() {
        return Array.from(this.connectedPrinters.values()).filter(printer => 
            printer.status === 'connected'
        );
    }

    // Get all thermal printers (connected and disconnected)
    getAllPrinters() {
        return Array.from(this.connectedPrinters.values());
    }

    // Check if any thermal printer is connected
    isConnected() {
        return this.getConnectedPrinters().length > 0;
    }

    // Get connection status
    getConnectionStatus() {
        return {
            status: this.connectionStatus,
            connectedCount: this.getConnectedPrinters().length,
            totalCount: this.connectedPrinters.size,
            printers: this.getAllPrinters(),
            scanInProgress: this.scanInProgress
        };
    }

    // Get performance metrics
    getMetrics() {
        const avgScanTime = this.metrics.scanTimes.length > 0 
            ? this.metrics.scanTimes.reduce((a, b) => a + b, 0) / this.metrics.scanTimes.length 
            : 0;
        
        const avgConnectionTime = this.metrics.connectionTimes.length > 0 
            ? this.metrics.connectionTimes.reduce((a, b) => a + b, 0) / this.metrics.connectionTimes.length 
            : 0;
        
        const avgPrintTime = this.metrics.printTimes.length > 0 
            ? this.metrics.printTimes.reduce((a, b) => a + b, 0) / this.metrics.printTimes.length 
            : 0;

        return {
            avgScanTime: Math.round(avgScanTime),
            avgConnectionTime: Math.round(avgConnectionTime),
            avgPrintTime: Math.round(avgPrintTime),
            totalScans: this.metrics.scanTimes.length,
            totalConnections: this.metrics.connectionTimes.length,
            totalPrints: this.metrics.printTimes.length,
            successfulPrints: this.metrics.successfulPrints,
            errorCount: this.metrics.errorCount,
            successRate: this.metrics.printTimes.length > 0 
                ? Math.round((this.metrics.successfulPrints / this.metrics.printTimes.length) * 100) 
                : 0
        };
    }

    // Clear all connections
    clearAllConnections() {
        this.connectedPrinters.clear();
        this.scanResults.clear();
        this.savePersistedConnections();
        this.connectionStatus = 'disconnected';
        console.log('📶 Cleared all WiFi thermal printer connections');
    }

    // Notify connection status changes
    notifyConnectionStatus(type, printer) {
        const event = new CustomEvent('wifiThermalPrinterStatus', {
            detail: { 
                type, 
                printer, 
                status: this.getConnectionStatus(),
                metrics: this.getMetrics()
            }
        });
        document.dispatchEvent(event);
    }
}

// Initialize the WiFi thermal printer manager
window.wifiThermalPrinterManager = new WiFiThermalPrinterManager();

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = WiFiThermalPrinterManager;
} else {
    window.WiFiThermalPrinterManager = WiFiThermalPrinterManager;
}

