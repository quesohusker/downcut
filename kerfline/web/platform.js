'use strict';

window.platform = {
  openTextFile(accept) {
    return new Promise((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = accept;
      input.addEventListener('change', async () => {
        const file = input.files[0];
        resolve(file ? { name: file.name, text: await file.text() } : null);
      });
      input.click();
    });
  },

  openBinaryFile(accept) {
    return new Promise((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = accept;
      input.addEventListener('change', async () => {
        const file = input.files[0];
        if (!file) {
          resolve(null);
          return;
        }
        const bytes = new Uint8Array(await file.arrayBuffer());
        let text = '';
        for (let at = 0; at < bytes.length; at += 32768) {
          text += String.fromCharCode(...bytes.subarray(at, at + 32768));
        }
        resolve({ name: file.name, base64: btoa(text) });
      });
      input.click();
    });
  },

  saveTextFile(name, text) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  },

  printFrame(frame) {
    frame.contentWindow.print();
  },

  confirm(message) {
    return Promise.resolve(window.confirm(message));
  },

  readDraft() {
    try {
      return localStorage.getItem('kerfline-draft');
    } catch {
      return null;
    }
  },

  writeDraft(text) {
    try {
      localStorage.setItem('kerfline-draft', text);
    } catch {
      return;
    }
  },

  readLibrary() {
    try {
      return localStorage.getItem('kerfline-library');
    } catch {
      return null;
    }
  },

  writeLibrary(text) {
    try {
      localStorage.setItem('kerfline-library', text);
    } catch {
      return;
    }
  },
};
