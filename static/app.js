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

const editButtons = document.querySelectorAll('.edit-user');
const cancelButtons = document.querySelectorAll('.cancel-user');
const deleteButtons = document.querySelectorAll('.delete-user');

function toggleUserRow(userId, editable) {
  const row = document.querySelector(`.edit-user[data-user-id="${userId}"]`)?.closest('tr');
  if (!row) {
    return;
  }
  const fields = row.querySelectorAll('.user-field');
  fields.forEach((field) => {
    field.disabled = !editable;
  });
  row.classList.toggle('is-editing', editable);
  row.querySelector(`.edit-user[data-user-id="${userId}"]`)?.toggleAttribute('hidden', editable);
  row.querySelector(`.save-user[data-user-id="${userId}"]`)?.toggleAttribute('hidden', !editable);
  row.querySelector(`.cancel-user[data-user-id="${userId}"]`)?.toggleAttribute('hidden', !editable);
}

editButtons.forEach((button) => {
  button.addEventListener('click', () => {
    toggleUserRow(button.dataset.userId, true);
  });
});

cancelButtons.forEach((button) => {
  button.addEventListener('click', () => {
    const row = button.closest('tr');
    if (row) {
      row.querySelectorAll('.user-field').forEach((field) => {
        if (field.dataset.original !== undefined) {
          field.value = field.dataset.original;
        }
      });
    }
    toggleUserRow(button.dataset.userId, false);
  });
});

deleteButtons.forEach((button) => {
  button.addEventListener('click', (event) => {
    const confirmed = window.confirm('Delete this user? This cannot be undone.');
    if (!confirmed) {
      event.preventDefault();
    }
  });
});
