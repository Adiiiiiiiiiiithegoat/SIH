lucide.createIcons();

const modalBackdrop = document.getElementById('modalBackdrop');
const uploadBtn = document.getElementById('uploadBtn');
const modalClose = document.getElementById('modalClose');
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const analyzeBtn = document.getElementById('analyzeBtn');
const uploadSuccess = document.getElementById('uploadSuccess');
const incidentTitle = document.getElementById('incidentTitle');
const queueItems = [...document.querySelectorAll('.queue-item')];
const mapPins = [...document.querySelectorAll('.map-pin')];
const reviewBtn = document.getElementById('reviewBtn');

const incidents = {
  bridge: 'Kali River bridge',
  road: 'NH-47, east approach',
  building: 'Shanti Nagar school',
  culvert: 'Ward 12 culvert'
};

function openModal() {
  modalBackdrop.classList.add('open');
  modalBackdrop.setAttribute('aria-hidden', 'false');
  uploadSuccess.classList.remove('show');
}
function closeModal() {
  modalBackdrop.classList.remove('open');
  modalBackdrop.setAttribute('aria-hidden', 'true');
}
uploadBtn.addEventListener('click', openModal);
modalClose.addEventListener('click', closeModal);
modalBackdrop.addEventListener('click', (event) => {
  if (event.target === modalBackdrop) closeModal();
});
dropzone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  if (fileInput.files.length) {
    dropzone.querySelector('strong').textContent = fileInput.files[0].name;
    dropzone.querySelector('span').textContent = 'Evidence attached · ready for analysis';
  }
});
analyzeBtn.addEventListener('click', () => {
  uploadSuccess.classList.add('show');
  analyzeBtn.innerHTML = '<i data-lucide="check"></i> Queued';
  analyzeBtn.disabled = true;
  lucide.createIcons();
  setTimeout(() => {
    closeModal();
    analyzeBtn.innerHTML = 'Analyze report <i data-lucide="arrow-right"></i>';
    analyzeBtn.disabled = false;
    lucide.createIcons();
  }, 1500);
});

function selectIncident(key) {
  incidentTitle.textContent = incidents[key] || incidents.bridge;
  queueItems.forEach((item) => item.classList.toggle('selected', item.dataset.incident === key));
  mapPins.forEach((pin) => pin.style.transform = pin.dataset.incident === key ? 'translate(-50%, -50%) scale(1.15)' : 'translate(-50%, -50%) scale(1)');
}
document.querySelectorAll('[data-incident]').forEach((el) => {
  el.addEventListener('click', () => selectIncident(el.dataset.incident));
});
reviewBtn.addEventListener('click', () => {
  reviewBtn.innerHTML = '<i data-lucide="check"></i> Review started';
  reviewBtn.style.background = '#5a8d6b';
  lucide.createIcons();
  setTimeout(() => {
    reviewBtn.innerHTML = '<i data-lucide="clipboard-check"></i> Review & assign';
    reviewBtn.style.background = '';
    lucide.createIcons();
  }, 1800);
});
