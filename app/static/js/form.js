document.addEventListener("DOMContentLoaded", function() {
  const container = document.getElementById("itemsContainer");
  const addBtn = document.getElementById("addItemBtn");

  function addRow() {
    const row = document.createElement("div");
    row.className = "row g-2 align-items-end mb-2";
    row.innerHTML = `
      <div class="col-md-6"><input type="text" name="item_name[]" class="form-control" placeholder="Item name" required></div>
      <div class="col-md-3"><input type="number" name="item_qty[]" class="form-control" placeholder="Qty" required min="1"></div>
      <div class="col-md-3"><input type="number" name="item_price[]" class="form-control" placeholder="Price" required min="0"></div>
    `;
    container.appendChild(row);
  }

  addBtn.addEventListener("click", addRow);
  // Add first row by default
  addRow();
});
