// form_items.js - Fixed: No Jinja in JS, uses window.userCurrencySymbol

class InvoiceFormManager {
    constructor() {
        this.inventoryData = [];
        this.usedProductIds = new Set();
        this.currencySymbol = window.userCurrencySymbol || 'Rs.';
        this.initialize();
    }

    initialize() {
        console.log('Initializing invoice form...');
        this.loadInventoryData();
        this.setupCoreEventListeners();
        this.updateEmptyState();
        this.updateGrandTotal();
    }

    loadInventoryData() {
        fetch('/api/inventory_items')
            .then(response => response.json())
            .then(items => {
                console.log(`Loaded ${items.length} inventory items`);
                this.inventoryData = items;
                this.updateInventoryDropdown();
            })
            .catch(error => {
                console.error('Failed to load inventory:', error);
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
                this.showAllInventory();
            }
            if (e.target.classList.contains('add-inventory-search-item')) {
                const productId = e.target.dataset.id;
                const productName = e.target.dataset.name;
                const productPrice = e.target.dataset.price;
                const productStock = e.target.dataset.stock;
                this.addInventoryItem(productId, productName, productPrice, productStock);
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
            if (this.usedProductIds.has(item.id.toString())) return;
            const option = document.createElement('option');
            option.value = item.id;
            option.textContent = `${item.name} - ${this.currencySymbol}${item.price} (Stock: ${item.stock})`;
            option.dataset.name = item.name;
            option.dataset.price = item.price;
            option.dataset.stock = item.stock;
            dropdown.appendChild(option);
        });
    }

    searchInventory(searchTerm) {
        const resultsDiv = document.getElementById('inventoryResults');
        if (!resultsDiv) return;

        if (!searchTerm.trim()) {
            resultsDiv.style.display = 'none';
            return;
        }

        const filteredItems = this.inventoryData.filter(item =>
            item.name.toLowerCase().includes(searchTerm.toLowerCase()) &&
            !this.usedProductIds.has(item.id.toString())
        );

        this.displaySearchResults(filteredItems);
    }

    showAllInventory() {
        const availableItems = this.inventoryData.filter(item => !this.usedProductIds.has(item.id.toString()));
        this.displaySearchResults(availableItems);
    }

    displaySearchResults(items) {
        const resultsDiv = document.getElementById('inventoryResults');
        if (!resultsDiv) return;

        if (items.length === 0) {
            resultsDiv.innerHTML = '<div class="alert alert-warning">No matching products found</div>';
            resultsDiv.style.display = 'block';
            return;
        }

        const resultsHTML = `
            <div class="alert alert-info mb-3">
                <strong>Found ${items.length} product(s):</strong>
            </div>
            <div class="row g-3">
                ${items.map(item => `
                    <div class="col-12 col-md-6 col-lg-4">
                        <div class="card h-100 shadow-sm">
                            <div class="card-body text-center">
                                <h6 class="card-title mb-3">${this.escapeHtml(item.name)}</h6>
                                <p class="mb-2"><strong>${this.currencySymbol}${item.price}</strong></p>
                                <p class="mb-3">
                                    <span class="badge ${item.stock > 10 ? 'bg-success' : item.stock > 0 ? 'bg-warning' : 'bg-danger'}">
                                        Stock: ${item.stock}
                                    </span>
                                </p>
                                <button type="button" class="btn btn-success w-100 add-inventory-search-item"
                                        data-id="${item.id}"
                                        data-name="${this.escapeHtml(item.name)}"
                                        data-price="${item.price}"
                                        data-stock="${item.stock}">
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

    addInventoryItemFromDropdown() {
        const dropdown = document.getElementById('inventoryDropdown');
        if (!dropdown || !dropdown.value) return;
        const opt = dropdown.options[dropdown.selectedIndex];
        this.addInventoryItem(opt.value, opt.dataset.name, opt.dataset.price, opt.dataset.stock);
        dropdown.selectedIndex = 0;
    }

    addInventoryItem(productId, productName, productPrice, productStock) {
        if (this.usedProductIds.has(productId)) {
            this.showToast('This item is already in the invoice', 'warning');
            return;
        }

        const itemsContainer = document.getElementById('itemsContainer');
        if (!itemsContainer) return;

        this.usedProductIds.add(productId);

        const newRow = document.createElement('div');
        newRow.className = 'row g-3 align-items-end mb-3 pb-3 border-bottom item-row';
        newRow.innerHTML = `
            <div class="col-md-5">
                <label class="form-label small fw-semibold">Item</label>
                <input type="text" name="item_name[]" class="form-control" value="${this.escapeHtml(productName)}" readonly>
                <small class="text-muted">Stock: ${productStock} units</small>
                <input type="hidden" name="item_id[]" value="${productId}">
            </div>
            <div class="col-md-2">
                <label class="form-label small fw-semibold">Qty</label>
                <input type="number" name="item_qty[]" class="form-control" value="1" min="1" max="${productStock}" required>
            </div>
            <div class="col-md-3">
                <label class="form-label small fw-semibold">Unit Price</label>
                <input type="number" name="item_price[]" class="form-control" value="${productPrice}" step="0.01" readonly>
            </div>
            <div class="col-md-1">
                <label class="form-label small opacity-0">Remove</label>
                <button type="button" class="btn btn-outline-danger removeItemBtn w-100">&times;</button>
            </div>
            <div class="col-md-1 text-end">
                <div class="fw-bold text-success fs-6 line-total">${this.currencySymbol}${productPrice}</div>
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

    validateQuantityInRealTime(input) {
        const row = input.closest('.item-row');
        const qty = parseInt(input.value) || 0;
        const productIdInput = row.querySelector('input[name="item_id[]"]');
        if (!productIdInput) return;

        const product = this.inventoryData.find(item => item.id.toString() === productIdInput.value);
        if (!product) return;

        if (qty > product.stock) {
            input.classList.add('is-invalid');
        } else {
            input.classList.remove('is-invalid');
        }

        const priceInput = row.querySelector('input[name="item_price[]"]');
        const lineTotalEl = row.querySelector('.line-total');
        if (priceInput && lineTotalEl) {
            const lineTotal = qty * parseFloat(priceInput.value || 0);
            lineTotalEl.textContent = `${this.currencySymbol}${lineTotal.toFixed(2)}`;
        }
    }

    updateGrandTotal() {
        let total = 0;
        document.querySelectorAll('.item-row').forEach(row => {
            const qty = parseInt(row.querySelector('input[name="item_qty[]"]').value) || 0;
            const price = parseFloat(row.querySelector('input[name="item_price[]"]').value) || 0;
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