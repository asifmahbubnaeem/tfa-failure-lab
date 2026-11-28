// Cart calls are synchronous on purpose: the page updates before the click returns, so a test
// that reads the badge right after clicking sees the server's answer.
function cartCall(method, slug) {
  const xhr = new XMLHttpRequest();
  xhr.open(method, "/api/cart/" + encodeURIComponent(slug), false);
  xhr.send();
  if (xhr.status !== 200) {
    console.error("cart call failed: " + method + " " + slug + " -> HTTP " + xhr.status);
    return null;
  }
  return JSON.parse(xhr.responseText);
}

function setBadge(count) {
  const link = document.querySelector(".shopping_cart_link");
  let badge = document.querySelector(".shopping_cart_badge");
  if (count > 0) {
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "shopping_cart_badge";
      badge.dataset.test = "shopping-cart-badge";
      link.appendChild(badge);
    }
    badge.textContent = String(count);
  } else if (badge) {
    badge.remove();
  }
}

function toggleCart(button) {
  const slug = button.dataset.slug;
  const inCart = button.dataset.test.startsWith("remove-");
  const result = cartCall(inCart ? "DELETE" : "POST", slug);
  if (!result) return;
  setBadge(result.count);
  button.dataset.test = (inCart ? "add-to-cart-" : "remove-") + slug;
  button.textContent = inCart ? "Add to cart" : "Remove";
}

function removeFromCartPage(button) {
  const result = cartCall("DELETE", button.dataset.slug);
  if (!result) return;
  setBadge(result.count);
  // Re-draw the list from the server's answer, so a server-side bug stays visible.
  const keep = new Set(result.items.map((i) => i.slug));
  document.querySelectorAll(".cart_item").forEach((row) => {
    const b = row.querySelector("button[data-slug]");
    if (b && !keep.has(b.dataset.slug)) row.remove();
  });
}

function sortItems(mode) {
  const list = document.querySelector(".inventory_list");
  const items = Array.from(list.querySelectorAll(".inventory_item"));
  // Read the price as shown, so the sort matches what the customer sees.
  const priceKey = (el) => el.querySelector(".inventory_item_price").textContent.trim();
  const cmp = {
    az: (a, b) => a.dataset.name.localeCompare(b.dataset.name),
    za: (a, b) => b.dataset.name.localeCompare(a.dataset.name),
    lohi: (a, b) => (priceKey(a) < priceKey(b) ? -1 : priceKey(a) > priceKey(b) ? 1 : 0),
    hilo: (a, b) => (priceKey(a) > priceKey(b) ? -1 : priceKey(a) < priceKey(b) ? 1 : 0),
  }[mode];
  items.sort(cmp).forEach((el) => list.appendChild(el));
}
