/**
 * Unified Printer Manager
 * Manages both Bluetooth and WiFi printers with a unified interface
 */

class UnifiedPrinterManager {
    constructor(bluetoothManager, wifiManager) {
        this.bluetoothManager = bluetoothManager;
        this.wifiManager = wifiManager;
        
        // Set up event listeners
        this.setupEventListeners();
    }

    // Set up event listeners for both managers
    setupEventListeners() {
        // Listen for Bluetooth printer events
        document.addEventListener('bluetoothPrinterStatus', (event) => {
            this.notifyUnifiedStatus('bluetooth', event.detail);
        });

        // Listen for WiFi printer events
        document.addEventListener('wifiPrinterStatus', (event) => {
            this.notifyUnifiedStatus('wifi', event.detail);
        });
    }

    // Notify unified status changes
    notifyUnifiedStatus(type, detail) {
        const event = new CustomEvent('unifiedPrinterStatus', {
            detail: { type, printerType: type, ...detail }
        });
        document.dispatchEvent(event);
    }

    // Get all connected printers
    getAllConnectedPrinters() {
        const bluetoothPrinters = this.bluetoothManager ? this.bluetoothManager.getConnectedPrinters() : [];
        const wifiPrinters = this.wifiManager ? this.wifiManager.getConnectedPrinters() : [];
        
        return {
            bluetooth: bluetoothPrinters,
            wifi: wifiPrinters,
            all: [...bluetoothPrinters, ...wifiPrinters]
        };
    }

    // Check if any printers are connected
    isConnected() {
        const printers = this.getAllConnectedPrinters();
        return printers.all.length > 0;
    }

    // Print to all connected printers
    async printToAllPrinters(content) {
        const results = [];
        
        // Print to Bluetooth printers
        if (this.bluetoothManager && this.bluetoothManager.isConnected()) {
            try {
                const bluetoothResults = await this.bluetoothManager.printToAllPrinters(content);
                results.push(...bluetoothResults);
            } catch (error) {
                console.error('Bluetooth print error:', error);
                results.push({ printerId: 'bluetooth', success: false, error: error.message });
            }
        }
        
        // Print to WiFi printers
        if (this.wifiManager && this.wifiManager.isConnected()) {
            try {
                const wifiResults = await this.wifiManager.printToAllPrinters(content);
                results.push(...wifiResults);
            } catch (error) {
                console.error('WiFi print error:', error);
                results.push({ printerId: 'wifi', success: false, error: error.message });
            }
        }
        
        return results;
    }

    // Get unified metrics
    getMetrics() {
        const bluetoothMetrics = this.bluetoothManager ? this.bluetoothManager.getMetrics() : {};
        const wifiMetrics = this.wifiManager ? this.wifiManager.getMetrics() : {};
        
        return {
            bluetooth: bluetoothMetrics,
            wifi: wifiMetrics,
            total: {
                avgConnectionTime: (bluetoothMetrics.avgConnectionTime || 0 + wifiMetrics.avgConnectionTime || 0) / 2,
                avgPrintTime: (bluetoothMetrics.avgPrintTime || 0 + wifiMetrics.avgPrintTime || 0) / 2,
                totalConnections: (bluetoothMetrics.totalConnections || 0) + (wifiMetrics.totalConnections || 0),
                totalPrints: (bluetoothMetrics.totalPrints || 0) + (wifiMetrics.totalPrints || 0),
                errorCount: (bluetoothMetrics.errorCount || 0) + (wifiMetrics.errorCount || 0)
            }
        };
    }

    // Disconnect all printers
    async disconnectAllPrinters() {
        if (this.bluetoothManager) {
            this.bluetoothManager.disconnectAllPrinters();
        }
        if (this.wifiManager) {
            this.wifiManager.disconnectAllPrinters();
        }
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = UnifiedPrinterManager;
} else {
    window.UnifiedPrinterManager = UnifiedPrinterManager;
}

