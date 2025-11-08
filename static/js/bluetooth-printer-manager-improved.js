/**
 * Enhanced Bluetooth Printer Manager
 * Improved efficiency and compatibility for all devices and browsers
 */

class EnhancedBluetoothPrinterManager {
    constructor() {
        this.connectedPrinters = new Map();
        this.connectionStatus = 'disconnected';
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 0; // Disable automatic reconnection
        this.reconnectDelay = 2000;
        this.connectionTimeout = 10000; // 10 second timeout
        this.chunkDelay = 5; // Reduced delay between chunks
        this.maxChunkSize = 244; // Optimal chunk size for BLE
        
        // Browser compatibility detection
        this.browserInfo = this.detectBrowser();
        this.supportsGetDevices = this.checkGetDevicesSupport();
        
        // Initialize from localStorage
        this.loadPersistedConnections();
        
        // Set up connection monitoring
        this.setupConnectionMonitoring();
        
        // Handle page visibility changes
        this.setupPageVisibilityHandling();
        
        // Performance metrics
        this.metrics = {
            connectionTimes: [],
            printTimes: [],
            reconnectionTimes: []
        };
    }

    // Detect browser and capabilities - simplified for universal compatibility
    detectBrowser() {
        const userAgent = navigator.userAgent;
        const isChrome = /Chrome/.test(userAgent) && !/Edg/.test(userAgent);
        const isEdge = /Edg/.test(userAgent);
        const isFirefox = /Firefox/.test(userAgent);
        const isSafari = /Safari/.test(userAgent) && !/Chrome/.test(userAgent);
        const isOpera = /OPR/.test(userAgent);
        const isOperaMini = /Opera Mini/.test(userAgent);
        const isMobile = /Android|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(userAgent);
        
        // Universal Bluetooth support check
        const supportsBluetooth = !!(navigator.bluetooth && typeof navigator.bluetooth.requestDevice === 'function');
        
        return {
            isChrome,
            isEdge,
            isFirefox,
            isSafari,
            isOpera,
            isOperaMini,
            isMobile,
            name: isChrome ? 'Chrome' : isEdge ? 'Edge' : isFirefox ? 'Firefox' : isSafari ? 'Safari' : isOpera ? 'Opera' : isOperaMini ? 'Opera Mini' : 'Unknown',
            supportsBluetooth,
            supportsSerial: !!(navigator.serial && typeof navigator.serial.requestPort === 'function'),
            // Universal compatibility flag
            isCompatible: supportsBluetooth
        };
    }

