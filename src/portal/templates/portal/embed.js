/* ballotbench gallery widget. On your page:
     <script src="https://your-portal/embed.js" data-event="event-slug" async></script>
   It puts an iframe of the event's public gallery right after the tag and
   keeps it as tall as its content. It listens only to messages from that
   iframe, from the portal's own origin. */
(function () {
  var script = document.currentScript;
  if (!script || !script.src) return;
  var origin = new URL(script.src).origin;
  var slug = script.getAttribute("data-event") || "";
  if (!/^[-a-zA-Z0-9_]+$/.test(slug)) return;
  var frame = document.createElement("iframe");
  frame.src = origin + "/embed/" + slug;
  frame.title = script.getAttribute("data-title") || "Hackathon projects";
  frame.loading = "lazy";
  frame.style.cssText = "display:block;width:100%;height:480px;border:0";
  script.parentNode.insertBefore(frame, script.nextSibling);
  window.addEventListener("message", function (e) {
    if (e.origin !== origin || e.source !== frame.contentWindow) return;
    var d = e.data;
    if (d && d.type === "ballotbench:height" && typeof d.height === "number" && d.height > 0 && d.height < 100000) {
      frame.style.height = Math.ceil(d.height) + "px";
    }
  });
})();
