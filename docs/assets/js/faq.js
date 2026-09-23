document.addEventListener("DOMContentLoaded", () => {
  const expandAll = document.getElementById("faq-expand-all");
  const collapseAll = document.getElementById("faq-collapse-all");
  const details = document.querySelectorAll("article details");

  expandAll?.addEventListener("click", () => {
    details.forEach((d) => (d.open = true));
  });
  collapseAll?.addEventListener("click", () => {
    details.forEach((d) => (d.open = false));
  });
});
