// form_items.js - Location-aware inventory search
class InvoiceFormManager {
    constructor() {
        this.inventoryData = [];         // Will hold products for the selected location
        this.usedProductIds = new Set();
        this.currencySymbol = window.userCurrencySymbol || 'Rs.';
        this.currentLocationId = window.defaultLocationId || null;
        this.initialize();
    }

    initialize() {
        console.log('Initializing location-aware invoice form...');
        this.setupCoreEventListeners();
        this.updateEmptyState();
        this.updateGrandTotal();

        // Load initial location products if a default location exists
        if (this.currentLocationId) {
            this.loadProductsForLocation(this.currentLocationId);
            const locationSelect = document.getElementById('invoiceLocationId');
            if (locationSelect) locationSelect.value = this.currentLocationId;
        }

        // Listen to location change
        const locationSelect = document.getElementById('invoiceLocationId');
        if (locationSelect) {
            locationSelect.addEventListener('change', (e) => {
                this.currentLocationId = e.target.value;
                if (this.currentLocationId) {
                    this.loadProductsForLocation(this.currentLocationId);
                } else {
                    this.inventoryData = [];
                    this.updateInventoryDropdown();
                    document.getElementById('locationStockInfo').innerHTML = 'Select a location to see available stock.';
                }
            });
        }
    }

    loadProductsForLocation(locationId) {
        fetch(`/api/v1/locations/${locationId}/products?per_page=500`, { credentials: 'same-origin' })
            .then(res => res.json())
            .then(data => {
                const products = data.products || [];  // <-- key change
                this.inventoryData = products.filter(p => p.stock_at_location > 0).map(p => ({
                    id: p.id,
                    name: p.name,
                    sku: p.sku,
                    price: p.selling_price,
                    stock: p.stock_at_location,
                    unit_type: p.unit_type || 'piece'
                }));
                console.log(`Loaded ${this.inventoryData.length} products for location ${locationId}`);
                this.updateInventoryDropdown();
                document.getElementById('locationStockInfo').innerHTML = 
                    `<i class="bi bi-check-circle-fill text-success"></i> Stock data loaded for ${this.inventoryData.length} products.`;
                const searchInput = document.getElementById('modalSearch') || document.getElementById('inventorySearch');
                if (searchInput && searchInput.value.trim()) {
                    this.searchInventory(searchInput.value);
                }
            })
            .catch(err => {
                console.error('Failed to load location products:', err);
                document.getElementById('locationStockInfo').innerHTML = 
                    `<i class="bi bi-exclamation-triangle-fill text-danger"></i> Failed to load stock data.`;
            });
    }

