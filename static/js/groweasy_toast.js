(function(){
  function showToast(message, bg) {
    bg = bg || "#28a745";
    const t = document.createElement("div");
    t.className = "groweasy-toast";
    t.innerHTML = `<div class="toast-body">${message}</div>`;
    document.body.appendChild(t);
    requestAnimationFrame(()=> t.classList.add("show"));
    setTimeout(()=>{ t.classList.remove("show"); setTimeout(()=>t.remove(),350); }, 3000);
  }
  window.showToast = showToast;
})();