/**
 * 明猎 - Background Service Worker
 */

// Chrome handles the user gesture once; avoid opening the panel twice.
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch(console.error);

chrome.runtime.onInstalled.addListener(() => {
  console.log("[明猎] 扩展已安装");
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "get_tab_id") {
    sendResponse({ tabId: sender.tab ? sender.tab.id : null });
    return true;
  }
});
