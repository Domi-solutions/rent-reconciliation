/* Photos are shrunk on the phone before they are sent: a 6 MB camera photo becomes about
   250 KB, so a message with photos goes in seconds on mobile data. The server still re-encodes
   each photo (which drops the GPS location) and makes the small copy shown in the chat.
   Works on any <input type="file" name="photos">; if a browser can't do it, the original goes. */
(() => {
  const MAX_SIDE = 1600, QUALITY = 0.8;

  async function shrink(file) {
    if (!file.type.startsWith('image/') || file.size < 300 * 1024) return file;
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
      const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(bmp.width * scale);
      canvas.height = Math.round(bmp.height * scale);
      canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(done => canvas.toBlob(done, 'image/jpeg', QUALITY));
      if (!blob || blob.size >= file.size) return file;
      return new File([blob], file.name.replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' });
    } catch (e) {
      return file;
    }
  }

  document.addEventListener('change', async (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== 'file' || input.name !== 'photos') return;
    if (input.dataset.shrunk || typeof DataTransfer === 'undefined' || !input.files.length) return;
    const buttons = input.form ? [...input.form.querySelectorAll('button')] : [];
    buttons.forEach(b => { b.dataset.wasDisabled = b.disabled ? '1' : ''; b.disabled = true; });
    try {
      const files = await Promise.all([...input.files].map(shrink));
      const picked = new DataTransfer();
      files.forEach(f => picked.items.add(f));
      input.dataset.shrunk = '1';
      input.files = picked.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));   // previews and counters update
    } finally {
      delete input.dataset.shrunk;
      buttons.forEach(b => { b.disabled = b.dataset.wasDisabled === '1'; delete b.dataset.wasDisabled; });
    }
  });
})();
