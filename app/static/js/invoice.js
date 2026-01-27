(function(){
  const btn = document.getElementById('downloadBtn');
  if (!btn) return;
  const dataFromServer = window.invoiceData;
  const qrFromServer = window.qrData;

  btn.addEventListener('click', function(){
    const payload = Object.assign({}, dataFromServer);
    if (qrFromServer) payload.qr_b64 = qrFromServer;
    const errs = [];
    const items = payload.items || [];
    items.forEach(function(it, idx){
      const name = (it.name || it.code || '').trim();
      const qty = Number(it.qty ?? 0);
      const price = Number(it.price ?? 0);
      if (name){
        if (!qty || qty <= 0) errs.push(`Row {{ currency_symbol }}{idx+1}: Quantity required.`);
        if (!price || price <= 0) errs.push(`Row {{ currency_symbol }}{idx+1}: Price required.`);
      }
    });
    if (errs.length){
      alert("Cannot download PDF:\n\n" + errs.join("\n"));
      return;
    }
    document.getElementById('downloadDataField').value = JSON.stringify(payload);
    document.getElementById('downloadForm').submit();
  });
})();