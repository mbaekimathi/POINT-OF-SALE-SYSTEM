/**
 * Separate Bluetooth Printer Manager
 * Handles only Bluetooth printer connections and operations
 */

class SeparateBluetoothManager {
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
        
        console.log('🔵 Separate Bluetooth Manager initialized');
    }

    // Load persisted connections from localStorage
    loadPersistedConnections() {
        try {
            const stored = localStorage.getItem('bluetooth_printers');
            if (stored) {
                const printers = JSON.parse(stored);
                printers.forEach(printer => {
                    this.connectedPrinters.set(printer.id, {
                        ...printer,
                        status: 'disconnected' // Reset status on load
                    });
                });
                console.log(`🔵 Loaded ${printers.length} persisted Bluetooth printers`);
            }
        } catch (error) {
            console.error('Error loading persisted Bluetooth connections:', error);
        }
    }

    // Save connections to localStorage
    savePersistedConnections() {
        try {
            const printers = Array.from(this.connectedPrinters.values()).map(printer => ({
                id: printer.id,
                name: printer.name,
                deviceId: printer.deviceId,
                connectedAt: printer.connectedAt,
                lastUsed: printer.lastUsed
            }));
            localStorage.setItem('bluetooth_printers', JSON.stringify(printers));
        } catch (error) {
            console.error('Error saving Bluetooth connections:', error);
        }
    }

    // Scan for Bluetooth printers
    async scanForPrinters() {
        try {
            console.log('🔍 Scanning for Bluetooth printers...');
            
            // Check if Bluetooth is available
            if (!navigator.bluetooth) {
                throw new Error('Bluetooth not supported in this browser');
            }

            // Request Bluetooth device with comprehensive service list
            const device = await navigator.bluetooth.requestDevice({
                filters: [
                    { namePrefix: 'Printer' },
                    { namePrefix: 'POS' },
                    { namePrefix: 'Thermal' },
                    { namePrefix: 'Receipt' },
                    { namePrefix: 'MPT' },
                    { namePrefix: 'CloudPrint' }
                ],
                optionalServices: [
                    // Standard services
                    '0000180a-0000-1000-8000-00805f9b34fb', // Device Information
                    '00001800-0000-1000-8000-00805f9b34fb', // Generic Access
                    '00001801-0000-1000-8000-00805f9b34fb', // Generic Attribute
                    
                    // Thermal printer services
                    '0000ffe0-0000-1000-8000-00805f9b34fb', // Custom service (common for thermal printers)
                    '0000ffe1-0000-1000-8000-00805f9b34fb', // Custom characteristic
                    '000018f0-0000-1000-8000-00805f9b34fb', // BLE thermal printer service
                    '00001101-0000-1000-8000-00805f9b34fb', // Serial Port Profile
                    '0000ff00-0000-1000-8000-00805f9b34fb', // Generic printer service
                    '0000ff01-0000-1000-8000-00805f9b34fb', // Generic printer characteristic
                    
                    // Additional thermal printer services
                    '0000ff10-0000-1000-8000-00805f9b34fb', // Thermal printer service 1
                    '0000ff20-0000-1000-8000-00805f9b34fb', // Thermal printer service 2
                    '0000ff30-0000-1000-8000-00805f9b34fb'  // Thermal printer service 3
                ]
            });

            console.log('🔵 Found Bluetooth device:', device.name);
            return device;

        } catch (error) {
            console.error('❌ Bluetooth scan error:', error);
            throw error;
        }
    }

    // Connect to a Bluetooth printer
    async connectToPrinter(device) {
        try {
            console.log(`🔗 Connecting to Bluetooth printer: ${device.name}`);
            
            // Connect to GATT server
            const server = await device.gatt.connect();
            console.log('🔵 Connected to GATT server');

            // Add printer to connected list
            const printerId = await this.addPrinter(device, server);
            
            this.connectionStatus = 'connected';
            this.reconnectAttempts = 0;
            
            console.log(`✅ Successfully connected to ${device.name}`);
            return printerId;

        } catch (error) {
            console.error('❌ Bluetooth connection error:', error);
            this.connectionStatus = 'disconnected';
            throw error;
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
            
            console.log(`🔵 Added printer: ${deviceName} (${printerId})`);
            return printerId;
            
        } catch (error) {
            console.error('Error adding Bluetooth printer:', error);
            throw error;
        }
    }

    // Set up device event listeners for connection monitoring
    setupDeviceEventListeners(device, printerId) {
        device.addEventListener('gattserverdisconnected', () => {
            console.log(`🔌 Bluetooth printer ${printerId} disconnected`);
            this.handleDisconnection(printerId);
        });
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
                if (printer && printer.device) {
                    await printer.device.gatt.connect();
                    printer.status = 'connected';
                    this.connectionStatus = 'connected';
                    this.reconnectAttempts = 0;
                    console.log(`✅ Reconnected to ${printer.name}`);
                }
            } catch (error) {
                console.error(`❌ Reconnection failed for ${printerId}:`, error);
                this.attemptReconnection(printerId);
            }
        }, this.reconnectDelay);
    }

    // Print to Bluetooth printer
    async printToBluetoothPrinter(printerId, content) {
        try {
            const printer = this.connectedPrinters.get(printerId);
            if (!printer) {
                throw new Error('Printer not found');
            }

            if (printer.status !== 'connected') {
                throw new Error('Printer not connected');
            }

            console.log(`🖨️ Printing to Bluetooth printer: ${printer.name}`);
            
            // Actually send data to the Bluetooth device
            await this.sendDataToBluetoothDevice(printer, content);
            
            printer.lastUsed = new Date().toISOString();
            this.savePersistedConnections();
            console.log(`✅ Print job sent to ${printer.name}`);
            
            return {
                success: true,
                message: `Print job sent to ${printer.name}`,
                bytes_sent: content.length
            };

        } catch (error) {
            console.error('❌ Bluetooth print error:', error);
            throw error;
        }
    }

    // Send data to Bluetooth device
    async sendDataToBluetoothDevice(printer, content) {
        try {
            if (!printer.server || !printer.server.connected) {
                throw new Error('Bluetooth device not connected');
            }

            console.log(`📤 Attempting to send data to Bluetooth device: ${printer.name}`);
            console.log(`📡 Server connected: ${printer.server.connected}`);

            // Convert content to bytes if it's a string
            let data;
            if (typeof content === 'string') {
                data = new TextEncoder().encode(content);
            } else {
                data = content;
            }

            console.log(`📤 Sending ${data.length} bytes to Bluetooth device...`);

            // Try to find a suitable service and characteristic for printing
            const services = await printer.server.getPrimaryServices();
            console.log(`📡 Found ${services.length} services`);
            
            let printService = null;
            let printCharacteristic = null;

            // Look for common thermal printer services
            const thermalPrinterServices = [
                '0000ffe0-0000-1000-8000-00805f9b34fb', // Custom service (common for thermal printers)
                '0000ffe1-0000-1000-8000-00805f9b34fb', // Custom characteristic
                '000018f0-0000-1000-8000-00805f9b34fb', // BLE thermal printer service
                '00001101-0000-1000-8000-00805f9b34fb', // Serial Port Profile
                '0000ff00-0000-1000-8000-00805f9b34fb', // Generic printer service
                '0000ff01-0000-1000-8000-00805f9b34fb'  // Generic printer characteristic
            ];
            
            for (const service of services) {
                console.log(`📡 Checking service: ${service.uuid}`);
                const characteristics = await service.getCharacteristics();
                console.log(`📡 Found ${characteristics.length} characteristics in service ${service.uuid}`);
                
                // Check if this is a known thermal printer service
                const isThermalService = thermalPrinterServices.includes(service.uuid.toLowerCase());
                console.log(`📡 Is thermal printer service: ${isThermalService}`);
                
                for (const characteristic of characteristics) {
                    console.log(`📡 Characteristic: ${characteristic.uuid}, Properties:`, characteristic.properties);
                    
                    // Check if this characteristic supports writing
                    if (characteristic.properties.write || characteristic.properties.writeWithoutResponse) {
                        // Prefer thermal printer services, but accept any writable characteristic as fallback
                        if (isThermalService || !printCharacteristic) {
                            printService = service;
                            printCharacteristic = characteristic;
                            console.log(`📡 Found writable characteristic: ${characteristic.uuid} (thermal: ${isThermalService})`);
                            
                            // If we found a thermal printer service, use it immediately
                            if (isThermalService) {
                                break;
                            }
                        }
                    }
                }
                if (printCharacteristic && isThermalService) break;
            }

            if (!printCharacteristic) {
                console.error('❌ No writable characteristic found');
                console.log('Available services and characteristics:');
                for (const service of services) {
                    console.log(`Service: ${service.uuid}`);
                    const characteristics = await service.getCharacteristics();
                    for (const characteristic of characteristics) {
                        console.log(`  Characteristic: ${characteristic.uuid}, Properties:`, characteristic.properties);
                    }
                }
                
                // Try alternative printing methods
                console.log('🔄 Trying alternative printing methods...');
                return await this.tryAlternativePrinting(printer, content);
            }

            // Try to send data with different approaches
            let sendSuccess = false;
            const approaches = [
                { name: 'writeWithoutResponse', method: () => printCharacteristic.writeValueWithoutResponse(data) },
                { name: 'writeValue', method: () => printCharacteristic.writeValue(data) },
                { name: 'chunked_writeWithoutResponse', method: () => this.sendDataInChunks(printCharacteristic, data, true) },
                { name: 'chunked_writeValue', method: () => this.sendDataInChunks(printCharacteristic, data, false) },
                { name: 'escpos_commands', method: () => this.sendEscPosCommands(printCharacteristic, content) }
            ];
            
            for (const approach of approaches) {
                try {
                    console.log(`📤 Trying approach: ${approach.name}`);
                    await approach.method();
                    console.log(`✅ Successfully sent data using ${approach.name}`);
                    sendSuccess = true;
                    break;
                } catch (error) {
                    console.warn(`❌ Approach ${approach.name} failed:`, error.message);
                    // Continue to next approach
                }
            }
            
            if (!sendSuccess) {
                throw new Error('All sending approaches failed');
            }

            console.log(`✅ Successfully sent ${data.length} bytes to Bluetooth device`);

        } catch (error) {
            console.error('❌ Error sending data to Bluetooth device:', error);
            throw error;
        }
    }

    // Send data in chunks
    async sendDataInChunks(characteristic, data, useWriteWithoutResponse = true) {
        const chunkSize = 20; // BLE characteristic max size
        console.log(`📤 Sending data in chunks of ${chunkSize} bytes (writeWithoutResponse: ${useWriteWithoutResponse})...`);
        
        for (let i = 0; i < data.length; i += chunkSize) {
            const chunk = data.slice(i, i + chunkSize);
            console.log(`📤 Sending chunk ${Math.floor(i/chunkSize) + 1}: ${chunk.length} bytes`);
            
            if (useWriteWithoutResponse) {
                await characteristic.writeValueWithoutResponse(chunk);
                console.log(`✅ Chunk sent without response`);
            } else {
                await characteristic.writeValue(chunk);
                console.log(`✅ Chunk sent with response`);
            }
            
            // Small delay between chunks
            await new Promise(resolve => setTimeout(resolve, 50));
        }
    }

    // Send ESC/POS commands for thermal printers
    async sendEscPosCommands(characteristic, content) {
        console.log(`📤 Sending ESC/POS commands...`);
        
        // ESC/POS initialization sequence
        const initCommands = new Uint8Array([
            0x1B, 0x40,  // ESC @ - Initialize printer
            0x1B, 0x61, 0x01,  // ESC a 1 - Center alignment
            0x1B, 0x21, 0x00   // ESC ! 0 - Normal text
        ]);
        
        // Convert content to bytes
        const contentBytes = new TextEncoder().encode(content);
        
        // Combine initialization and content
        const fullData = new Uint8Array(initCommands.length + contentBytes.length);
        fullData.set(initCommands, 0);
        fullData.set(contentBytes, initCommands.length);
        
        // Add cut command at the end
        const cutCommand = new Uint8Array([0x1D, 0x56, 0x00]); // GS V 0 - Full cut
        const finalData = new Uint8Array(fullData.length + cutCommand.length);
        finalData.set(fullData, 0);
        finalData.set(cutCommand, fullData.length);
        
        console.log(`📤 Sending ${finalData.length} bytes of ESC/POS data...`);
        
        // Try different sending methods
        try {
            await characteristic.writeValueWithoutResponse(finalData);
            console.log(`✅ ESC/POS data sent without response`);
        } catch (error) {
            console.warn(`❌ writeWithoutResponse failed, trying writeValue:`, error.message);
            await characteristic.writeValue(finalData);
            console.log(`✅ ESC/POS data sent with response`);
        }
    }

    // Try alternative printing methods when no writable characteristic is found
    async tryAlternativePrinting(printer, content) {
        console.log('🔄 Attempting alternative printing methods...');
        
        const methods = [
            { name: 'Web Serial API', method: () => this.tryWebSerialPrinting(content) },
            { name: 'Bluetooth RFCOMM', method: () => this.tryBluetoothRFCOMM(printer, content) },
            { name: 'Backend API Fallback', method: () => this.tryBackendAPIFallback(printer, content) },
            { name: 'Notification Method', method: () => this.tryNotificationMethod(printer, content) }
        ];
        
        for (const method of methods) {
            try {
                console.log(`🔄 Trying ${method.name}...`);
                const result = await method.method();
                console.log(`✅ ${method.name} succeeded:`, result);
                return result;
            } catch (error) {
                console.warn(`❌ ${method.name} failed:`, error.message);
            }
        }
        
        throw new Error('All alternative printing methods failed');
    }

    // Try Web Serial API for printing
    async tryWebSerialPrinting(content) {
        if (!navigator.serial) {
            throw new Error('Web Serial API not supported');
        }
        
        console.log('📡 Trying Web Serial API...');
        
        // Request port access
        const port = await navigator.serial.requestPort();
        await port.open({ baudRate: 9600 });
        
        const writer = port.writable.getWriter();
        const data = new TextEncoder().encode(content);
        await writer.write(data);
        writer.releaseLock();
        await port.close();
        
        return { success: true, method: 'Web Serial API' };
    }

    // Try Bluetooth RFCOMM (if available)
    async tryBluetoothRFCOMM(printer, content) {
        console.log('📡 Trying Bluetooth RFCOMM...');
        
        // This is a placeholder for RFCOMM implementation
        // Most browsers don't support RFCOMM directly
        throw new Error('Bluetooth RFCOMM not supported in this browser');
    }

    // Try backend API fallback
    async tryBackendAPIFallback(printer, content) {
        console.log('📡 Trying backend API fallback...');
        
        const response = await fetch('/api/bluetooth/print', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                deviceId: printer.deviceId,
                content: content,
                printerName: printer.name
            })
        });

        const result = await response.json();
        
        if (result.success) {
            return { success: true, method: 'Backend API', result: result };
        } else {
            throw new Error(result.error || 'Backend API failed');
        }
    }

    // Try notification method (show content to user)
    async tryNotificationMethod(printer, content) {
        console.log('📡 Trying notification method...');
        
        // Show the receipt content to the user
        const receiptWindow = window.open('', '_blank', 'width=400,height=600');
        receiptWindow.document.write(`
            <html>
                <head>
                    <title>Receipt - ${printer.name}</title>
                    <style>
                        body { font-family: monospace; padding: 20px; }
                        .receipt { white-space: pre-wrap; }
                        .print-btn { 
                            background: #007bff; 
                            color: white; 
                            border: none; 
                            padding: 10px 20px; 
                            margin: 10px 0; 
                            cursor: pointer; 
                        }
                    </style>
                </head>
                <body>
                    <h3>Receipt from ${printer.name}</h3>
                    <div class="receipt">${content}</div>
                    <button class="print-btn" onclick="window.print()">Print Receipt</button>
                    <button class="print-btn" onclick="window.close()">Close</button>
                </body>
            </html>
        `);
        receiptWindow.document.close();
        
        return { success: true, method: 'Notification Window', window: receiptWindow };
    }

    // Disconnect from a printer
    async disconnectPrinter(printerId) {
        try {
            const printer = this.connectedPrinters.get(printerId);
            if (!printer) {
                throw new Error('Printer not found');
            }

            console.log(`🔌 Disconnecting from ${printer.name}`);

            // Send disconnect request to backend
            const response = await fetch('/api/bluetooth/disconnect', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    deviceId: printer.deviceId
                })
            });

            const result = await response.json();
            
            if (result.success) {
                // Disconnect from GATT server
                if (printer.server && printer.server.connected) {
                    printer.server.disconnect();
                }
                
                // Remove from connected printers
                this.connectedPrinters.delete(printerId);
                this.savePersistedConnections();
                
                console.log(`✅ Disconnected from ${printer.name}`);
                return result;
            } else {
                throw new Error(result.error || 'Disconnect failed');
            }

        } catch (error) {
            console.error('❌ Bluetooth disconnect error:', error);
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
            if (printer.status === 'connected' && printer.server) {
                try {
                    if (!printer.server.connected) {
                        this.handleDisconnection(printerId);
                    }
                } catch (error) {
                    console.error(`Connection check failed for ${printerId}:`, error);
                    this.handleDisconnection(printerId);
                }
            }
        }
    }

    // Set up page visibility handling
    setupPageVisibilityHandling() {
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                console.log('🔵 Page visible - checking Bluetooth connections');
                this.checkConnectionStatus();
            }
        });
    }

    // Clear all connections
    clearAllConnections() {
        this.connectedPrinters.clear();
        this.savePersistedConnections();
        this.connectionStatus = 'disconnected';
        console.log('🔵 Cleared all Bluetooth connections');
    }
}

// Initialize the separate Bluetooth manager
window.separateBluetoothManager = new SeparateBluetoothManager();

