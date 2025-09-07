/**
 * Global Bluetooth Printer Manager
 * Manages persistent Bluetooth printer connections across all pages
 */

class BluetoothPrinterManager {
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
        
        // Handle page visibility changes
        this.setupPageVisibilityHandling();
    }

    // Load persisted connections from localStorage
    loadPersistedConnections() {
        try {
            const savedConnections = localStorage.getItem('bluetooth-printer-connections');
            if (savedConnections) {
                const connections = JSON.parse(savedConnections);
                console.log('Loading persisted printer connections:', connections);
                
                // Store connection info for auto-reconnection
                connections.forEach(connection => {
                    this.connectedPrinters.set(connection.id, {
                        ...connection,
                        device: null, // Will be reconnected when needed
                        server: null,
                        status: 'disconnected'
                    });
                });
                
                // Attempt to reconnect to previously paired devices
                this.attemptReconnectAll();
            }
        } catch (error) {
            console.error('Error loading persisted connections:', error);
        }
    }

    // Save connections to localStorage
    savePersistedConnections() {
        try {
            const connections = Array.from(this.connectedPrinters.values()).map(printer => ({
                id: printer.id,
                name: printer.name,
                deviceId: printer.deviceId,
                connectedAt: printer.connectedAt,
                lastUsed: printer.lastUsed
            }));
            
            localStorage.setItem('bluetooth-printer-connections', JSON.stringify(connections));
            console.log('Saved printer connections to localStorage:', connections);
        } catch (error) {
            console.error('Error saving connections:', error);
        }
    }

    // Add a new Bluetooth printer connection
    async addPrinter(device, server) {
        try {
            const deviceName = device.name || 'Bluetooth Printer';
            const printerId = `ble_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
            
            const printerData = {
                id: printerId,
                name: deviceName,
                deviceId: device.id,
                device: device,
                server: server,
                status: 'connected',
                connectedAt: new Date().toISOString(),
                lastUsed: new Date().toISOString()
            };

            this.connectedPrinters.set(printerId, printerData);
            this.savePersistedConnections();
            
            // Set up device event listeners
            this.setupDeviceEventListeners(device, printerId);
            
            console.log(`Added printer: ${deviceName} (${printerId})`);
            return printerId;
            
        } catch (error) {
            console.error('Error adding printer:', error);
            throw error;
        }
    }

    // Set up device event listeners for connection monitoring
    setupDeviceEventListeners(device, printerId) {
        device.addEventListener('gattserverdisconnected', () => {
            console.log(`Printer ${printerId} disconnected`);
            this.handleDisconnection(printerId);
        });
    }

    // Handle printer disconnection
    handleDisconnection(printerId) {
        const printer = this.connectedPrinters.get(printerId);
        if (printer) {
            printer.status = 'disconnected';
            printer.device = null;
            printer.server = null;
            
            // Attempt to reconnect
            this.attemptReconnection(printerId);
        }
    }

    // Attempt to reconnect all previously paired devices
    async attemptReconnectAll() {
        console.log('Attempting to reconnect all previously paired devices...');
        
        for (const [printerId, printer] of this.connectedPrinters) {
            if (printer.status === 'disconnected') {
                // Add a small delay between reconnection attempts
                setTimeout(() => {
                    this.attemptReconnection(printerId);
                }, 1000 * Math.random()); // Random delay to avoid conflicts
            }
        }
    }

    // Attempt to reconnect a disconnected printer
    async attemptReconnection(printerId) {
        const printer = this.connectedPrinters.get(printerId);
        if (!printer) {
            return;
        }

        console.log(`Attempting to reconnect printer ${printerId}: ${printer.name}`);

        try {
            // First, try to get previously paired devices without showing picker
            let device = null;
            
            try {
                const pairedDevices = await navigator.bluetooth.getDevices();
                device = pairedDevices.find(d => d.id === printer.deviceId || d.name === printer.name);
                console.log(`Found paired device for ${printer.name}:`, device ? 'Yes' : 'No');
            } catch (error) {
                console.log('getDevices() not supported, falling back to requestDevice');
            }

            // If not found in paired devices, try requestDevice (this will show picker)
            if (!device) {
                console.log(`Device not found in paired devices, requesting device selection for ${printer.name}`);
                device = await navigator.bluetooth.requestDevice({
                    acceptAllDevices: true,
                    optionalServices: [
                        '000018f0-0000-1000-8000-00805f9b34fb',
                        '00001800-0000-1000-8000-00805f9b34fb',
                        '00001801-0000-1000-8000-00805f9b34fb',
                        '00001101-0000-1000-8000-00805f9b34fb',
                        '0000ffe0-0000-1000-8000-00805f9b34fb',
                        '0000ffe1-0000-1000-8000-00805f9b34fb'
                    ]
                });
            }

            // Check if this is the device we're looking for
            if (device && (device.id === printer.deviceId || device.name === printer.name)) {
                const server = await device.gatt.connect();
                printer.device = device;
                printer.server = server;
                printer.status = 'connected';
                printer.lastUsed = new Date().toISOString();
                
                this.setupDeviceEventListeners(device, printerId);
                
                console.log(`Successfully reconnected printer ${printerId}: ${printer.name}`);
                this.notifyConnectionStatus('reconnected', printer);
                this.savePersistedConnections();
            } else {
                console.log(`Device found but doesn't match printer ${printerId}`);
            }
        } catch (error) {
            console.log(`Reconnection attempt failed for printer ${printerId}:`, error.message);
            // Don't retry automatically on page load - let user manually reconnect if needed
        }
    }

    // Set up connection monitoring
    setupConnectionMonitoring() {
        // Check connection status every 30 seconds
        setInterval(() => {
            this.checkConnections();
        }, 30000);
    }

    // Check all connections
    async checkConnections() {
        for (const [printerId, printer] of this.connectedPrinters) {
            if (printer.status === 'connected' && printer.device) {
                try {
                    // Check if device is still connected
                    if (!printer.device.gatt.connected) {
                        this.handleDisconnection(printerId);
                    }
                } catch (error) {
                    console.log(`Connection check failed for printer ${printerId}:`, error);
                    this.handleDisconnection(printerId);
                }
            }
        }
    }

    // Set up page visibility handling
    setupPageVisibilityHandling() {
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                // Page became visible, check connections
                this.checkConnections();
            }
        });

        // Handle page load - attempt to reconnect previously paired devices
        window.addEventListener('load', () => {
            console.log('Page loaded, checking for previously paired devices...');
            this.attemptReconnectAll();
        });

        // Handle beforeunload to clean up connections
        window.addEventListener('beforeunload', () => {
            console.log('Page unloading, cleaning up connections...');
            // Note: We don't disconnect here as we want to maintain connections
        });
    }

    // Get all connected printers
    getConnectedPrinters() {
        return Array.from(this.connectedPrinters.values()).filter(printer => 
            printer.status === 'connected' && printer.device && printer.server
        );
    }

    // Get printer by ID
    getPrinter(printerId) {
        return this.connectedPrinters.get(printerId);
    }

    // Print to a specific printer
    async printToPrinter(printerId, content) {
        const printer = this.connectedPrinters.get(printerId);
        if (!printer || printer.status !== 'connected') {
            throw new Error('Printer not connected');
        }

        try {
            const success = await this.sendPrintData(printer, content);
            if (success) {
                printer.lastUsed = new Date().toISOString();
                this.savePersistedConnections();
            }
            return success;
        } catch (error) {
            console.error(`Print failed for printer ${printerId}:`, error);
            throw error;
        }
    }

    // Print to all connected printers
    async printToAllPrinters(content) {
        const connectedPrinters = this.getConnectedPrinters();
        if (connectedPrinters.length === 0) {
            throw new Error('No printers connected');
        }

        const results = [];
        for (const printer of connectedPrinters) {
            try {
                const success = await this.printToPrinter(printer.id, content);
                results.push({ printerId: printer.id, success });
            } catch (error) {
                results.push({ printerId: printer.id, success: false, error: error.message });
            }
        }

        return results;
    }

    // Send print data to printer
    async sendPrintData(printer, content) {
        try {
            if (!printer.device.gatt.connected) {
                throw new Error('Device not connected');
            }

            let service, characteristic;
            
            try {
                service = await printer.server.getPrimaryService('000018f0-0000-1000-8000-00805f9b34fb');
                characteristic = await service.getCharacteristic('00002af1-0000-1000-8000-00805f9b34fb');
            } catch (e) {
                try {
                    service = await printer.server.getPrimaryService('00001101-0000-1000-8000-00805f9b34fb');
                    characteristic = await service.getCharacteristic('00001102-0000-1000-8000-00805f9b34fb');
                } catch (e2) {
                    try {
                        service = await printer.server.getPrimaryService('0000ffe0-0000-1000-8000-00805f9b34fb');
                        characteristic = await service.getCharacteristic('0000ffe1-0000-1000-8000-00805f9b34fb');
                    } catch (e3) {
                        const services = await printer.server.getPrimaryServices();
                        for (const svc of services) {
                            try {
                                const characteristics = await svc.getCharacteristics();
                                for (const char of characteristics) {
                                    if (char.properties.write || char.properties.writeWithoutResponse) {
                                        service = svc;
                                        characteristic = char;
                                        break;
                                    }
                                }
                                if (characteristic) break;
                            } catch (e4) {
                                continue;
                            }
                        }
                    }
                }
            }

            if (!characteristic) {
                throw new Error('No writable characteristic found');
            }

            const encoder = new TextEncoder();
            const data = encoder.encode(content);

            const chunkSize = 244;
            for (let i = 0; i < data.length; i += chunkSize) {
                const chunk = data.slice(i, i + chunkSize);
                await characteristic.writeValue(chunk);
                if (i + chunkSize < data.length) {
                    await new Promise(resolve => setTimeout(resolve, 10));
                }
            }

            return true;

        } catch (error) {
            console.error('Print data send error:', error);
            throw error;
        }
    }

    // Disconnect a specific printer
    disconnectPrinter(printerId) {
        const printer = this.connectedPrinters.get(printerId);
        if (printer && printer.device) {
            if (printer.device.gatt.connected) {
                printer.device.gatt.disconnect();
            }
            this.connectedPrinters.delete(printerId);
            this.savePersistedConnections();
            console.log(`Disconnected printer ${printerId}`);
        }
    }

    // Disconnect all printers
    disconnectAllPrinters() {
        for (const [printerId, printer] of this.connectedPrinters) {
            if (printer.device && printer.device.gatt.connected) {
                printer.device.gatt.disconnect();
            }
        }
        this.connectedPrinters.clear();
        this.savePersistedConnections();
        console.log('Disconnected all printers');
    }

    // Get connection status
    getConnectionStatus() {
        const connectedCount = this.getConnectedPrinters().length;
        return {
            connected: connectedCount,
            total: this.connectedPrinters.size,
            status: connectedCount > 0 ? 'connected' : 'disconnected'
        };
    }

    // Notify connection status changes
    notifyConnectionStatus(type, printer) {
        const event = new CustomEvent('bluetoothPrinterStatus', {
            detail: { type, printer, status: this.getConnectionStatus() }
        });
        document.dispatchEvent(event);
        
        // Also show user notification
        this.showReconnectionNotification(type, printer);
    }

    // Show reconnection notification to user
    showReconnectionNotification(type, printer) {
        let message = '';
        let notificationType = 'info';
        
        switch (type) {
            case 'reconnected':
                message = `Printer "${printer.name}" reconnected successfully!`;
                notificationType = 'success';
                break;
            case 'failed':
                message = `Failed to reconnect printer "${printer.name}". Please check if it's powered on and in range.`;
                notificationType = 'warning';
                break;
            case 'disconnected':
                message = `Printer "${printer.name}" disconnected. Attempting to reconnect...`;
                notificationType = 'warning';
                break;
            default:
                return;
        }
        
        // Dispatch a custom event for notifications
        const notificationEvent = new CustomEvent('showNotification', {
            detail: { message, type: notificationType }
        });
        document.dispatchEvent(notificationEvent);
    }

    // Generate test receipt
    generateTestReceipt() {
        const now = new Date();
        const dateStr = now.toLocaleDateString();
        const timeStr = now.toLocaleTimeString();
        
        let receipt = '';
        receipt += '\x1B\x40'; // Initialize printer
        receipt += '\x1B\x61\x01'; // Center alignment
        receipt += '\x1B\x21\x30'; // Double height and width
        receipt += 'PRINTER TEST\n';
        receipt += '\x1B\x21\x00'; // Normal text
        receipt += 'HOTEL POS\n';
        receipt += '\x1B\x61\x00'; // Left alignment
        receipt += '================================\n';
        receipt += `Date: ${dateStr}\n`;
        receipt += `Time: ${timeStr}\n`;
        receipt += 'Test Type: Bluetooth BLE\n';
        receipt += 'Status: SUCCESS\n';
        receipt += '================================\n';
        receipt += 'This is a test print to verify\n';
        receipt += 'Bluetooth connectivity and\n';
        receipt += 'printer functionality.\n';
        receipt += '================================\n';
        receipt += '\n\n\n';
        receipt += '\x1D\x56\x00'; // Cut paper
        
        return receipt;
    }
}

// Create global instance
window.bluetoothPrinterManager = new BluetoothPrinterManager();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = BluetoothPrinterManager;
}
