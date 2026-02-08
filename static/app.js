const dialog = document.getElementById('confirmDialog');
const statusToast = document.getElementById('statusToast');
let pendingLink = null;

function showStatus(message) {
  if (!statusToast) {
    return;
  }
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

function initializeMatrixEffect() {
  const matrixRoot = document.querySelector('[data-matrix-demo]');
  const canvas = document.getElementById('matrixCanvas');
  const form = document.getElementById('matrixForm');
  const input = document.getElementById('matrixInput');
  if (!matrixRoot || !canvas || !form || !input) {
    return;
  }

  const ctx = canvas.getContext('2d');
  if (!ctx) {
    return;
  }

  const glyphs = ['0', '1'];
  const fontSize = 14;
  const freezeSpeed = 0.006;
  let columns = 0;
  let rows = 0;
  let drops = [];
  let freezeMap = [];

  function resizeCanvas() {
    const rect = matrixRoot.getBoundingClientRect();
    canvas.width = Math.max(Math.floor(rect.width), 240);
    canvas.height = Math.max(Math.floor(rect.height), 280);
    columns = Math.ceil(canvas.width / fontSize);
    rows = Math.ceil(canvas.height / fontSize);
    drops = Array.from({ length: columns }, () => Math.floor(Math.random() * rows));
    freezeMap = Array.from({ length: rows }, () => Array(columns).fill(0));
  }

  function drawTargetToMap(text) {
    const local = document.createElement('canvas');
    local.width = columns;
    local.height = rows;
    const localCtx = local.getContext('2d');
    if (!localCtx) {
      return;
    }

    localCtx.fillStyle = '#000';
    localCtx.fillRect(0, 0, columns, rows);
    localCtx.fillStyle = '#fff';

    const lineList = text
      .trim()
      .split('\n')
      .map((line) => line.trimEnd())
      .filter((line) => line.length > 0)
      .slice(0, 6);

    const lines = lineList.length > 0 ? lineList : ['TOLL BOT'];
    const maxLen = Math.max(...lines.map((line) => line.length), 1);
    const textSize = Math.max(6, Math.floor(Math.min(columns / (maxLen * 0.62), rows / (lines.length * 1.2))));
    localCtx.font = `${textSize}px monospace`;
    localCtx.textAlign = 'center';
    localCtx.textBaseline = 'middle';

    const centerX = columns / 2;
    const lineHeight = textSize * 1.2;
    const startY = rows / 2 - ((lines.length - 1) * lineHeight) / 2;

    lines.forEach((line, index) => {
      localCtx.fillText(line, centerX, startY + index * lineHeight);
    });

    const imageData = localCtx.getImageData(0, 0, columns, rows).data;
    const target = Array.from({ length: rows }, () => Array(columns).fill(0));

    for (let y = 0; y < rows; y += 1) {
      for (let x = 0; x < columns; x += 1) {
        const i = (y * columns + x) * 4;
        const alpha = imageData[i + 3];
        target[y][x] = alpha > 30 ? 1 : 0;
      }
    }

    freezeMap = target;
  }

  function diffuseFreeze() {
    for (let y = 0; y < rows; y += 1) {
      for (let x = 0; x < columns; x += 1) {
        if (freezeMap[y][x] === 1) {
          if (Math.random() < freezeSpeed) {
            freezeMap[y][x] = 2;
          }
        } else if (freezeMap[y][x] === 2 && Math.random() < 0.001) {
          freezeMap[y][x] = 1;
        }
      }
    }
  }

  function draw() {
    ctx.fillStyle = 'rgba(0, 10, 4, 0.18)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.font = `${fontSize}px monospace`;

    for (let column = 0; column < columns; column += 1) {
      const row = drops[column];
      const x = column * fontSize;
      const y = row * fontSize;
      const state = freezeMap[row]?.[column] ?? 0;

      if (state === 2) {
        ctx.fillStyle = '#e8ffec';
        ctx.fillText(glyphs[Math.random() > 0.5 ? 1 : 0], x, y);
      } else {
        ctx.fillStyle = '#63f28c';
        ctx.fillText(glyphs[Math.random() > 0.5 ? 1 : 0], x, y);
        drops[column] = row > rows || Math.random() > 0.975 ? 0 : row + 1;
      }
    }

    diffuseFreeze();
    window.requestAnimationFrame(draw);
  }

  resizeCanvas();
  drawTargetToMap('TOLL BOT');
  draw();

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    drawTargetToMap(input.value || 'TOLL BOT');
    showStatus('Signal captured. Diffusing glyph lock...');
  });

  window.addEventListener('resize', () => {
    resizeCanvas();
    drawTargetToMap(input.value || 'TOLL BOT');
  });
}

initializeMatrixEffect();