    setupCoreEventListeners() {
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('removeItemBtn')) {
                this.removeItem(e.target);
            }
            if (e.target.id === 'addInventoryBtn') {
                this.addInventoryItemFromDropdown();
            }
            if (e.target.id === 'showAllInventory') {
                if (this.currentLocationId) {
                    this.showAllInventory();
                } else {
                    this.showToast('Please select a location first', 'warning');
                }
            }
            if (e.target.classList.contains('add-inventory-search-item')) {
                const productId = e.target.dataset.id;
                const product = this.inventoryData.find(p => p.id == productId);
                if (product) {
                    this.addInventoryItem(
                        product.id,
                        product.name,
                        product.price,
                        product.stock,
                        product.sku,
                        product.unit_type
                    );
                }
                const modal = bootstrap.Modal.getInstance(document.getElementById('addItemModal'));
                if (modal) modal.hide();
            }
        });

        document.addEventListener('input', (e) => {
            if (e.target.id === 'modalSearch' || e.target.id === 'inventorySearch') {
                this.searchInventory(e.target.value);
            }
            if (e.target.name === 'item_qty[]' || e.target.name === 'item_price[]') {
                this.validateQuantityInRealTime(e.target);
                this.updateLineTotal(e.target.closest('.item-row'));
                this.updateGrandTotal();
            }
        });

        const modalEl = document.getElementById('addItemModal');
        if (modalEl) {
            modalEl.addEventListener('shown.bs.modal', () => {
                const searchInput = document.getElementById('modalSearch');
                if (searchInput) {
                    searchInput.value = '';
                    searchInput.focus();
                    const results = document.getElementById('inventoryResults');
                    if (results) results.style.display = 'none';
                }
            });
        }
    }

    updateInventoryDropdown() {
        const dropdown = document.getElementById('inventoryDropdown');
        if (!dropdown) return;

        dropdown.innerHTML = '<option value="">Select product...</option>';
        this.inventoryData.forEach(item => {
            if (this.usedProductIds.has(item.id)) return;
            const option = document.createElement('option');
            option.value = item.id;
            let label = item.name;
            if (item.sku) label = `${item.sku} - ${label}`;
            label += ` - ${this.currencySymbol}${Number(item.price).toFixed(2)} (Stock: ${item.stock})`;
            option.textContent = label;
            option.dataset.name = item.name;
            option.dataset.price = item.price;
            option.dataset.stock = item.stock;
            option.dataset.sku = item.sku || '';
            option.dataset.unitType = item.unit_type || 'piece';
            dropdown.appendChild(option);
        });
    }

    searchInventory(searchTerm) {
        const resultsDiv = document.getElementById('inventoryResults');
        if (!resultsDiv) return;

        if (!searchTerm.trim() || !this.currentLocationId) {
            resultsDiv.style.display = 'none';
            if (!this.currentLocationId && searchTerm.trim()) {
                resultsDiv.innerHTML = '<div class="alert alert-warning">Please select a location first</div>';
                resultsDiv.style.display = 'block';
            }
            return;
        }

        const filteredItems = this.inventoryData.filter(item =>
            (item.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
             (item.sku && item.sku.toLowerCase().includes(searchTerm.toLowerCase()))) &&
            !this.usedProductIds.has(item.id)
        );

        this.displaySearchResults(filteredItems);
    }

    showAllInventory() {
        if (!this.currentLocationId) {
            this.showToast('Please select a location first', 'warning');
            return;
        }
        const availableItems = this.inventoryData.filter(item => !this.usedProductIds.has(item.id));
        this.displaySearchResults(availableItems);
    }

    displaySearchResults(items) {
        const resultsDiv = document.getElementById('inventoryResults');
        if (!resultsDiv) return;

        if (items.length === 0) {
            resultsDiv.innerHTML = '<div class="alert alert-warning">No matching products found at this location</div>';
            resultsDiv.style.display = 'block';
            return;
        }

        const resultsHTML = `
            <div class="alert alert-info mb-3">
                <strong>Found ${items.length} product(s) at this location:</strong>
            </div>
            <div class="row g-3">
                ${items.map(item => `
                    <div class="col-12 col-md-6 col-lg-4">
                        <div class="card h-100 shadow-sm">
                            <div class="card-body text-center">
                                <h6 class="card-title mb-2">
                                    ${this.escapeHtml(item.name)}
                                    ${item.sku ? `<br><small class="text-muted">SKU: ${this.escapeHtml(item.sku)}</small>` : ''}
                                </h6>
                                <p class="mb-2"><strong>${this.currencySymbol}${Number(item.price).toFixed(2)}</strong></p>
                                <p class="mb-3">
                                    <span class="badge ${item.stock > 10 ? 'bg-success' : item.stock > 0 ? 'bg-warning' : 'bg-danger'}">
                                        Stock: ${item.stock}
                                    </span>
                                    <span class="badge bg-secondary ms-1">
                                        ${this.getUnitLabel(item.unit_type)}
                                    </span>
                                </p>
                                <button type="button" class="btn btn-success w-100 add-inventory-search-item"
                                        data-id="${item.id}"
                                        data-name="${this.escapeHtml(item.name)}"
                                        data-price="${item.price}"
                                        data-stock="${item.stock}"
                                        data-sku="${this.escapeHtml(item.sku || '')}"
                                        data-unit-type="${item.unit_type || 'piece'}">
                                    Add to Invoice
                                </button>
                            </div>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;

        resultsDiv.innerHTML = resultsHTML;
        resultsDiv.style.display = 'block';
    }

    addInventoryItem(productId, productName, productPrice, productStock, productSku = '', unitType = 'piece') {
        if (this.usedProductIds.has(productId)) {
            this.showToast('This item is already in the invoice', 'warning');
            return;
        }

        const itemsContainer = document.getElementById('itemsContainer');
        if (!itemsContainer) return;

        this.usedProductIds.add(productId);

        const qtyAttributes = this.getQuantityAttributes(unitType);
        const initialQty = (unitType === 'piece') ? '1' : '1.00';

        const newRow = document.createElement('div');
        newRow.className = 'row g-3 align-items-end mb-3 pb-3 border-bottom item-row';
        newRow.innerHTML = `
            <div class="col-md-5">
                <label class="form-label small fw-semibold">Item</label>
                <input type="text" name="item_name[]" class="form-control" value="${this.escapeHtml(productName)}" readonly>
                ${productSku ? `<small class="text-muted d-block">SKU: ${this.escapeHtml(productSku)}</small>` : ''}
                <small class="text-muted d-block">
                    Unit: ${this.getUnitLabel(unitType)} • Stock: ${productStock} ${this.getUnitLabel(unitType)}
                </small>
                <input type="hidden" name="item_id[]" value="${productId}">
                <input type="hidden" name="item_unit_type[]" value="${unitType}">
            </div>
            <div class="col-md-2">
                <label class="form-label small fw-semibold">Qty</label>
                <div class="input-group">
                    <input type="number" name="item_qty[]" class="form-control" value="${initialQty}" 
                           ${qtyAttributes} required>
                    <span class="input-group-text">${this.getUnitLabel(unitType)}</span>
                </div>
            </div>
            <div class="col-md-3">
                <label class="form-label small fw-semibold">Unit Price</label>
                <input type="number" name="item_price[]" class="form-control" value="${Number(productPrice)}" step="0.01" readonly>
            </div>
            <div class="col-md-1">
                <label class="form-label small opacity-0">Remove</label>
                <button type="button" class="btn btn-outline-danger removeItemBtn w-100">×</button>
            </div>
            <div class="col-md-1 text-end">
                <div class="fw-bold text-success fs-6 line-total">${this.currencySymbol}${Number(productPrice).toFixed(2)}</div>
            </div>
        `;

        itemsContainer.appendChild(newRow);

        this.showToast(`${productName} added!`);
        this.updateEmptyState();
        this.updateInventoryDropdown();
        this.updateGrandTotal();

        const resultsDiv = document.getElementById('inventoryResults');
        if (resultsDiv) resultsDiv.style.display = 'none';
        const searchInput = document.getElementById('modalSearch') || document.getElementById('inventorySearch');
        if (searchInput) searchInput.value = '';
    }

    getQuantityAttributes(unitType) {
        if (unitType === 'piece') {
            return 'min="1" step="1"';
        } else {
            return 'min="0.01" step="0.01"';
        }
    }

    getUnitLabel(unitType) {
        const map = {
            'piece': 'pcs',
            'weight': 'kg',
            'volume': 'L',
            'length': 'm'
        };
        return map[unitType] || unitType;
    }

    validateQuantityInRealTime(input) {
        const row = input.closest('.item-row');
        const qty = parseFloat(input.value) || 0;
        const productIdInput = row.querySelector('input[name="item_id[]"]');
        if (!productIdInput) return;

        const product = this.inventoryData.find(item => item.id == productIdInput.value);
        if (!product) return;

        if (qty > product.stock) {
            input.classList.add('is-invalid');
            this.showToast(`Only ${product.stock} available at this location`, 'warning');
        } else {
            input.classList.remove('is-invalid');
        }
    }

    updateLineTotal(row) {
        const qty = parseFloat(row.querySelector('input[name="item_qty[]"]').value) || 0;
        const price = Number(row.querySelector('input[name="item_price[]"]').value) || 0;
        const lineTotalEl = row.querySelector('.line-total');
        if (lineTotalEl) {
            lineTotalEl.textContent = `${this.currencySymbol}${(qty * price).toFixed(2)}`;
        }
    }

    updateGrandTotal() {
        let total = 0;
        document.querySelectorAll('.item-row').forEach(row => {
            const qty = parseFloat(row.querySelector('input[name="item_qty[]"]').value) || 0;
            const price = Number(row.querySelector('input[name="item_price[]"]').value) || 0;
            total += qty * price;
        });
        const grandTotalEl = document.getElementById('grandTotal');
        if (grandTotalEl) {
            grandTotalEl.textContent = total.toFixed(2);
        }
    }

    updateEmptyState() {
        const itemsContainer = document.getElementById('itemsContainer');
        const noItemsMessage = document.getElementById('noItemsMessage');
        if (!itemsContainer || !noItemsMessage) return;

        const hasItems = itemsContainer.querySelectorAll('.item-row').length > 0;
        noItemsMessage.style.display = hasItems ? 'none' : 'block';
    }

    removeItem(button) {
        const row = button.closest('.item-row');
        if (!row) return;

        const productIdInput = row.querySelector('input[name="item_id[]"]');
        if (productIdInput) {
            this.usedProductIds.delete(productIdInput.value);
        }

        row.remove();
        this.showToast('Item removed', 'error');
        this.updateEmptyState();
        this.updateInventoryDropdown();
        this.updateGrandTotal();
    }

    addInventoryItemFromDropdown() {
        const dropdown = document.getElementById('inventoryDropdown');
        if (!dropdown || !dropdown.value) return;
        const opt = dropdown.options[dropdown.selectedIndex];
        this.addInventoryItem(
            opt.value,
            opt.dataset.name,
            opt.dataset.price,
            opt.dataset.stock,
            opt.dataset.sku,
            opt.dataset.unitType
        );
        dropdown.selectedIndex = 0;
    }

    showToast(message, type = 'success') {
        if (typeof window.showToast === 'function') {
            const bgColor = type === 'error' ? '#dc3545' : type === 'warning' ? '#ffc107' : '#28a745';
            window.showToast(message, bgColor);
        } else {
            alert(message);
        }
    }

    escapeHtml(unsafe) {
        if (!unsafe) return '';
        return unsafe
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new InvoiceFormManager();
});