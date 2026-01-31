const dialog = document.getElementById('confirmDialog');
const statusToast = document.getElementById('statusToast');
let pendingLink = null;

function showStatus(message) {
  statusToast.textContent = message;
  statusToast.classList.add('show');
  setTimeout(() => statusToast.classList.remove('show'), 2500);
}

function handleExportClick(event) {
  const link = event.currentTarget;
  if (!dialog) {
    return;
  }
  event.preventDefault();
  pendingLink = link;
  dialog.showModal();
}

if (dialog) {
  const cancelButton = document.getElementById('confirmCancel');
  const confirmButton = document.getElementById('confirmOk');

  cancelButton?.addEventListener('click', () => {
    dialog.close();
    pendingLink = null;
  });

  confirmButton?.addEventListener('click', () => {
    dialog.close();
    if (pendingLink) {
      showStatus('Preparing your export...');
      window.location.href = pendingLink.href;
      pendingLink = null;
    }
  });
}

const exportLinks = document.querySelectorAll('.export-link');
exportLinks.forEach((link) => {
  link.addEventListener('click', handleExportClick);
});
