// Photo upload / webcam capture and small helpers for the employee form.
(function () {
  const preview = document.getElementById('preview');
  const photoData = document.getElementById('photo_data');
  const photoFile = document.getElementById('photo_file');
  const btnStart = document.getElementById('camStart');
  const btnSnap = document.getElementById('camSnap');
  const btnStop = document.getElementById('camStop');
  const msg = document.getElementById('camMsg');
  let stream = null;

  function showImage(src) {
    preview.innerHTML = '';
    const img = document.createElement('img');
    img.src = src;
    preview.appendChild(img);
  }

  function stopCamera() {
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null;
    btnStart.hidden = false;
    btnSnap.hidden = true;
    btnStop.hidden = true;
  }

  photoFile.addEventListener('change', () => {
    const f = photoFile.files[0];
    if (!f) return;
    photoData.value = '';  // uploaded file wins over an earlier capture
    stopCamera();
    showImage(URL.createObjectURL(f));
    msg.textContent = '';
  });

  btnStart.addEventListener('click', async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      msg.textContent = 'Camera is not available here. Webcam capture needs the app to be opened as ' +
        'http://localhost:5000 on this computer (or over HTTPS). Use "Upload photo" instead.';
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
    } catch (err) {
      msg.textContent = 'Could not open the camera: ' + err.message;
      return;
    }
    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.srcObject = stream;
    preview.innerHTML = '';
    preview.appendChild(video);
    btnStart.hidden = true;
    btnSnap.hidden = false;
    btnStop.hidden = false;
    msg.textContent = 'Position the face in the frame and click "Capture photo".';
  });

  btnSnap.addEventListener('click', () => {
    const video = preview.querySelector('video');
    if (!video || !video.videoWidth) return;
    // Crop the centre of the frame to a passport-style 5:6 portrait.
    const h = video.videoHeight;
    const w = Math.min(video.videoWidth, Math.round(h * 5 / 6));
    const canvas = document.createElement('canvas');
    canvas.width = 400;
    canvas.height = 480;
    canvas.getContext('2d').drawImage(video, (video.videoWidth - w) / 2, 0, w, h, 0, 0, 400, 480);
    const data = canvas.toDataURL('image/jpeg', 0.9);
    photoData.value = data;
    photoFile.value = '';
    stopCamera();
    showImage(data);
    msg.textContent = 'Photo captured. It will be saved when you click "Save employee".';
  });

  btnStop.addEventListener('click', () => {
    stopCamera();
    preview.textContent = 'No photo';
    msg.textContent = '';
  });

  // Show police verification fields only when "Yes" is selected.
  const pvFields = document.getElementById('pvFields');
  const pvValid = document.getElementById('pv_valid_upto');
  function syncPv() {
    const yes = document.querySelector('input[name=pv_done]:checked')?.value === '1';
    pvFields.style.display = yes ? '' : 'none';
    pvValid.required = yes;
  }
  document.querySelectorAll('input[name=pv_done]').forEach(r => r.addEventListener('change', syncPv));
  syncPv();

  // Format Aadhaar as "1234 5678 9012" while typing.
  const aadhaar = document.getElementById('aadhaar');
  aadhaar.addEventListener('input', () => {
    const digits = aadhaar.value.replace(/\D/g, '').slice(0, 12);
    aadhaar.value = digits.replace(/(\d{4})(?=\d)/g, '$1 ');
  });
})();