    // Check if getDevices() is supported
    checkGetDevicesSupport() {
        return !!(navigator.bluetooth && typeof navigator.bluetooth.getDevices === 'function');
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
                
                // Try to reconnect to previously paired devices using getDevices()
                this.attemptReconnectFromPairedDevices();
            }
        } catch (error) {
            console.error('Error loading persisted connections:', error);
        }
    }

    // Attempt to reconnect to previously paired devices without showing picker
    async attemptReconnectFromPairedDevices() {
        if (!this.supportsGetDevices) {
            console.log('getDevices() not supported, skipping auto-reconnect');
            return;
        }

        try {
            const pairedDevices = await navigator.bluetooth.getDevices();
            console.log('Found previously paired devices:', pairedDevices.length);

            // Try to match paired devices with saved printer connections
            for (const [printerId, printer] of this.connectedPrinters.entries()) {
                if (printer.status === 'disconnected') {
                    // Find matching paired device
                    const matchedDevice = pairedDevices.find(d => 
                        d.id === printer.deviceId || d.name === printer.name
                    );

                    if (matchedDevice) {
                        console.log(`Attempting to reconnect to ${printer.name} from paired devices...`);
                        try {
                            // Try to reconnect without showing picker
                            if (matchedDevice.gatt.connected) {
                                // Already connected, just use it
                                printer.device = matchedDevice;
                                printer.server = matchedDevice.gatt;
                                printer.status = 'connected';
                                this.setupDeviceEventListeners(matchedDevice, printerId);
                                console.log(`Successfully reconnected to ${printer.name}`);
                                this.notifyConnectionStatus('reconnected', printer);
                            } else {
                                // Connect to GATT server
                                const server = await this.connectWithTimeout(matchedDevice);
                                printer.device = matchedDevice;
                                printer.server = server;
                                printer.status = 'connected';
                                this.setupDeviceEventListeners(matchedDevice, printerId);
                                console.log(`Successfully reconnected to ${printer.name}`);
                                this.notifyConnectionStatus('reconnected', printer);
                            }
                        } catch (error) {
                            console.log(`Failed to reconnect to ${printer.name}:`, error.message);
                        }
                    }
                }
            }

            this.savePersistedConnections();
        } catch (error) {
            console.error('Error attempting reconnect from paired devices:', error);
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

    // Add a new Bluetooth printer connection with improved error handling
    // Supports multiple devices connecting to the same printer
    async addPrinter(device, server) {
        try {
            const deviceName = device.name || 'Bluetooth Printer';
            
            // Check if this device is already connected (by deviceId)
            const existingPrinter = Array.from(this.connectedPrinters.values()).find(
                p => p.deviceId === device.id && p.status === 'connected'
            );

            if (existingPrinter) {
                console.log(`Printer ${deviceName} already connected as ${existingPrinter.id}, updating connection...`);
                // Update existing connection
                existingPrinter.device = device;
                existingPrinter.server = server;
                existingPrinter.status = 'connected';
                existingPrinter.lastUsed = new Date().toISOString();
                this.setupDeviceEventListeners(device, existingPrinter.id);
                this.savePersistedConnections();
                return existingPrinter.id;
            }

            // Create new printer connection
            const printerId = `ble_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
            
            const printerData = {
                id: printerId,
                name: deviceName,
                deviceId: device.id,
                device: device,
                server: server,
                status: 'connected',
                connectedAt: new Date().toISOString(),
                lastUsed: new Date().toISOString(),
                browserInfo: this.browserInfo.name,
                connectionAttempts: 0,
                shared: true // Allow sharing across tabs/devices
            };

            this.connectedPrinters.set(printerId, printerData);
            this.savePersistedConnections();
            
            // Set up device event listeners
            this.setupDeviceEventListeners(device, printerId);
            
            // Broadcast connection to other tabs
            this.broadcastConnectionState('connected', printerData);
            
            console.log(`Added printer: ${deviceName} (${printerId})`);
            return printerId;
            
        } catch (error) {
            console.error('Error adding printer:', error);
            throw error;
        }
    }

    // Broadcast connection state to other tabs/devices
    broadcastConnectionState(type, printer) {
        try {
            // Use BroadcastChannel for cross-tab communication
            if (typeof BroadcastChannel !== 'undefined') {
                if (!this.broadcastChannel) {
                    this.broadcastChannel = new BroadcastChannel('bluetooth-printer-connections');
                    this.broadcastChannel.onmessage = (event) => {
                        this.handleBroadcastMessage(event.data);
                    };
                }

                this.broadcastChannel.postMessage({
                    type: type,
                    printer: {
                        id: printer.id,
                        name: printer.name,
                        deviceId: printer.deviceId,
                        status: printer.status,
                        connectedAt: printer.connectedAt,
                        lastUsed: printer.lastUsed
                    }
                });
            }
        } catch (error) {
            console.error('Error broadcasting connection state:', error);
        }
    }

    // Handle broadcast messages from other tabs
    handleBroadcastMessage(data) {
        if (data.type === 'connected' || data.type === 'reconnected') {
            // Another tab connected to a printer - update our state
            const existingPrinter = Array.from(this.connectedPrinters.values()).find(
                p => p.deviceId === data.printer.deviceId
            );

            if (existingPrinter && existingPrinter.status === 'disconnected') {
                // Update disconnected printer info
                existingPrinter.name = data.printer.name;
                existingPrinter.connectedAt = data.printer.connectedAt;
                existingPrinter.lastUsed = data.printer.lastUsed;
                console.log(`Received connection update from another tab for ${data.printer.name}`);
                this.savePersistedConnections();
            }
        } else if (data.type === 'disconnected') {
            // Another tab disconnected - update our state
            const existingPrinter = Array.from(this.connectedPrinters.values()).find(
                p => p.deviceId === data.printer.deviceId
            );

            if (existingPrinter) {
                existingPrinter.status = 'disconnected';
                existingPrinter.device = null;
                existingPrinter.server = null;
                console.log(`Received disconnection update from another tab for ${data.printer.name}`);
                this.savePersistedConnections();
            }
        }
    }

    // Set up device event listeners for connection monitoring
    setupDeviceEventListeners(device, printerId) {
        device.addEventListener('gattserverdisconnected', () => {
            console.log(`Printer ${printerId} disconnected`);
            this.handleDisconnection(printerId);
        });
    }

    // Handle printer disconnection with improved reconnection logic
    handleDisconnection(printerId) {
        const printer = this.connectedPrinters.get(printerId);
        if (printer) {
            printer.status = 'disconnected';
            printer.device = null;
            printer.server = null;
            printer.connectionAttempts = (printer.connectionAttempts || 0) + 1;
            
            // Broadcast disconnection to other tabs
            this.broadcastConnectionState('disconnected', printer);
            
            // Try to reconnect using getDevices() if supported
            if (this.supportsGetDevices && printer.shared) {
                console.log(`Printer ${printerId} disconnected. Attempting to reconnect from paired devices...`);
                this.attemptReconnectFromPairedDevices();
            } else {
                console.log(`Printer ${printerId} disconnected.`);
            }
            
            this.notifyConnectionStatus('disconnected', printer);
            this.savePersistedConnections();
        }
    }

    // Attempt to reconnect all previously paired devices with improved efficiency
    async attemptReconnectAll() {
        console.log('Attempting to reconnect all previously paired devices...');
        
        // Use Promise.allSettled for parallel reconnection attempts
        const reconnectionPromises = Array.from(this.connectedPrinters.entries())
            .filter(([_, printer]) => printer.status === 'disconnected')
            .map(([printerId, printer], index) => {
                // Stagger reconnection attempts to avoid conflicts
                return new Promise(resolve => {
                    setTimeout(() => {
                        this.attemptReconnection(printerId).then(resolve).catch(resolve);
                    }, index * 1000); // 1 second delay between attempts
                });
            });

        await Promise.allSettled(reconnectionPromises);
    }

    // Enhanced reconnection with better error handling and timeout
    // Now supports reconnecting without showing picker if device is already paired
    async attemptReconnection(printerId, showPicker = false) {
        const printer = this.connectedPrinters.get(printerId);
        if (!printer) {
            return false;
        }

        console.log(`Attempting to reconnect printer ${printerId}: ${printer.name}`);

        try {
            // First, try to get previously paired devices without showing picker
            let device = null;
            
            if (this.supportsGetDevices && !showPicker) {
                try {
                    const pairedDevices = await navigator.bluetooth.getDevices();
                    device = pairedDevices.find(d => d.id === printer.deviceId || d.name === printer.name);
                    console.log(`Found paired device for ${printer.name}:`, device ? 'Yes' : 'No');
                    
                    // If device is already connected, just use it
                    if (device && device.gatt.connected) {
                        printer.device = device;
                        printer.server = device.gatt;
                        printer.status = 'connected';
                        printer.lastUsed = new Date().toISOString();
                        printer.connectionAttempts = 0;
                        this.setupDeviceEventListeners(device, printerId);
                        this.broadcastConnectionState('reconnected', printer);
                        this.savePersistedConnections();
                        console.log(`Successfully reconnected to already-connected device ${printer.name}`);
                        return true;
                    }
                } catch (error) {
                    console.log('getDevices() failed, falling back to requestDevice');
                }
            }

            // If not found in paired devices or showPicker is true, try requestDevice (this will show picker)
            if (!device || showPicker) {
                console.log(`Device not found in paired devices or picker requested, requesting device selection for ${printer.name}`);
                device = await this.requestDeviceWithTimeout();
            }

            // Check if this is the device we're looking for
            if (device && (device.id === printer.deviceId || device.name === printer.name)) {
                const connectionStartTime = Date.now();
                
                // Connect only if not already connected
                let server;
                if (device.gatt.connected) {
                    server = device.gatt;
                } else {
                    server = await this.connectWithTimeout(device);
                }
                
                const connectionTime = Date.now() - connectionStartTime;
                
                // Record connection time
                this.metrics.connectionTimes.push(connectionTime);
                
                printer.device = device;
                printer.server = server;
                printer.status = 'connected';
                printer.lastUsed = new Date().toISOString();
                printer.connectionAttempts = 0; // Reset attempts on successful connection
                
                this.setupDeviceEventListeners(device, printerId);
                
                // Broadcast reconnection to other tabs
                this.broadcastConnectionState('reconnected', printer);
                
                console.log(`Successfully reconnected printer ${printerId}: ${printer.name} in ${connectionTime}ms`);
                this.notifyConnectionStatus('reconnected', printer);
                this.savePersistedConnections();
                return true;
            } else {
                console.log(`Device found but doesn't match printer ${printerId}`);
                return false;
            }
        } catch (error) {
            console.log(`Reconnection attempt failed for printer ${printerId}:`, error.message);
            this.notifyConnectionStatus('failed', printer);
            return false;
        }
    }

    // Universal device request with simplified options for maximum compatibility
    async requestDeviceWithTimeout() {
        try {
            // Simplified request for universal compatibility
            const device = await navigator.bluetooth.requestDevice({
                acceptAllDevices: true,
                optionalServices: [
                    '000018f0-0000-1000-8000-00805f9b34fb', // BLE thermal printer
                    '00001800-0000-1000-8000-00805f9b34fb', // Generic Access
                    '00001801-0000-1000-8000-00805f9b34fb', // Generic Attribute
                    '00001101-0000-1000-8000-00805f9b34fb', // Serial Port Profile
                    '0000ffe0-0000-1000-8000-00805f9b34fb', // Custom service
                    '0000ffe1-0000-1000-8000-00805f9b34fb'  // Custom characteristic
                ]
            });
            return device;
        } catch (error) {
            // Enhanced error handling for different scenarios
            if (error.name === 'SecurityError') {
                throw new Error('Bluetooth access denied. Please allow Bluetooth permissions in your browser settings.');
            } else if (error.name === 'NotFoundError') {
                // Check if it's user cancellation
                if (error.message && error.message.includes('cancelled')) {
                    // User cancelled - this is normal, don't throw error
                    console.log('User cancelled Bluetooth device selection');
                    return null;
                } else {
                    throw new Error('No Bluetooth devices found. Make sure your printer is in pairing mode and nearby.');
                }
            } else if (error.name === 'NotSupportedError') {
                throw new Error('Bluetooth not supported on this device. Please use a compatible browser.');
            } else {
                throw new Error(`Bluetooth error: ${error.message}`);
            }
        }
    }

    // Connect with timeout
    async connectWithTimeout(device) {
        return Promise.race([
            device.gatt.connect(),
            new Promise((_, reject) => 
                setTimeout(() => reject(new Error('Connection timeout')), this.connectionTimeout)
            )
        ]);
    }

    // Set up connection monitoring with improved efficiency
    setupConnectionMonitoring() {
        // Check connection status every 30 seconds
        setInterval(() => {
            this.checkConnections();
        }, 30000);
    }

    // Check all connections with improved error handling
    async checkConnections() {
        const checkPromises = Array.from(this.connectedPrinters.entries())
            .filter(([_, printer]) => printer.status === 'connected' && printer.device)
            .map(async ([printerId, printer]) => {
                try {
                    // Check if device is still connected
                    if (!printer.device.gatt.connected) {
                        this.handleDisconnection(printerId);
                    }
                } catch (error) {
                    console.log(`Connection check failed for printer ${printerId}:`, error);
                    this.handleDisconnection(printerId);
                }
            });

        await Promise.allSettled(checkPromises);
    }

    // Set up page visibility handling
    setupPageVisibilityHandling() {
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                // Page became visible, check connections
                this.checkConnections();
            }
        });

        // Handle page load - auto-reconnection disabled
        window.addEventListener('load', () => {
            console.log('Page loaded. Auto-reconnection disabled - user must manually connect printers.');
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

    // Print to a specific printer with improved performance
    async printToPrinter(printerId, content) {
        const printer = this.connectedPrinters.get(printerId);
        if (!printer || printer.status !== 'connected') {
            throw new Error('Printer not connected');
        }

        try {
            const printStartTime = Date.now();
            const success = await this.sendPrintData(printer, content);
            const printTime = Date.now() - printStartTime;
            
            // Record print time
            this.metrics.printTimes.push(printTime);
            
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

    // Print to all connected printers with parallel processing
    async printToAllPrinters(content) {
        const connectedPrinters = this.getConnectedPrinters();
        if (connectedPrinters.length === 0) {
            throw new Error('No printers connected');
        }

        // Process all printers in parallel for better performance
        const printPromises = connectedPrinters.map(async (printer) => {
            try {
                const success = await this.printToPrinter(printer.id, content);
                return { printerId: printer.id, success };
            } catch (error) {
                return { printerId: printer.id, success: false, error: error.message };
            }
        });

        return Promise.all(printPromises);
    }

    // Enhanced send print data with better error handling and performance
    async sendPrintData(printer, content) {
        try {
            if (!printer.device.gatt.connected) {
                throw new Error('Device not connected');
            }

            let service, characteristic;
            
            // Try to find the correct service and characteristic with improved fallback
            const serviceAttempts = [
                { service: '000018f0-0000-1000-8000-00805f9b34fb', characteristic: '00002af1-0000-1000-8000-00805f9b34fb' },
                { service: '00001101-0000-1000-8000-00805f9b34fb', characteristic: '00001102-0000-1000-8000-00805f9b34fb' },
                { service: '0000ffe0-0000-1000-8000-00805f9b34fb', characteristic: '0000ffe1-0000-1000-8000-00805f9b34fb' }
            ];

            for (const attempt of serviceAttempts) {
                try {
                    service = await printer.server.getPrimaryService(attempt.service);
                    characteristic = await service.getCharacteristic(attempt.characteristic);
                    break;
                } catch (e) {
                    continue;
                }
            }

            // If no specific service found, search for any writable characteristic
            if (!characteristic) {
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
                    } catch (e) {
                        continue;
                    }
                }
            }

            if (!characteristic) {
                throw new Error('No writable characteristic found');
            }

            const encoder = new TextEncoder();
            const data = encoder.encode(content);

            // Send data in optimized chunks
            for (let i = 0; i < data.length; i += this.maxChunkSize) {
                const chunk = data.slice(i, i + this.maxChunkSize);
                await characteristic.writeValue(chunk);
                if (i + this.maxChunkSize < data.length) {
                    await new Promise(resolve => setTimeout(resolve, this.chunkDelay));
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

    // Get performance metrics
    getMetrics() {
        const avgConnectionTime = this.metrics.connectionTimes.length > 0 
            ? this.metrics.connectionTimes.reduce((a, b) => a + b, 0) / this.metrics.connectionTimes.length 
            : 0;
        
        const avgPrintTime = this.metrics.printTimes.length > 0 
            ? this.metrics.printTimes.reduce((a, b) => a + b, 0) / this.metrics.printTimes.length 
            : 0;

        return {
            browser: this.browserInfo,
            avgConnectionTime: Math.round(avgConnectionTime),
            avgPrintTime: Math.round(avgPrintTime),
            totalConnections: this.metrics.connectionTimes.length,
            totalPrints: this.metrics.printTimes.length
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
window.enhancedBluetoothPrinterManager = new EnhancedBluetoothPrinterManager();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = EnhancedBluetoothPrinterManager;
}
