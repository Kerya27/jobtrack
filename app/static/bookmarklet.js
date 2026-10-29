// "Save to JobTrack" bookmarklet. The server inlines it into a javascript: link
// (see app/services/bookmarklet.py): comment lines are dropped and lines are joined,
// so every statement must end with a semicolon.
(function () {
  var J = "__ORIGIN__";
  var url = location.href;
  var text = ((document.body && document.body.innerText) || "").slice(0, 100000);
  try {
    navigator.clipboard.writeText(text).catch(function () {});
  } catch (e) {}
  var copy = document.documentElement.cloneNode(true);
  var junk = copy.querySelectorAll('script:not([type="application/ld+json"]),style,noscript,svg,iframe,link,img,picture,video,canvas,template');
  for (var i = 0; i < junk.length; i++) { junk[i].remove(); }
  var html = "<!doctype html>" + copy.outerHTML;
  if (html.length > 2000000) { html = html.slice(0, 2000000); }
  var win = window.open(J + "/capture?url=" + encodeURIComponent(url), "jobtrack_capture");
  if (!win) { alert("JobTrack: allow pop-ups for this site and click the button again."); return; }
  var onMessage = function (e) {
    if (e.origin !== J || !e.data || e.data.type !== "jobtrack-ready") { return; }
    window.removeEventListener("message", onMessage);
    win.postMessage({ type: "jobtrack-page", url: url, html: html, text: text }, J);
  };
  window.addEventListener("message", onMessage);
})();
