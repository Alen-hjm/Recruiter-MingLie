"use strict";
let windowId;
chrome.windows.getCurrent().then((win) => {
  windowId = win.id;
});
document.getElementById("openPanel").addEventListener("click", () => {
  if (windowId === undefined) return;
  chrome.sidePanel
    .open({ windowId })
    .then(() => window.close())
    .catch(() => {
      document.getElementById("popupStatus").textContent =
        "请点击工具栏中的明猎图标打开侧边栏。";
    });
});
